"""
Migration Validation Script for Anti-Ragging Portal
Runs an Alembic upgrade on a throw-away SQLite database and validates
that all expected tables, columns, indexes, and constraints are met.

Usage:
    python scripts/validate_migration.py
"""
import os
import sys
import tempfile
import sqlite3

# Set env vars before importing any backend code
os.environ.setdefault("SKIP_SCHEMA_INIT", "true")
os.environ.setdefault("DISABLE_REMINDER_SCHEDULER", "true")

# Ensure we run from the project root
PORTAL_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(PORTAL_ROOT)
sys.path.insert(0, PORTAL_ROOT)

from alembic.config import Config as AlembicConfig
from alembic import command as alembic_command

EXPECTED_TABLES = {
    "committee_member": [
        "id", "email", "full_name", "role", "department", "phone",
        "is_active", "created_at", "updated_at"
    ],
    "complaint": [
        "id", "complaint_reference", "student_email", "complainant_name",
        "complainant_phone", "is_anonymous", "complaint_title", "complaint_text",
        "department", "assigned_cleaner", "status", "priority",
        "escalation_level", "admin_remarks", "resolution_details",
        "created_at", "updated_at", "closed_at", "timestamp",
        "incident_type", "severity_level", "incident_date_time",
        "incident_location", "accused_details", "witness_details",
        "is_confidential", "tracking_token", "committee_assigned",
        "assigned_investigator_id"
    ],
    "complaint_evidence": [
        "id", "complaint_id", "file_type", "original_filename",
        "storage_filename", "mime_type", "file_size_bytes",
        "uploader_role", "access_classification", "uploaded_at"
    ],
    "complaint_status_history": [
        "id", "complaint_id", "previous_status", "new_status",
        "action_type", "actor_role", "actor_email", "remarks", "timestamp"
    ],
    "investigation_action": [
        "id", "complaint_id", "assigned_to_id", "assigned_to_name",
        "action_type", "action_summary", "confidential_notes",
        "performed_by", "created_at"
    ],
    "notification": [
        "id", "recipient_role", "recipient_email", "complaint_id",
        "notification_type", "title", "message", "created_at", "is_read",
        "read_at", "is_resolved", "resolved_at", "dedup_key"
    ],
    "cleaner": ["id", "name", "department", "area", "phone"],
    "call_log": ["id", "complaint_id", "cleaner_name", "phone_number", "call_status", "timestamp"],
    "sms_logs": ["id", "complaint_id", "to_number", "message", "status", "timestamp"],
    "sessions": ["id", "session_id", "data", "expiry"]
}

EXPECTED_INDEXES = {
    "committee_member": ["ix_committee_member_email"],
    "complaint": [
        "ix_complaint_complaint_reference",
        "ix_complaint_created_at",
        "ix_complaint_incident_type",
        "ix_complaint_status",
        "ix_complaint_timestamp",
        "ix_complaint_tracking_token"
    ],
    "complaint_evidence": ["ix_complaint_evidence_complaint_id"],
    "complaint_status_history": [
        "ix_complaint_status_history_complaint_id",
        "ix_complaint_status_history_timestamp"
    ],
    "investigation_action": [
        "ix_investigation_action_complaint_id",
        "ix_investigation_action_created_at"
    ],
    "notification": [
        "ix_notification_complaint_id",
        "ix_notification_created_at",
        "ix_notification_dedup_key",
        "ix_notification_is_read",
        "ix_notification_is_resolved",
        "ix_notification_recipient_email",
        "ix_notification_recipient_role",
        "ix_notification_resolved_at"
    ],
}

PASS = "[PASS]"
FAIL = "[FAIL]"


def run_validation():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_db = f.name

    db_url = f"sqlite:///{tmp_db}"
    os.environ["DATABASE_URL"] = db_url

    try:
        print(f"\n>>> Anti-Ragging Portal Reconstructed Migration Validator")
        print(f">>> Test database: {tmp_db}")

        alembic_cfg = AlembicConfig(os.path.join(PORTAL_ROOT, "migrations", "alembic.ini"))
        alembic_cfg.set_main_option("script_location", os.path.join(PORTAL_ROOT, "migrations"))

        from backend import create_app
        app = create_app({"SQLALCHEMY_DATABASE_URI": db_url, "TESTING": True, "DISABLE_REMINDER_SCHEDULER": True})

        with app.app_context():
            print("\n[1] Running alembic upgrade head ...")
            try:
                alembic_command.upgrade(alembic_cfg, "head")
                print(f"    {PASS} Alembic upgrade head completed without error.")
            except Exception as e:
                print(f"    {FAIL} Alembic upgrade head failed: {e}")
                return False

        conn = sqlite3.connect(tmp_db)
        cursor = conn.cursor()

        all_passed = True

        print("\n[2] Validating tables and columns ...")
        for table, expected_cols in EXPECTED_TABLES.items():
            cursor.execute(f"PRAGMA table_info('{table}')")
            actual_cols = [row[1] for row in cursor.fetchall()]

            if not actual_cols:
                print(f"    {FAIL} Table '{table}' was not created!")
                all_passed = False
                continue

            missing = set(expected_cols) - set(actual_cols)
            if missing:
                print(f"    {FAIL} Table '{table}' is missing columns: {missing}")
                all_passed = False
            else:
                print(f"    {PASS} Table '{table}': all {len(expected_cols)} expected columns present.")

        print("\n[3] Validating indexes ...")
        for table, expected_idxs in EXPECTED_INDEXES.items():
            cursor.execute(f"PRAGMA index_list('{table}')")
            actual_idxs = [row[1] for row in cursor.fetchall()]

            for idx in expected_idxs:
                if idx in actual_idxs:
                    print(f"    {PASS} Index '{idx}' on '{table}'.")
                else:
                    print(f"    {FAIL} Missing index '{idx}' on '{table}' (found: {actual_idxs})")
                    all_passed = False

        print("\n[4] Validating foreign key integrity ...")
        cursor.execute("PRAGMA foreign_key_list('complaint_evidence')")
        fks = cursor.fetchall()
        has_complaint_fk = any(row[2] == "complaint" and row[3] == "complaint_id" and row[4] == "id" for row in fks)
        if has_complaint_fk:
            print(f"    {PASS} Foreign key complaint_evidence.complaint_id -> complaint.id present.")
        else:
            print(f"    {FAIL} Missing foreign key on complaint_evidence.complaint_id")
            all_passed = False

        print("\n[5] Validating unique constraint on complaint_evidence.storage_filename ...")
        cursor.execute("PRAGMA index_list('complaint_evidence')")
        indices = cursor.fetchall()
        has_unique = False
        for idx in indices:
            if idx[2] == 1:
                cursor.execute(f"PRAGMA index_info('{idx[1]}')")
                cols = [r[2] for r in cursor.fetchall()]
                if cols == ["storage_filename"]:
                    has_unique = True
                    break
        if has_unique:
            print(f"    {PASS} Unique constraint on complaint_evidence.storage_filename found.")
        else:
            print(f"    {FAIL} Missing unique constraint on complaint_evidence.storage_filename")
            all_passed = False

        conn.close()

        if all_passed:
            print(f"\n>>> All validation checks {PASS}. Migration is ready for Supabase deployment.\n")
        else:
            print(f"\n>>> Some validation checks {FAIL}. Please review the output above.\n")

        return all_passed

    finally:
        if os.path.exists(tmp_db):
            try:
                os.remove(tmp_db)
            except Exception:
                pass


if __name__ == "__main__":
    success = run_validation()
    sys.exit(0 if success else 1)
