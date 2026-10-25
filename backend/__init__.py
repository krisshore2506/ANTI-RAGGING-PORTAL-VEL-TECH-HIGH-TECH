import os
import jinja2
from flask import Flask, render_template
from flask_session import Session
from sqlalchemy import inspect as sa_inspect, text as sa_text

from backend.config.settings import (
    PORTAL_ROOT,
    SECRET_KEY,
    SESSION_TYPE,
    SESSION_PERMANENT,
    SESSION_USE_SIGNER,
    SESSION_COOKIE_HTTPONLY,
    SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE,
    SQLALCHEMY_DATABASE_URI,
    SQLALCHEMY_TRACK_MODIFICATIONS,
    MAX_CONTENT_LENGTH,
    logger
)
from backend.models.base import db
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
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

    if config_overrides:
        app.config.update(config_overrides)

    # Session & Database Initialization
    Session(app)
    db.init_app(app)

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

    # Database Schema Initialization & Migration
    with app.app_context():
        db.create_all()
        try:
            inspector = sa_inspect(db.engine)
            if "complaint" in inspector.get_table_names():
                columns = [col["name"] for col in inspector.get_columns("complaint")]
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
                    "committee_assigned": "VARCHAR(100)"
                }
                with db.engine.connect() as connection:
                    for col_name, col_type in column_defs.items():
                        if col_name not in columns:
                            connection.execute(sa_text(f"ALTER TABLE complaint ADD COLUMN {col_name} {col_type}"))
                    connection.commit()

            if "notification" in inspector.get_table_names():
                notif_columns = [col["name"] for col in inspector.get_columns("notification")]
                notif_column_defs = {
                    "is_resolved": "BOOLEAN DEFAULT 0",
                    "resolved_at": "DATETIME"
                }
                with db.engine.connect() as connection:
                    for col_name, col_type in notif_column_defs.items():
                        if col_name not in notif_columns:
                            connection.execute(sa_text(f"ALTER TABLE notification ADD COLUMN {col_name} {col_type}"))

                    # Ensure dedup_key index is non-unique to allow multiple historical resolved cycles
                    try:
                        indices = connection.execute(sa_text(
                            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='notification' AND name='ix_notification_dedup_key'"
                        )).fetchall()
                        for idx_name, idx_sql in indices:
                            if idx_sql and "UNIQUE" in idx_sql.upper():
                                connection.execute(sa_text("DROP INDEX ix_notification_dedup_key"))
                                connection.execute(sa_text("CREATE INDEX ix_notification_dedup_key ON notification (dedup_key)"))
                                break
                    except Exception as idx_err:
                        logger.warning("Notification dedup index inspection note: %s", str(idx_err))

                    connection.commit()
            logger.info("Database schema initialized/verified.")
        except Exception as e:
            logger.error("Database schema migration error: %s", str(e))

        # Start Reminder Scheduler Daemon (unless testing)
        if not app.config.get("TESTING"):
            start_reminder_scheduler(app)

    return app
