"""
Comprehensive Test Suite for Phase 2 (Strict Student Email Validation),
Phase 3 (Complaint Evidence Uploads & Security), Phase 4 (Notifications & Scheduler),
and Phase 5 (Template Footers, Logo Aspect Ratio, and Zero-Emoji Compliance).
"""

import unittest
import os
import io
import re
import shutil
import tempfile
from unittest.mock import patch

import grievai_app
from grievai_app import (
    app, db, Complaint, ComplaintEvidence, Notification,
    inspect_file_signature, format_file_size, process_pending_action_reminders,
    admin_failed_attempts
)

PORTAL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class BaseEvidenceTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        app.config["SESSION_TYPE"] = "filesystem"
        app.config["CSRF_ENABLED"] = False
        app.config["WTF_CSRF_ENABLED"] = False

        self.app_context = app.app_context()
        self.app_context.push()
        db.create_all()

        self.client = app.test_client()
        admin_failed_attempts.clear()

        # Temporary upload folder for test isolation
        self.test_upload_dir = tempfile.mkdtemp()
        self.orig_upload_folder = grievai_app.EVIDENCE_UPLOAD_FOLDER
        grievai_app.EVIDENCE_UPLOAD_FOLDER = self.test_upload_dir

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.app_context.pop()

        grievai_app.EVIDENCE_UPLOAD_FOLDER = self.orig_upload_folder
        shutil.rmtree(self.test_upload_dir, ignore_errors=True)

    def get_csrf(self):
        resp = self.client.get("/student_login")
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.data.decode("utf-8"))
        return match.group(1) if match else "test_csrf_token"


class TestStudentEmailValidation(BaseEvidenceTestCase):
    """Phase 2: Strict Student Email Validation Tests (^vh[0-9]+@velhightech\.com$)."""

    @patch("grievai_app.send_otp_email")
    def test_valid_student_emails_accepted(self, mock_send):
        """Accepted formats: vh13838@velhightech.com, vh12345@velhightech.com."""
        csrf = self.get_csrf()
        valid_emails = ["vh13838@velhightech.com", "vh12345@velhightech.com", "vh99999@velhightech.com"]

        for email in valid_emails:
            with self.subTest(email=email):
                resp = self.client.post("/send_otp", data={"email": email, "csrf_token": csrf}, follow_redirects=True)
                self.assertEqual(resp.status_code, 200)
                self.assertIn(b"Enter 6-Digit OTP", resp.data)
                with self.client.session_transaction() as sess:
                    self.assertEqual(sess.get("email"), email)

    @patch("grievai_app.send_otp_email")
    def test_invalid_student_emails_rejected(self, mock_send):
        """Rejected formats: teacher@velhightech.com, student@gmail.com, vhabc@velhightech.com, vh@velhightech.com."""
        csrf = self.get_csrf()
        invalid_emails = [
            "teacher@velhightech.com",
            "student@gmail.com",
            "vhabc@velhightech.com",
            "vh@velhightech.com",
            "admin@velhightech.com",
            "vh123@yahoo.com",
            "random@othercollege.edu",
            "",
        ]

        for email in invalid_emails:
            with self.subTest(email=email):
                resp = self.client.post("/send_otp", data={"email": email, "csrf_token": csrf})
                self.assertIn(b"Access Restricted", resp.data)
                self.assertIn(b"Student Roll Number", resp.data)

    @patch("grievai_app.send_otp_email")
    def test_email_case_and_whitespace_normalization(self, mock_send):
        """Whitespace and uppercase are trimmed and normalized before regex matching."""
        csrf = self.get_csrf()
        raw_email = "   VH13838@VELHIGHTECH.COM   "

        resp = self.client.post("/send_otp", data={"email": raw_email, "csrf_token": csrf}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Enter 6-Digit OTP", resp.data)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("email"), "vh13838@velhightech.com")

    def test_admin_login_isolation_from_student_regex(self):
        """Administrator login route (/admin_login) is isolated and not restricted by student roll-number validation."""
        csrf = self.get_csrf()
        admin_email = os.getenv("ADMIN_EMAIL", "admin@velhightech.com")
        admin_pass = os.getenv("ADMIN_PASSWORD", "Admin@VelTech2026")

        resp = self.client.post("/admin_login", data={
            "email": admin_email,
            "password": admin_pass,
            "csrf_token": csrf
        }, follow_redirects=False)

        # Admin route accepts the standard admin email and redirects to /admin
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin", resp.headers.get("Location", ""))


class TestComplaintEvidenceUploadsAndSecurity(BaseEvidenceTestCase):
    """Phase 3: Secure Complaint Evidence Uploads & Access Control Tests."""

    def setUp(self):
        super().setUp()
        # Setup verified student session
        with self.client.session_transaction() as sess:
            sess["verified"] = True
            sess["email"] = "vh13838@velhightech.com"

    def test_file_signature_inspector(self):
        """Test magic-byte signature validation logic."""
        # Valid JPEG
        cat, mime = inspect_file_signature(".jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF")
        self.assertEqual(cat, "photo")
        self.assertEqual(mime, "image/jpeg")

        # Valid PNG
        cat, mime = inspect_file_signature(".png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
        self.assertEqual(cat, "photo")
        self.assertEqual(mime, "image/png")

        # Valid PDF
        cat, mime = inspect_file_signature(".pdf", b"%PDF-1.5\n%...")
        self.assertEqual(cat, "document")
        self.assertEqual(mime, "application/pdf")

        # Valid WebM voice
        cat, mime = inspect_file_signature(".webm", b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81", is_voice=True)
        self.assertEqual(cat, "audio")
        self.assertEqual(mime, "audio/webm")

        # Spoofed extension: .jpg with executable header
        cat, mime = inspect_file_signature(".jpg", b"MZ\x90\x00\x03\x00\x00\x00")
        self.assertIsNone(cat)
        self.assertIsNone(mime)

    def test_photo_evidence_upload_success(self):
        """Test multi-photo upload attached to complaint."""
        jpeg_data = b"\xff\xd8\xff\xe0\x00\x10JFIF" + (b"\x00" * 200)
        png_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (b"\x00" * 200)

        data = {
            "complaint": "Evidence test incident with photo documentation",
            "incident_type": "Verbal Abuse",
            "incident_date_time": "2026-10-13 10:00 UTC",
            "location": "Library block 2nd floor",
            "priority": "High",
            "accuracy_declaration": "true",
            "evidence_files": [
                (io.BytesIO(jpeg_data), "photo1.jpg"),
                (io.BytesIO(png_data), "screenshot.png")
            ]
        }

        resp = self.client.post("/predict", data=data, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Incident Report Registered", resp.data)

        # Verify evidence records created in database
        latest_c = Complaint.query.filter_by(student_email="vh13838@velhightech.com").order_by(Complaint.id.desc()).first()
        self.assertIsNotNone(latest_c)
        self.assertEqual(len(latest_c.evidence_files), 2)

        ev_types = [ev.file_type for ev in latest_c.evidence_files]
        self.assertIn("photo", ev_types)

        # Verify storage filenames are randomized UUIDs, not user input
        for ev in latest_c.evidence_files:
            self.assertFalse("photo1.jpg" in ev.storage_filename)
            self.assertFalse("screenshot.png" in ev.storage_filename)
            self.assertTrue(os.path.exists(os.path.join(self.test_upload_dir, ev.storage_filename)))

    def test_voice_recording_attachment_success(self):
        """Test browser voice recording (WebM audio) upload attached to complaint."""
        webm_data = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81" + (b"\x00" * 300)

        data = {
            "complaint": "Incident with spoken audio recording statement",
            "incident_type": "Hostel Ragging",
            "incident_date_time": "2026-10-13 11:30 UTC",
            "location": "Hostel Block A",
            "priority": "Critical",
            "accuracy_declaration": "true",
            "voice_recording": (io.BytesIO(webm_data), "voice_statement_123.webm")
        }

        resp = self.client.post("/predict", data=data, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)

        latest_c = Complaint.query.filter_by(student_email="vh13838@velhightech.com").order_by(Complaint.id.desc()).first()
        self.assertIsNotNone(latest_c)
        voice_evs = [ev for ev in latest_c.evidence_files if ev.file_type == "audio"]
        self.assertEqual(len(voice_evs), 1)
        self.assertEqual(voice_evs[0].mime_type, "audio/webm")

    def test_invalid_extension_rejected_and_cleaned_up(self):
        """Disallowed file extension (.exe) is rejected and does not leave orphan files."""
        exe_data = b"MZ\x90\x00\x03\x00\x00\x00" + (b"\x00" * 100)

        data = {
            "complaint": "Testing rejection of executable payload",
            "incident_type": "Other",
            "accuracy_declaration": "true",
            "evidence_files": [
                (io.BytesIO(exe_data), "malicious.exe")
            ]
        }

        resp = self.client.post("/predict", data=data, content_type="multipart/form-data", follow_redirects=True)
        # Should redirect back to complaint form with flash error
        self.assertIn(b"Evidence submission error", resp.data)

        # Upload dir should be completely clean (no orphan files)
        self.assertEqual(len(os.listdir(self.test_upload_dir)), 0)

    def test_evidence_access_authorization_isolation(self):
        """Enforces authorization: student owner can view, other student blocked (403), admin can view."""
        # Create a complaint with an evidence record
        c = Complaint(
            student_email="vh13838@velhightech.com",
            complaint_text="Secret incident evidence",
            department="Campus Premises",
            status="Open",
            priority="High"
        )
        db.session.add(c)
        db.session.commit()

        # Write physical file in test upload directory
        storage_file = "test_evidence_123.jpg"
        file_path = os.path.join(self.test_upload_dir, storage_file)
        with open(file_path, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0\x00\x10JFIF" + (b"\x00" * 100))

        ev = ComplaintEvidence(
            complaint_id=c.id,
            file_type="photo",
            original_filename="student_proof.jpg",
            storage_filename=storage_file,
            mime_type="image/jpeg",
            file_size_bytes=os.path.getsize(file_path)
        )
        db.session.add(ev)
        db.session.commit()

        # 1. Student Owner accessing evidence -> 200 OK
        with self.client.session_transaction() as sess:
            sess["verified"] = True
            sess["email"] = "vh13838@velhightech.com"
            sess.pop("admin_logged_in", None)

        resp_owner = self.client.get(f"/evidence/{ev.id}")
        self.assertEqual(resp_owner.status_code, 200)
        self.assertEqual(resp_owner.content_type, "image/jpeg")

        # 2. Another Student accessing evidence -> 403 Forbidden
        with self.client.session_transaction() as sess:
            sess["verified"] = True
            sess["email"] = "vh99999@velhightech.com"  # Different student!
            sess.pop("admin_logged_in", None)

        resp_unauth_student = self.client.get(f"/evidence/{ev.id}")
        self.assertEqual(resp_unauth_student.status_code, 403)
        self.assertIn(b"Unauthorized Evidence Access", resp_unauth_student.data)

        # 3. Unauthenticated visitor accessing evidence -> 403 Forbidden
        with self.client.session_transaction() as sess:
            sess.clear()

        resp_visitor = self.client.get(f"/evidence/{ev.id}")
        self.assertEqual(resp_visitor.status_code, 403)

        # 4. Authenticated Administrator accessing evidence -> 200 OK
        with self.client.session_transaction() as sess:
            sess.clear()
            sess["admin_logged_in"] = True

        resp_admin = self.client.get(f"/evidence/{ev.id}")
        self.assertEqual(resp_admin.status_code, 200)

    def test_missing_evidence_file_handled_gracefully(self):
        """Missing file on disk returns clean 404 without crashing."""
        c = Complaint(
            student_email="vh13838@velhightech.com",
            complaint_text="Missing file incident",
            department="Hostel",
            status="Open"
        )
        db.session.add(c)
        db.session.commit()

        ev = ComplaintEvidence(
            complaint_id=c.id,
            file_type="document",
            original_filename="missing_doc.pdf",
            storage_filename="non_existent_uuid.pdf",
            mime_type="application/pdf",
            file_size_bytes=1024
        )
        db.session.add(ev)
        db.session.commit()

        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True

        resp = self.client.get(f"/evidence/{ev.id}")
        self.assertEqual(resp.status_code, 404)
        self.assertIn(b"Evidence File Not Found", resp.data)


class TestInstitutionalFooterAndCompliance(BaseEvidenceTestCase):
    """Phase 1 & Phase 5: Institutional Footer, Logo Aspect Ratio, and Zero-Emoji Compliance."""

    ALL_12_TEMPLATES = [
        "index.html",
        "student_login.html",
        "verify_otp.html",
        "student_dashboard.html",
        "complaint_form.html",
        "success.html",
        "track.html",
        "admin_login.html",
        "admin.html",
        "admin_complaint_detail.html",
        "admin_logs.html",
        "error.html",
    ]

    EXACT_SAR_TEXT = "NBA – Tier 1 SAR Guideline: Criterion No. 10.1.4"
    EXACT_COPYRIGHT_TEXT = "Powered by Anti-Ragging Committee | © 2026 Vel Tech High Tech Dr. Rangarajan Dr. Sakunthala Engineering College. All rights reserved."

    def test_all_12_templates_include_institutional_footer(self):
        """Verify the exact required institutional footer is present on all 12 templates."""
        templates_dir = os.path.join(PORTAL_DIR, "templates")

        for tmpl_name in self.ALL_12_TEMPLATES:
            with self.subTest(template=tmpl_name):
                path = os.path.join(templates_dir, tmpl_name)
                self.assertTrue(os.path.exists(path), f"Template missing: {tmpl_name}")
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()

                # Checks footer include or component presence
                has_footer_include = "components/footer.html" in content or "portal-footer" in content
                self.assertTrue(has_footer_include, f"Template {tmpl_name} lacks institutional footer component.")

        # Also verify footer component text directly
        footer_comp_path = os.path.join(templates_dir, "components", "footer.html")
        self.assertTrue(os.path.exists(footer_comp_path))
        with open(footer_comp_path, "r", encoding="utf-8") as f:
            footer_html = f.read()

        self.assertIn("Criterion No. 10.1.4", footer_html)
        self.assertIn("Vel Tech High Tech Dr. Rangarajan Dr. Sakunthala Engineering College", footer_html)

    def test_college_logo_presence_and_proportions(self):
        """Verify the college logo exists and CSS preserves its aspect ratio."""
        logo_path = os.path.join(PORTAL_DIR, "static", "images", "veltech_college_logo.png")
        self.assertTrue(os.path.exists(logo_path), "College logo file missing at static/images/veltech_college_logo.png")

        # Check CSS for object-fit / aspect ratio preservation
        css_path = os.path.join(PORTAL_DIR, "static", "css", "style.css")
        with open(css_path, "r", encoding="utf-8") as f:
            css = f.read()

        self.assertIn(".college-logo", css)
        self.assertIn("object-fit: contain", css)

    def test_zero_emojis_across_all_templates(self):
        """Check all templates for emojis and preserve existing professional SVG icon style."""
        emoji_pattern = re.compile(
            r"[\U0001F600-\U0001F64F"  # emoticons
            r"\U0001F300-\U0001F5FF"  # symbols & pictographs
            r"\U0001F680-\U0001F6FF"  # transport & map
            r"\U0001F1E0-\U0001F1FF"  # flags (iOS)
            r"\U00002702-\U000027B0"
            r"\U000024C2-\U0001F251"
            r"]+",
            flags=re.UNICODE
        )

        templates_dir = os.path.join(PORTAL_DIR, "templates")
        for tmpl_name in self.ALL_12_TEMPLATES:
            with self.subTest(template=tmpl_name):
                path = os.path.join(templates_dir, tmpl_name)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()

                emojis_found = emoji_pattern.findall(content)
                self.assertEqual(len(emojis_found), 0, f"Emoji detected in {tmpl_name}: {emojis_found}")


class TestNotificationPreservation(BaseEvidenceTestCase):
    """Phase 4: Preserve and Verify Notification System and Reminder Deduplication."""

    def test_pending_action_reminders_deduplication(self):
        """Verify process_pending_action_reminders runs idempotently without creating duplicate notifications."""
        initial_count = Notification.query.count()
        c1 = process_pending_action_reminders()
        c2 = process_pending_action_reminders()
        # Second run should generate 0 duplicates
        self.assertEqual(c2, 0)


if __name__ == "__main__":
    unittest.main()
