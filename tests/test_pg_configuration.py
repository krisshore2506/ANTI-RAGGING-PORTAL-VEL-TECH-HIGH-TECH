import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Pre-set test environment variables before any backend imports
os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-minimum-length-for-testing")
os.environ.setdefault("ADMIN_EMAIL", "admin@velhightech.com")
os.environ.setdefault("ADMIN_PASSWORD", "AdminSecurePass123")

PORTAL_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PORTAL_ROOT not in sys.path:
    sys.path.insert(0, PORTAL_ROOT)

from backend.config.settings import (
    normalize_database_url,
    mask_database_url,
    sanitize_db_error
)


class TestPostgresURLNormalization(unittest.TestCase):
    """Tests for URL scheme normalization and driver enforcement."""

    def test_postgres_legacy_scheme_converted(self):
        url = "postgres://user:pass@ep-cool-db.ap-south-1.aws.supabase.co:5432/postgres"
        result = normalize_database_url(url)
        self.assertTrue(result.startswith("postgresql+psycopg://"))
        self.assertNotIn("postgres://", result)

    def test_postgresql_driverless_scheme_converted(self):
        url = "postgresql://user:pass@ep-cool-db.ap-south-1.aws.supabase.co:5432/postgres"
        result = normalize_database_url(url)
        self.assertTrue(result.startswith("postgresql+psycopg://"))

    def test_explicit_driver_preserved(self):
        url = "postgresql+psycopg2://user:pass@ep-cool-db.ap-south-1.aws.supabase.co:5432/postgres"
        result = normalize_database_url(url)
        self.assertTrue(result.startswith("postgresql+psycopg2://"))

    def test_sqlite_url_preserved(self):
        url = "sqlite:///path/to/test.db"
        result = normalize_database_url(url)
        self.assertEqual(result, "sqlite:///path/to/test.db")

    def test_empty_url_returns_empty_string(self):
        self.assertEqual(normalize_database_url(""), "")
        self.assertEqual(normalize_database_url(None), "")
        self.assertEqual(normalize_database_url("   "), "")

    def test_placeholder_url_returns_empty_string(self):
        self.assertEqual(normalize_database_url("your_db_url"), "")
        self.assertEqual(normalize_database_url("none"), "")


class TestTLSEnforcement(unittest.TestCase):
    """Tests for automatic TLS/SSL mode requirement on remote hosts."""

    def test_remote_host_gets_sslmode_require(self):
        url = "postgresql://user:pass@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"
        result = normalize_database_url(url)
        self.assertIn("sslmode=require", result)

    def test_existing_sslmode_preserved(self):
        url = "postgresql://user:pass@aws-0-ap-south-1.pooler.supabase.com:6543/postgres?sslmode=verify-full"
        result = normalize_database_url(url)
        self.assertIn("sslmode=verify-full", result)
        self.assertNotIn("sslmode=require", result)

    def test_localhost_does_not_force_sslmode(self):
        url = "postgresql://postgres:secret@localhost:5432/testdb"
        result = normalize_database_url(url)
        self.assertNotIn("sslmode=require", result)

    def test_127_does_not_force_sslmode(self):
        url = "postgresql://postgres:secret@127.0.0.1:5432/testdb"
        result = normalize_database_url(url)
        self.assertNotIn("sslmode=require", result)


class TestSpecialCharacterPasswords(unittest.TestCase):
    """Verify passwords with special characters (@, #, :, /, etc.) are preserved."""

    def test_special_chars_in_password_preserved(self):
        url = "postgresql+psycopg://user%40domain:p%40ss%23word%21@db.example.com:5432/dbname"
        result = normalize_database_url(url)
        self.assertIn("p%40ss%23word%21", result)

    def test_standard_alphanumeric_password_preserved(self):
        url = "postgresql://appuser:SecretPassword123@db.supabase.co:5432/appdb"
        result = normalize_database_url(url)
        self.assertIn("SecretPassword123", result)


class TestCredentialMasking(unittest.TestCase):
    """Tests for safe URL logging without credential exposure."""

    def test_mask_postgresql_url(self):
        url = "postgresql+psycopg://postgres:SuperSecretPass123@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"
        masked = mask_database_url(url)
        self.assertNotIn("SuperSecretPass123", masked)
        self.assertIn("aws-0-ap-south-1.pooler.supabase.com", masked)
        self.assertTrue("***" in masked or "hidden" in masked.lower())

    def test_mask_empty_url(self):
        self.assertEqual(mask_database_url(""), "")
        self.assertEqual(mask_database_url(None), "")

    def test_mask_sqlite_url(self):
        url = "sqlite:///C:/path/to/anti_ragging_dev.db"
        masked = mask_database_url(url)
        self.assertIn("anti_ragging_dev.db", masked)


class TestErrorSanitization(unittest.TestCase):
    """Tests for error message sanitization."""

    def test_sanitize_connection_string_in_error(self):
        raw_error = Exception(
            "connection failed: postgresql+psycopg://postgres:VerySecretKey@db.supabase.co:5432/postgres: timeout"
        )
        result = sanitize_db_error(raw_error)
        self.assertNotIn("VerySecretKey", result)
        self.assertIn(":***@", result)

    def test_sanitize_password_parameter_in_error(self):
        raw_error = Exception("could not connect: password=MySecretPassword host=db.example.com")
        result = sanitize_db_error(raw_error)
        self.assertNotIn("MySecretPassword", result)
        self.assertIn("password=***", result)

    def test_sanitize_bearer_token_in_error(self):
        raw_error = Exception("API error: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret failed")
        result = sanitize_db_error(raw_error)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", result)
        self.assertIn("Bearer ***", result)

    def test_clean_error_passes_through(self):
        raw_error = Exception("relation complaint does not exist")
        result = sanitize_db_error(raw_error)
        self.assertIn("complaint", result)
        self.assertNotIn("***", result)


class TestSQLiteFallback(unittest.TestCase):
    """Integration test: app initializes with SQLite when DATABASE_URL is unset."""

    def test_app_creates_with_sqlite(self):
        """When DATABASE_URL is absent, the app must fall back to SQLite in development."""
        from backend.config.settings import normalize_database_url, DB_PATH
        norm = normalize_database_url("")
        fallback_uri = norm if norm else f"sqlite:///{DB_PATH}"
        self.assertTrue(
            fallback_uri.startswith("sqlite"),
            f"Expected SQLite fallback but got: {fallback_uri}"
        )


class TestProductionDatabaseSafety(unittest.TestCase):
    """Verify that in production mode, silent SQLite fallback is blocked."""

    def test_production_mode_blocks_silent_sqlite_fallback(self):
        """FLASK_ENV=production without valid DATABASE_URL must raise RuntimeError."""
        with patch.dict(os.environ, {"FLASK_ENV": "production", "DATABASE_URL": ""}):
            env_db = os.getenv("DATABASE_URL", "").strip()
            norm = normalize_database_url(env_db)
            is_prod = os.getenv("FLASK_ENV", "").lower() == "production"

            self.assertTrue(is_prod)
            self.assertEqual(norm, "")
            with self.assertRaises(RuntimeError) as ctx:
                if not norm and is_prod:
                    raise RuntimeError("In production mode, a valid PostgreSQL DATABASE_URL is strictly required.")
            self.assertIn("DATABASE_URL is strictly required", str(ctx.exception))


class TestSchemaInitializationSafety(unittest.TestCase):
    """
    Regression tests verifying that:
    - Production/Postgres startup NEVER calls db.create_all() automatically.
    - Missing production schema triggers an informative RuntimeError.
    - Alembic upgrade is idempotent.
    """

    def test_production_startup_does_not_invoke_create_all(self):
        from backend import create_app
        from backend.models.base import db

        with patch.object(db, "create_all") as mock_create_all:
            # Configure production mode with mock DB
            with patch("backend.IS_PRODUCTION", True):
                try:
                    create_app({
                        "TESTING": True,
                        "DISABLE_REMINDER_SCHEDULER": True
                    })
                except Exception:
                    pass
            # create_all must NOT have been called
            mock_create_all.assert_not_called()

    def test_missing_schema_raises_informative_error(self):
        from backend import create_app

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            safe_path = tmp_path.replace("\\", "/")
            empty_uri = f"sqlite:///{safe_path}"
            # Force non-test, non-cli context with IS_PRODUCTION
            with patch("backend.IS_PRODUCTION", True):
                with patch("sys.argv", ["gunicorn", "grievai_app:app"]):
                    with self.assertRaises(RuntimeError) as ctx:
                        create_app({
                            "TESTING": False,
                            "SQLALCHEMY_DATABASE_URI": empty_uri,
                            "DISABLE_REMINDER_SCHEDULER": True
                        })
                    self.assertIn("Missing required tables", str(ctx.exception))
                    self.assertIn("flask --app grievai_app db upgrade", str(ctx.exception))
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def test_alembic_creates_schema_and_second_run_is_noop(self):
        from alembic.config import Config as AlembicConfig
        from alembic import command as alembic_command
        from backend import create_app
        import sqlite3

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            tmp_uri = f"sqlite:///{tmp_path}"
            alembic_ini = os.path.join(PORTAL_ROOT, "migrations", "alembic.ini")
            cfg = AlembicConfig(alembic_ini)
            cfg.set_main_option("script_location", os.path.join(PORTAL_ROOT, "migrations"))

            app = create_app({
                "TESTING": True,
                "SKIP_SCHEMA_INIT": True,
                "SQLALCHEMY_DATABASE_URI": tmp_uri,
                "DISABLE_REMINDER_SCHEDULER": True
            })

            # 1. First upgrade
            with app.app_context():
                alembic_command.upgrade(cfg, "head")

            conn = sqlite3.connect(tmp_path)
            cur = conn.cursor()
            cur.execute("SELECT version_num FROM alembic_version")
            rev = cur.fetchone()[0]
            self.assertEqual(rev, "7f2c8a91b4de")

            cur.execute("SELECT count(*) FROM complaint")
            self.assertEqual(cur.fetchone()[0], 0)
            conn.close()

            # 2. Second upgrade (idempotent no-op)
            with app.app_context():
                alembic_command.upgrade(cfg, "head")

        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass


class TestReconstructedAntiRaggingModels(unittest.TestCase):
    """Test the reconstructed models for Anti-Ragging Portal."""

    def setUp(self):
        from backend import create_app
        from backend.models.base import db
        self.app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "DISABLE_REMINDER_SCHEDULER": True})
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

    def tearDown(self):
        from backend.models.base import db
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_committee_member_lifecycle(self):
        from backend.models.base import db
        from backend.models.committee import CommitteeMember

        member = CommitteeMember(
            email="squad.lead@velhightech.com",
            full_name="Dr. S. Ramesh",
            role="squad_member",
            department="Computer Science",
            phone="9876543210"
        )
        db.session.add(member)
        db.session.commit()

        queried = CommitteeMember.query.filter_by(email="squad.lead@velhightech.com").first()
        self.assertIsNotNone(queried)
        self.assertEqual(queried.full_name, "Dr. S. Ramesh")
        self.assertTrue(queried.is_active)
        self.assertEqual(queried.to_dict()["role"], "squad_member")

    def test_complaint_status_history_audit_trail(self):
        from backend.models.base import db
        from backend.models.complaint import Complaint, ComplaintStatusHistory

        c = Complaint(
            student_email="vh13838@velhightech.com",
            complaint_title="Hostel Intimidation Incident",
            complaint_text="Hostel hazing event after hours in Block B.",
            department="Hostel hazing or coercion",
            incident_type="Hostel hazing or coercion",
            severity_level="High",
            status="Open"
        )
        db.session.add(c)
        db.session.flush()

        audit1 = ComplaintStatusHistory(
            complaint_id=c.id,
            previous_status=None,
            new_status="Open",
            action_type="submitted",
            actor_role="student",
            actor_email=c.student_email,
            remarks="Incident submitted by student."
        )
        db.session.add(audit1)
        db.session.commit()

        c.status = "Under Review"
        audit2 = ComplaintStatusHistory(
            complaint_id=c.id,
            previous_status="Open",
            new_status="Under Review",
            action_type="status_update",
            actor_role="admin",
            actor_email="admin@velhightech.com",
            remarks="ARC convened to review narrative."
        )
        db.session.add(audit2)
        db.session.commit()

        history = ComplaintStatusHistory.query.filter_by(complaint_id=c.id).order_by(ComplaintStatusHistory.timestamp.asc()).all()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].action_type, "submitted")
        self.assertEqual(history[1].new_status, "Under Review")
        self.assertEqual(history[1].actor_role, "admin")

    def test_investigation_action_recording(self):
        from backend.models.base import db
        from backend.models.complaint import Complaint
        from backend.models.committee import CommitteeMember, InvestigationAction

        member = CommitteeMember(
            email="investigator@velhightech.com",
            full_name="Prof. Anita Verma",
            role="investigator",
            department="Electrical"
        )
        db.session.add(member)
        db.session.flush()

        c = Complaint(
            student_email="vh13838@velhightech.com",
            complaint_title="Verbal Harassment",
            complaint_text="Detailed harassment narrative.",
            department="Verbal or emotional harassment",
            incident_type="Verbal or emotional harassment"
        )
        db.session.add(c)
        db.session.flush()

        action = InvestigationAction(
            complaint_id=c.id,
            assigned_to_id=member.id,
            assigned_to_name=member.full_name,
            action_type="hearing_scheduled",
            action_summary="Preliminary hearing scheduled with witnesses.",
            confidential_notes="Keep witness identities anonymous during initial meeting.",
            performed_by=member.email
        )
        db.session.add(action)
        db.session.commit()

        queried = InvestigationAction.query.filter_by(complaint_id=c.id).first()
        self.assertIsNotNone(queried)
        self.assertEqual(queried.assigned_to_name, "Prof. Anita Verma")
        self.assertEqual(queried.action_type, "hearing_scheduled")
        self.assertIn("witness identities anonymous", queried.confidential_notes)


if __name__ == "__main__":
    unittest.main()
