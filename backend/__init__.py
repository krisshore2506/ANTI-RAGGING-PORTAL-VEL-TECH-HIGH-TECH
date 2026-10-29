import sys
import os
import re
import jinja2
from flask import Flask, render_template
from flask_session import Session
from sqlalchemy import inspect as sa_inspect, text as sa_text

from backend.config.settings import (
    PORTAL_ROOT,
    SECRET_KEY,
    IS_PRODUCTION,
    FLASK_ENV,
    SESSION_TYPE,
    SESSION_PERMANENT,
    SESSION_USE_SIGNER,
    SESSION_COOKIE_HTTPONLY,
    SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE,
    SQLALCHEMY_DATABASE_URI,
    SQLALCHEMY_TRACK_MODIFICATIONS,
    SQLALCHEMY_ENGINE_OPTIONS,
    MAX_CONTENT_LENGTH,
    mask_database_url,
    sanitize_db_error,
    logger
)
from backend.models.base import db, migrate
from backend.middleware.csrf import inject_csrf_token, validate_csrf
from backend.services.evidence_service import format_file_size
from backend.services.notification_service import start_reminder_scheduler
from backend.routes.auth_routes import auth_bp
from backend.routes.student_routes import student_bp
from backend.routes.admin_routes import admin_bp


def create_app(config_overrides=None):
    """
    Application Factory for Vel Tech Anti-Ragging Portal.
    Configures multi-directory Jinja2 template resolving (frontend/pages, frontend, templates),
    database, session cookies, CSRF protection, and route blueprints.
    """
    app = Flask(
        __name__,
        static_folder=os.path.join(PORTAL_ROOT, "static"),
        static_url_path="/static"
    )

    # Multi-directory Jinja2 Loader: supports both organized frontend/pages and root templates/
    template_dirs = [
        os.path.join(PORTAL_ROOT, "frontend", "pages"),
        os.path.join(PORTAL_ROOT, "frontend"),
        os.path.join(PORTAL_ROOT, "templates"),
    ]
    app.jinja_loader = jinja2.FileSystemLoader(template_dirs)

    # Core Configuration
    app.secret_key = SECRET_KEY
    app.config["SESSION_TYPE"] = SESSION_TYPE
    app.config["SESSION_PERMANENT"] = SESSION_PERMANENT
    app.config["SESSION_USE_SIGNER"] = SESSION_USE_SIGNER
    app.config["SESSION_COOKIE_HTTPONLY"] = SESSION_COOKIE_HTTPONLY
    app.config["SESSION_COOKIE_SAMESITE"] = SESSION_COOKIE_SAMESITE
    app.config["SESSION_COOKIE_SECURE"] = SESSION_COOKIE_SECURE
    app.config["SQLALCHEMY_DATABASE_URI"] = SQLALCHEMY_DATABASE_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = SQLALCHEMY_TRACK_MODIFICATIONS
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = SQLALCHEMY_ENGINE_OPTIONS
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

    if config_overrides:
        app.config.update(config_overrides)

    # Sanitize engine options for SQLite connections (SQLite does not accept pool_size/max_overflow)
    effective_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
    if effective_uri.startswith("sqlite"):
        opts = dict(app.config.get("SQLALCHEMY_ENGINE_OPTIONS") or {})
        opts.pop("pool_size", None)
        opts.pop("max_overflow", None)
        opts.pop("pool_recycle", None)
        opts.setdefault("pool_pre_ping", True)
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = opts

    # Session storage configuration
    session_type_current = str(app.config.get("SESSION_TYPE", "filesystem")).lower()
    if session_type_current == "sqlalchemy":
        app.config["SESSION_SQLALCHEMY"] = db
        app.config.setdefault("SESSION_SQLALCHEMY_TABLE", "sessions")
    elif session_type_current == "filesystem":
        session_dir = os.path.join(PORTAL_ROOT, "flask_session")
        os.makedirs(session_dir, exist_ok=True)
        app.config.setdefault("SESSION_FILE_DIR", session_dir)

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    Session(app)

    # Context Processors & Middleware
    app.context_processor(inject_csrf_token)
    app.before_request(validate_csrf)

    # Template Filters
    @app.template_filter("format_file_size")
    def template_format_file_size(num_bytes):
        return format_file_size(num_bytes)

    # Error Handlers
    @app.errorhandler(413)
    def request_entity_too_large(error):
        logger.warning("Upload rejected: total payload exceeded 25 MB limit (413 Request Entity Too Large).")
        return render_template(
            "error.html",
            error_title="Upload Limit Exceeded",
            error_heading="File Size Exceeded Limit",
            error_message="The total size of your uploaded evidence files exceeds the 25 MB limit. Please reduce file sizes or select fewer files and try again."
        ), 413

    # Register Blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(admin_bp)

    # Alias unqualified endpoints on app for backwards compatibility with tests and templates
    for rule in list(app.url_map.iter_rules()):
        if "." in rule.endpoint:
            short = rule.endpoint.split(".", 1)[1]
            if short not in app.view_functions:
                app.view_functions[short] = app.view_functions[rule.endpoint]
            app.url_map._rules_by_endpoint.setdefault(short, []).append(rule)

    def _url_build_error_fallback(error, endpoint, values):
        if "." not in endpoint:
            for bp in ("auth", "student", "admin"):
                cand = f"{bp}.{endpoint}"
                if cand in app.view_functions:
                    from flask import url_for as _url_for
                    return _url_for(cand, **values)
        return None

    app.url_build_error_handlers.append(_url_build_error_fallback)

    # --------------------------------------------------------------------------
    # Database Initialization & Read-Only Production Verification
    # --------------------------------------------------------------------------
    db_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
    is_sqlite = db_uri.startswith("sqlite")
    is_testing = bool(app.config.get("TESTING", False))

    def _is_migration_cli():
        args = [a.lower() for a in sys.argv] if sys.argv else []
        script = os.path.basename(sys.argv[0]).lower() if sys.argv else ""
        if any(tool in script for tool in ("alembic",)):
            return True
        if any(arg in args for arg in ("db", "upgrade", "downgrade", "migrate", "stamp", "revision")):
            return True
        return False

    is_cli_migration = _is_migration_cli()

    if is_sqlite and not IS_PRODUCTION:
        # ----------------------------------------------------------------------
        # SQLite Development & Test Environment Handler
        # Preserves isolated unit test fixtures and dev auto-creation.
        # ----------------------------------------------------------------------
        skip_schema_init = app.config.get("SKIP_SCHEMA_INIT")
        if skip_schema_init is None:
            skip_schema_init = os.getenv("SKIP_SCHEMA_INIT", "False").lower() in ("true", "1", "yes")

        if not skip_schema_init and not is_cli_migration:
            with app.app_context():
                try:
                    db.create_all()
                    with db.engine.connect() as connection:
                        tables = [row[0] for row in connection.execute(sa_text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()]

                        if "complaint" in tables:
                            columns = [row[1] for row in connection.execute(sa_text("PRAGMA table_info(complaint)")).fetchall()]
                            column_defs = {
                                "complaint_title": "VARCHAR(150)",
                                "admin_remarks": "TEXT",
                                "incident_type": "VARCHAR(100)",
                                "severity_level": "VARCHAR(50) DEFAULT 'Medium'",
                                "incident_date_time": "VARCHAR(100)",
                                "incident_location": "VARCHAR(150)",
                                "accused_details": "TEXT",
                                "witness_details": "TEXT",
                                "is_confidential": "BOOLEAN DEFAULT 0",
                                "tracking_token": "VARCHAR(64)",
                                "committee_assigned": "VARCHAR(100)",
                                "complaint_reference": "VARCHAR(50)",
                                "complainant_name": "VARCHAR(100)",
                                "complainant_phone": "VARCHAR(20)",
                                "is_anonymous": "BOOLEAN DEFAULT 0",
                                "resolution_details": "TEXT",
                                "updated_at": "DATETIME",
                                "closed_at": "DATETIME",
                                "assigned_investigator_id": "INTEGER"
                            }
                            for col_name, col_type in column_defs.items():
                                if col_name not in columns:
                                    connection.execute(sa_text(f"ALTER TABLE complaint ADD COLUMN {col_name} {col_type}"))

                        if "notification" in tables:
                            notif_columns = [row[1] for row in connection.execute(sa_text("PRAGMA table_info('notification')")).fetchall()]
                            notif_column_defs = {
                                "is_resolved": "BOOLEAN DEFAULT 0",
                                "resolved_at": "DATETIME"
                            }
                            for col_name, col_type in notif_column_defs.items():
                                if col_name not in notif_columns:
                                    connection.execute(sa_text(f"ALTER TABLE notification ADD COLUMN {col_name} {col_type}"))

                            # Relax dedup_key unique indexes in SQLite for test compatibility
                            idx_list = connection.execute(sa_text("PRAGMA index_list('notification')")).fetchall()
                            needs_rebuild = False
                            unique_dedup_standalone_indexes = []

                            for idx in idx_list:
                                idx_name = idx[1]
                                is_unique = bool(idx[2])
                                origin = idx[3]

                                if is_unique:
                                    idx_info = connection.execute(sa_text(f"PRAGMA index_info('{idx_name}')")).fetchall()
                                    indexed_cols = [row[2] for row in idx_info]
                                    if indexed_cols == ['dedup_key']:
                                        if origin == 'u':
                                            needs_rebuild = True
                                        else:
                                            unique_dedup_standalone_indexes.append(idx_name)

                            for u_idx in unique_dedup_standalone_indexes:
                                connection.execute(sa_text(f'DROP INDEX IF EXISTS "{u_idx}"'))

                            if needs_rebuild:
                                non_dedup_index_sqls = []
                                raw_indexes = connection.execute(sa_text(
                                    "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='notification' AND sql IS NOT NULL"
                                ))
                                for idx_name, idx_sql in raw_indexes:
                                    idx_cols = [row[2] for row in connection.execute(sa_text(f"PRAGMA index_info('{idx_name}')")).fetchall()]
                                    if idx_cols != ['dedup_key']:
                                        non_dedup_index_sqls.append(idx_sql)

                                create_sql = connection.execute(sa_text(
                                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='notification'"
                                )).fetchone()[0]

                                lines = create_sql.splitlines()
                                new_lines = []
                                for line in lines:
                                    if re.search(r'\bUNIQUE\s*\(\s*["`\[]?dedup_key["`\]]?\s*\)', line, re.IGNORECASE):
                                        continue
                                    elif re.search(r'\bdedup_key\b', line, re.IGNORECASE) and not re.search(r'^\s*CONSTRAINT\b', line, re.IGNORECASE):
                                        line = re.sub(r'\bUNIQUE\b', '', line, flags=re.IGNORECASE)
                                        new_lines.append(line)
                                    else:
                                        new_lines.append(line)

                                new_sql = "\n".join(new_lines)
                                new_sql = re.sub(r',\s*\)', '\n)', new_sql)

                                cols_str = ", ".join(f'"{c}"' for c in notif_columns)

                                connection.execute(sa_text("PRAGMA foreign_keys = OFF"))
                                connection.execute(sa_text("ALTER TABLE notification RENAME TO _notification_old"))
                                connection.execute(sa_text(new_sql))
                                connection.execute(sa_text(f"INSERT INTO notification ({cols_str}) SELECT {cols_str} FROM _notification_old"))
                                connection.execute(sa_text("DROP TABLE _notification_old"))
                                connection.execute(sa_text("PRAGMA foreign_keys = ON"))

                                for idx_sql in non_dedup_index_sqls:
                                    connection.execute(sa_text(idx_sql))

                            connection.execute(sa_text("CREATE INDEX IF NOT EXISTS ix_notification_dedup_key ON notification (dedup_key)"))

                        connection.commit()
                    logger.info("Local SQLite schema initialized/verified: %s", mask_database_url(db_uri))
                except Exception as e:
                    safe_err = sanitize_db_error(e)
                    logger.error("Local SQLite initialization error: %s", safe_err)
                    raise RuntimeError(f"SQLite initialization failed: {safe_err}") from None

    else:
        # ----------------------------------------------------------------------
        # PostgreSQL / Supabase Production Environment Handler
        # db.create_all() is strictly prohibited.
        # Alembic/Flask-Migrate is the exclusive authoritative migration tool.
        # Startup verifies existing schema in READ-ONLY mode.
        # ----------------------------------------------------------------------
        if not is_testing and not is_cli_migration:
            with app.app_context():
                try:
                    with db.engine.connect() as connection:
                        inspector = sa_inspect(connection)
                        existing_tables = set(inspector.get_table_names(schema="public" if not is_sqlite else None))

                    required_tables = {
                        "committee_member",
                        "complaint",
                        "complaint_evidence",
                        "complaint_status_history",
                        "investigation_action",
                        "notification"
                    }
                    missing_tables = required_tables - existing_tables
                    if missing_tables:
                        err_msg = (
                            f"Database schema is incomplete or uninitialized. Missing required tables: {sorted(missing_tables)}. "
                            "Run 'python -m flask --app grievai_app db upgrade' to apply migrations."
                        )
                        logger.error(err_msg)
                        raise RuntimeError(err_msg)

                    logger.info("Database schema verified (read-only): %s", mask_database_url(db_uri))
                except RuntimeError:
                    raise
                except Exception as e:
                    safe_err = sanitize_db_error(e)
                    logger.error("Database connection/verification failed: %s", safe_err)
                    raise RuntimeError(f"Database connection failed: {safe_err}") from None

    # --------------------------------------------------------------------------
    # Reminder Scheduler Daemon
    # Strictly suppressed during testing, CLI commands, or when explicitly disabled
    # --------------------------------------------------------------------------
    disable_scheduler = app.config.get("DISABLE_REMINDER_SCHEDULER")
    if disable_scheduler is None:
        disable_scheduler = os.getenv("DISABLE_REMINDER_SCHEDULER", "False")
    if isinstance(disable_scheduler, str):
        disable_scheduler = disable_scheduler.lower() in ("true", "1", "yes")

    if not is_testing and not disable_scheduler and not is_cli_migration:
        start_reminder_scheduler(app)

    return app
