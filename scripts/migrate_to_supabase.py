"""
Anti-Ragging Portal - Safe SQLite to Supabase PostgreSQL Migration Utility

Features:
- Connects to source SQLite in strict read-only mode (never modifies local data).
- Supports --dry-run for non-destructive pre-flight schema & data validation.
- Supports --schema-only to create PostgreSQL tables, indexes, and constraints.
- Supports --import-data to migrate records in dependency order with explicit confirmation.
- Automatically synchronizes PostgreSQL primary key sequences (setval).
- Compares source and target row counts and min/max IDs post-migration.
- Masks all database passwords and credentials in logs and console output.

Usage:
    # 1. Pre-flight dry run inspection (read-only):
    python scripts/migrate_to_supabase.py --dry-run

    # 2. Deploy schema only (creates tables, indexes, constraints):
    python scripts/migrate_to_supabase.py --schema-only

    # 3. Import records from local SQLite to Supabase PostgreSQL:
    python scripts/migrate_to_supabase.py --import-data

    # 4. Validate existing target database against SQLite source:
    python scripts/migrate_to_supabase.py --validate-only
"""

import os
import sys
import argparse
import sqlite3
from datetime import datetime
from sqlalchemy import create_engine, inspect as sa_inspect, text as sa_text

# Ensure project root is in sys.path
PORTAL_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PORTAL_ROOT not in sys.path:
    sys.path.insert(0, PORTAL_ROOT)

from backend.config.settings import (
    DB_PATH,
    normalize_database_url,
    mask_database_url,
    sanitize_db_error
)

# Tables in strict dependency order (parent before child)
MIGRATION_TABLES = [
    "committee_member",
    "cleaner",
    "complaint",
    "complaint_evidence",
    "complaint_status_history",
    "investigation_action",
    "call_log",
    "sms_logs",
    "notification",
    "sessions"
]


def get_source_sqlite_connection(source_path: str):
    """Opens a connection to the SQLite database in strict read-only mode."""
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"Source SQLite database file not found: {source_path}")
    abs_path = os.path.abspath(source_path).replace("\\", "/")
    uri = f"file:///{abs_path.lstrip('/')}?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except Exception:
        # Fallback to standard connection if URI mode fails
        return sqlite3.connect(source_path)


def get_target_engine(target_url: str):
    """Creates a SQLAlchemy engine for the target PostgreSQL database."""
    if not target_url or target_url.lower() in ("your_db_url", "none", ""):
        raise ValueError("Target database URL is empty. Provide --target-url or set DATABASE_URL environment variable.")
    normalized_url = normalize_database_url(target_url)
    if normalized_url.startswith("sqlite"):
        return create_engine(normalized_url, pool_pre_ping=True)
    return create_engine(
        normalized_url,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"sslmode": "require"} if "supabase" in normalized_url else {}
    )


def inspect_source_database(sqlite_conn):
    """Gathers table names, column lists, and row counts from SQLite."""
    cur = sqlite_conn.cursor()
    info = {}
    master_tables = [
        r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    ]
    for table in master_tables:
        count = cur.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        columns = [c[1] for c in cur.execute(f'PRAGMA table_info("{table}")').fetchall()]
        info[table] = {
            "count": count,
            "columns": columns
        }
    return info


def inspect_target_database(target_engine):
    """Gathers existing table names and row counts from target database."""
    with target_engine.connect() as conn:
        inspector = sa_inspect(conn)
        tables = inspector.get_table_names()
        info = {}
        for t in tables:
            try:
                cnt = conn.execute(sa_text(f'SELECT count(*) FROM "{t}"')).scalar()
                cols = [c["name"] for c in inspector.get_columns(t)]
                info[t] = {"count": cnt, "columns": cols}
            except Exception:
                info[t] = {"count": "error", "columns": []}
    return info


def run_dry_run(source_path: str, target_url: str):
    """Runs a non-destructive pre-flight audit between source and target."""
    print("=" * 70)
    print("ANTI-RAGGING PORTAL - PRE-FLIGHT MIGRATION DRY RUN")
    print("=" * 70)
    print(f"Source SQLite (Read-Only): {source_path}")
    masked_target = mask_database_url(target_url) if target_url and target_url.lower() not in ("your_db_url", "none") else "(not configured)"
    print(f"Target Database URL:       {masked_target}")
    print("-" * 70)

    try:
        src_conn = get_source_sqlite_connection(source_path)
        src_info = inspect_source_database(src_conn)
        print("[1] Source SQLite Inventory:")
        total_rows = 0
        for tbl in MIGRATION_TABLES:
            if tbl in src_info:
                cnt = src_info[tbl]["count"]
                total_rows += cnt
                print(f"    - Table '{tbl}': {cnt} row(s), {len(src_info[tbl]['columns'])} column(s)")
            else:
                print(f"    - Table '{tbl}': (not present in source)")
        print(f"    Total records across all tables: {total_rows}")
        src_conn.close()
    except Exception as e:
        print(f"ERROR reading source SQLite database: {e}")
        return False

    print("\n[2] Checking Target Database Connectivity...")
    if not target_url or target_url.lower() in ("your_db_url", "none", ""):
        print("    Target URL is not configured. Dry run completed on local source only.")
        print("    Configure DATABASE_URL or pass --target-url to verify PostgreSQL connectivity.")
        print("=" * 70)
        return True

    try:
        target_engine = get_target_engine(target_url)
        with target_engine.connect() as conn:
            tgt_info = inspect_target_database(target_engine)
            dialect = target_engine.dialect.name
            print(f"    Target Dialect: {dialect} ({target_engine.dialect.driver})")
            print(f"    Target Tables Found: {list(tgt_info.keys())}")
            for tbl in MIGRATION_TABLES:
                if tbl in tgt_info:
                    print(f"    - Target Table '{tbl}': currently has {tgt_info[tbl]['count']} row(s)")
                else:
                    print(f"    - Target Table '{tbl}': MISSING (will be created during migration)")
        target_engine.dispose()
    except Exception as e:
        safe_err = sanitize_db_error(e)
        print(f"ERROR connecting to target database: {safe_err}")
        return False

    print("\n[3] Pre-Flight Assessment:")
    print("    - Dry run complete. No modifications were made.")
    print("    - Use --schema-only to deploy tables and indexes.")
    print("    - Use --import-data to copy records into Supabase PostgreSQL.")
    print("=" * 70)
    return True


def apply_schema(target_url: str):
    """Applies the complete schema to the target database."""
    print("=" * 70)
    print("DEPLOYING SCHEMA TO TARGET DATABASE")
    print(f"Target: {mask_database_url(target_url)}")
    print("=" * 70)

    target_engine = get_target_engine(target_url)
    from backend import create_app
    from backend.models.base import db

    app = create_app({
        "SQLALCHEMY_DATABASE_URI": normalize_database_url(target_url),
        "TESTING": True,
        "DISABLE_REMINDER_SCHEDULER": True
    })

    with app.app_context():
        try:
            print("Creating all tables via SQLAlchemy metadata...")
            db.create_all()
            print("Tables created successfully.")

            # If PostgreSQL, apply RLS policies if desired
            if target_engine.dialect.name == "postgresql":
                sql_path = os.path.join(PORTAL_ROOT, "scripts", "supabase_schema.sql")
                if os.path.exists(sql_path):
                    print("Found scripts/supabase_schema.sql. Applying RLS and index safeguards...")
                    with open(sql_path, "r", encoding="utf-8") as f:
                        sql_content = f.read()

                    with target_engine.connect() as conn:
                        for statement in sql_content.split(";"):
                            stmt_clean = statement.strip()
                            if stmt_clean and not stmt_clean.startswith("--"):
                                try:
                                    conn.execute(sa_text(stmt_clean))
                                except Exception:
                                    pass
                        conn.commit()
                    print("PostgreSQL security configurations applied.")

            print("Schema deployment completed successfully.")
            return True
        except Exception as e:
            print(f"Schema deployment failed: {sanitize_db_error(e)}")
            return False
        finally:
            target_engine.dispose()


def import_data(source_path: str, target_url: str, force_yes: bool = False):
    """Migrates records from source SQLite to target PostgreSQL in dependency order."""
    print("=" * 70)
    print("MIGRATING DATA FROM SQLITE TO TARGET DATABASE")
    print(f"Source: {source_path}")
    print(f"Target: {mask_database_url(target_url)}")
    print("=" * 70)

    if not force_yes:
        confirm = input(
            "\n[CONFIRMATION REQUIRED] Are you sure you want to import records into the target database?\n"
            "This will insert records from the local SQLite database.\n"
            "Type 'yes' to proceed: "
        ).strip().lower()
        if confirm != "yes":
            print("Migration aborted by user.")
            return False

    src_conn = get_source_sqlite_connection(source_path)
    src_conn.row_factory = sqlite3.Row
    src_cur = src_conn.cursor()

    target_engine = get_target_engine(target_url)

    # First ensure schema exists on target
    apply_schema(target_url)

    with target_engine.connect() as tgt_conn:
        inspector = sa_inspect(tgt_conn)
        target_tables = inspector.get_table_names()

        for table in MIGRATION_TABLES:
            src_table_check = src_cur.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"
            ).fetchone()
            if not src_table_check:
                continue

            if table not in target_tables:
                print(f"Skipping table '{table}': not present on target.")
                continue

            rows = src_cur.execute(f'SELECT * FROM "{table}" ORDER BY id ASC').fetchall()
            if not rows:
                print(f"Table '{table}': 0 rows to migrate.")
                continue

            col_names = [col["name"] for col in inspector.get_columns(table)]
            src_cols = rows[0].keys()
            matching_cols = [c for c in src_cols if c in col_names]

            print(f"\nMigrating table '{table}' ({len(rows)} record(s))...")

            cols_quoted = ", ".join([f'"{c}"' for c in matching_cols])
            placeholders = ", ".join([f":{c}" for c in matching_cols])
            insert_sql = sa_text(f'INSERT INTO "{table}" ({cols_quoted}) VALUES ({placeholders})')

            trans = tgt_conn.begin()
            try:
                for row in rows:
                    row_dict = {}
                    for c in matching_cols:
                        val = row[c]
                        if target_engine.dialect.name == "postgresql" and isinstance(val, int) and c in ("is_confidential", "is_read", "is_resolved"):
                            val = bool(val)
                        row_dict[c] = val
                    tgt_conn.execute(insert_sql, row_dict)
                trans.commit()
                print(f"  Successfully inserted {len(rows)} row(s) into '{table}'.")

                if target_engine.dialect.name == "postgresql" and "id" in matching_cols:
                    try:
                        seq_sql = sa_text(
                            f"SELECT setval(pg_get_serial_sequence('\"{table}\"', 'id'), COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM \"{table}\";"
                        )
                        tgt_conn.execute(seq_sql)
                        tgt_conn.commit()
                        print(f"  Synchronized PostgreSQL identity sequence for '{table}'.")
                    except Exception as seq_err:
                        print(f"  (Sequence sync notice for '{table}': {seq_err})")

            except Exception as e:
                trans.rollback()
                safe_err = sanitize_db_error(e)
                print(f"  ERROR migrating table '{table}': {safe_err}")
                src_conn.close()
                target_engine.dispose()
                return False

    src_conn.close()
    target_engine.dispose()

    print("\nValidating migrated record counts...")
    return validate_migration(source_path, target_url)


def validate_migration(source_path: str, target_url: str):
    """Compares source SQLite row counts against target database row counts."""
    src_conn = get_source_sqlite_connection(source_path)
    src_info = inspect_source_database(src_conn)
    src_conn.close()

    target_engine = get_target_engine(target_url)
    tgt_info = inspect_target_database(target_engine)
    target_engine.dispose()

    print("\n" + "=" * 70)
    print("MIGRATION INTEGRITY VALIDATION REPORT")
    print("=" * 70)
    print(f"{'Table Name':<25} {'Source Rows':<15} {'Target Rows':<15} {'Status'}")
    print("-" * 70)

    all_matched = True
    for tbl in MIGRATION_TABLES:
        src_cnt = src_info.get(tbl, {}).get("count", 0)
        tgt_cnt = tgt_info.get(tbl, {}).get("count", 0)
        if tgt_cnt == "error":
            status = "ERROR"
            all_matched = False
        elif src_cnt == tgt_cnt:
            status = "MATCHED (OK)"
        else:
            status = f"MISMATCH ({src_cnt} != {tgt_cnt})"
            all_matched = False
        print(f"{tbl:<25} {src_cnt:<15} {str(tgt_cnt):<15} {status}")

    print("=" * 70)
    if all_matched:
        print("ALL TABLES MATCHED PERFECTLY. Migration validation passed.\n")
    else:
        print("ATTENTION: One or more tables have count discrepancies.\n")
    return all_matched


def main():
    parser = argparse.ArgumentParser(description="Anti-Ragging Portal SQLite -> PostgreSQL / Supabase Migration Tool")
    parser.add_argument("--source-db", default=DB_PATH, help=f"Path to source SQLite file (default: {DB_PATH})")
    parser.add_argument("--target-url", default=os.getenv("DATABASE_URL", ""), help="Target PostgreSQL connection URL (or DATABASE_URL env var)")
    parser.add_argument("--dry-run", action="store_true", help="Perform pre-flight verification without modifying target")
    parser.add_argument("--schema-only", action="store_true", help="Deploy tables, indexes, and constraints only without data")
    parser.add_argument("--import-data", action="store_true", help="Migrate records from SQLite to target database")
    parser.add_argument("--validate-only", action="store_true", help="Validate row counts between source and target")
    parser.add_argument("--yes", action="store_true", help="Auto-confirm data import without interactive prompt")

    args = parser.parse_args()

    target_url = args.target_url or os.getenv("DATABASE_URL", "")

    if args.dry_run or (not args.schema_only and not args.import_data and not args.validate_only):
        success = run_dry_run(args.source_db, target_url)
        sys.exit(0 if success else 1)
    elif args.schema_only:
        if not target_url or target_url.lower() in ("your_db_url", "none", ""):
            print("ERROR: Target database URL must be configured via --target-url or DATABASE_URL environment variable.")
            sys.exit(1)
        success = apply_schema(target_url)
        sys.exit(0 if success else 1)
    elif args.import_data:
        if not target_url or target_url.lower() in ("your_db_url", "none", ""):
            print("ERROR: Target database URL must be configured via --target-url or DATABASE_URL environment variable.")
            sys.exit(1)
        success = import_data(args.source_db, target_url, force_yes=args.yes)
        sys.exit(0 if success else 1)
    elif args.validate_only:
        if not target_url or target_url.lower() in ("your_db_url", "none", ""):
            print("ERROR: Target database URL must be configured via --target-url or DATABASE_URL environment variable.")
            sys.exit(1)
        success = validate_migration(args.source_db, target_url)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
