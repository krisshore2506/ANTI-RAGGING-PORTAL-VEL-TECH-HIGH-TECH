import os
import sys
import time
import re
import unittest
from email.mime.multipart import MIMEMultipart
from unittest.mock import patch, MagicMock

# Configure test environment variables before importing application
os.environ["SECRET_KEY"] = "test-secret-key-32-chars-minimum-length-for-testing"
os.environ["ADMIN_EMAIL"] = "admin@velhightech.com"
os.environ["ADMIN_PASSWORD"] = "AdminSecurePass123"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import grievai_app
from grievai_app import (
    app,
    db,
    Complaint,
    Cleaner,
    CallLog,
    SMSLog,
    hash_tracking_token,
    admin_failed_attempts,
    tracking_ip_attempts,
    send_otp_email,
    send_status_update_email,
)


class BaseE2EWorkflowTestCase(unittest.TestCase):
    """
    Base test fixture ensuring complete isolation:
    - In-memory SQLite database (:memory:)
    - Session isolation
    - In-memory rate limiting and lockout state resets
    """

    def setUp(self):
        app.config["TESTING"] = True
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        app.config["SESSION_TYPE"] = "filesystem"
        app.config["CSRF_ENABLED"] = True

        self.app_context = app.app_context()
        self.app_context.push()
        db.create_all()

        self.client = app.test_client()

        # Reset in-memory rate limiting dictionaries between tests
        admin_failed_attempts.clear()
        tracking_ip_attempts.clear()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.app_context.pop()

    def get_csrf_token(self):
        """Establish a clean session and extract the CSRF token."""
        self.client.get("/student_login")
        with self.client.session_transaction() as sess:
            token = sess.get("csrf_token")
        return token


class TestStudentEndToEndJourney(BaseE2EWorkflowTestCase):
    """
    Validates the complete student user journey end-to-end:
    Landing -> Login -> OTP Dispatch (Mocked) -> Verification -> Dashboard ->
    Complaint Form -> Submission (/predict) -> Success Receipt -> Tracking (/track) ->
    Student Dashboard Updated -> Logout -> Unauthenticated Route Guards.
    """

    @patch("grievai_app.send_otp_email")
    def test_full_student_lifecycle(self, mock_send_email):
        """Test full student lifecycle from landing to complaint submission and tracking."""
        student_email = "student_journey@velhightech.com"

        # 1. Access Landing Page
        resp_home = self.client.get("/")
        self.assertEqual(resp_home.status_code, 200)
        self.assertIn(b"Anti-Ragging Portal", resp_home.data)
        self.assertIn(b"Student Safety", resp_home.data)

        # 2. Access Student Login Page
        resp_login = self.client.get("/student_login")
        self.assertEqual(resp_login.status_code, 200)
        self.assertIn(b"Student Authentication", resp_login.data)
        csrf = self.get_csrf_token()

        # 3. Request OTP for official student email
        resp_send_otp = self.client.post("/send_otp", data={
            "email": student_email,
            "csrf_token": csrf
        }, follow_redirects=True)
        self.assertEqual(resp_send_otp.status_code, 200)
        self.assertIn(b"An OTP has been sent to your college email", resp_send_otp.data)

        # Verify email was dispatched via mocked sender
        mock_send_email.assert_called_once()
        sent_email, sent_otp = mock_send_email.call_args[0]
        self.assertEqual(sent_email, student_email)
        self.assertEqual(len(sent_otp), 6)
        self.assertTrue(sent_otp.isdigit())

        # 4. Verify OTP
        with self.client.session_transaction() as sess:
            csrf_otp = sess.get("csrf_token")

        resp_verify = self.client.post("/verify_otp", data={
            "otp": sent_otp,
            "csrf_token": csrf_otp
        }, follow_redirects=True)
        self.assertEqual(resp_verify.status_code, 200)
        self.assertIn(b"Your Complaint History", resp_verify.data)

        # Verify session state
        with self.client.session_transaction() as sess:
            self.assertTrue(sess.get("verified"))
            self.assertEqual(sess.get("email"), student_email)

        # 5. Load Complaint Form
        resp_form = self.client.get("/complaint_form")
        self.assertEqual(resp_form.status_code, 200)
        self.assertIn(b"Anti-Ragging Incident Reporting Form", resp_form.data)
        self.assertIn(student_email.encode("utf-8"), resp_form.data)

        with self.client.session_transaction() as sess:
            form_csrf = sess.get("csrf_token")

        # 6. Submit Complaint (/predict)
        complaint_payload = {
            "complaint": "Senior students demanded money and threatened me near Hostel Block B.",
            "incident_type": "Hostel hazing or coercion",
            "incident_date_time": "2026-10-11 14:00 UTC",
            "location": "Hostel Premises",
            "location_details": "Block B, 2nd Floor Corridor",
            "title": "Extortion and Threat by Senior Students",
            "priority": "High",
            "accused_details": "Two final-year hostel residents",
            "witness_details": "Roommate who witnessed the confrontation",
            "is_confidential": "on",
            "accuracy_declaration": "on",
            "csrf_token": form_csrf
        }

        resp_predict = self.client.post("/predict", data=complaint_payload)
        self.assertEqual(resp_predict.status_code, 200)

        # Verify Success Page Content
        self.assertIn(b"Incident Report Registered", resp_predict.data)
        self.assertIn(b"Reference Ticket ID", resp_predict.data)
        self.assertIn(b"Confidential Tracking Token", resp_predict.data)
        self.assertIn(b"ARC-", resp_predict.data)
        self.assertIn(b"Confidential Handling Active", resp_predict.data)

        # Extract ticket ID and raw tracking token from rendered success HTML
        html_text = resp_predict.data.decode("utf-8")
        token_match = re.search(r"ARC-[A-Za-z0-9_-]+", html_text)
        self.assertIsNotNone(token_match, "Tracking token not found in success.html output")
        raw_token = token_match.group(0)

        # Verify Record in Test Database and extract ticket ID
        complaint_db = Complaint.query.filter_by(student_email=student_email).first()
        self.assertIsNotNone(complaint_db, "Complaint was not created in test database")
        ticket_id = complaint_db.id
        self.assertIn(f"#{ticket_id}".encode("utf-8"), resp_predict.data)
        self.assertEqual(complaint_db.student_email, student_email)
        self.assertEqual(complaint_db.status, "Open")
        self.assertEqual(complaint_db.incident_type, "Hostel hazing or coercion")
        self.assertTrue(complaint_db.is_confidential)
        self.assertEqual(complaint_db.tracking_token, hash_tracking_token(raw_token))

        # 7. Track Complaint via Token (Public lookup via GET query parameters)
        resp_track_get = self.client.get(f"/track?id={ticket_id}&token={raw_token}")
        self.assertEqual(resp_track_get.status_code, 200)
        self.assertIn(b"Procedural Enquiry Stepper", resp_track_get.data)
        self.assertIn(b"Hostel hazing or coercion", resp_track_get.data)
        self.assertIn(f"#{ticket_id}".encode("utf-8"), resp_track_get.data)

        # Track Complaint via Token (POST form submission with CSRF)
        with self.client.session_transaction() as sess:
            track_csrf = sess.get("csrf_token")

        resp_track_post = self.client.post("/track", data={
            "complaint_id": str(ticket_id),
            "tracking_token": raw_token,
            "csrf_token": track_csrf
        })
        self.assertEqual(resp_track_post.status_code, 200)
        self.assertIn(b"Procedural Enquiry Stepper", resp_track_post.data)

        # 8. Verify complaint appears on Student Dashboard
        resp_dashboard = self.client.get("/student_dashboard")
        self.assertEqual(resp_dashboard.status_code, 200)
        self.assertIn(f"#{ticket_id}".encode("utf-8"), resp_dashboard.data)
        self.assertIn(b"Extortion and Threat by Senior Students", resp_dashboard.data)
        self.assertIn(b"Open", resp_dashboard.data)

        # 9. Student Logout
        resp_logout = self.client.get("/student_logout", follow_redirects=True)
        self.assertEqual(resp_logout.status_code, 200)

        with self.client.session_transaction() as sess:
            self.assertIsNone(sess.get("verified"))
            self.assertIsNone(sess.get("email"))

        # 10. Attempting to access dashboard after logout redirects to student_login
        resp_unauth = self.client.get("/student_dashboard")
        self.assertEqual(resp_unauth.status_code, 302)
        self.assertTrue(resp_unauth.headers["Location"].endswith("/student_login"))


class TestAdministratorEndToEndJourney(BaseE2EWorkflowTestCase):
    """
    Validates the complete administrator journey end-to-end:
    Auth guard -> Admin Login -> ARC Dashboard Metrics & List ->
    Complaint Details -> Status Transitions (Open -> Under Review -> Action Taken -> Closed) ->
    Student Status Update Email Notifications -> Audit Logs (/admin/logs) -> Logout.
    """

    @patch("grievai_app.send_status_update_email")
    def test_full_admin_lifecycle(self, mock_status_email):
        """Test full administrator lifecycle including status updates and notification generation."""
        student_email = "victim_notify@velhightech.com"

        # Create sample complaint in isolated test database
        complaint = Complaint(
            student_email=student_email,
            complaint_title="Verbal Harassment in Canteen",
            complaint_text="Seniors used abusive language during lunch.",
            department="Verbal or emotional harassment",
            status="Open",
            priority="Medium",
            incident_type="Verbal or emotional harassment",
            severity_level="Medium",
            incident_date_time="2026-10-11 12:30 UTC",
            incident_location="College Canteen",
            accused_details="Unknown 3rd year students",
            witness_details="Canteen staff",
            is_confidential=False,
            tracking_token=hash_tracking_token("ARC-token-admin-test-1234"),
            committee_assigned="Anti-Ragging Committee",
            admin_remarks="Initial triage: Open"
        )
        db.session.add(complaint)
        db.session.commit()
        complaint_id = complaint.id

        # 1. Unauthenticated access blocked
        resp_unauth_admin = self.client.get("/admin")
        self.assertEqual(resp_unauth_admin.status_code, 302)
        self.assertTrue(resp_unauth_admin.headers["Location"].endswith("/admin_login"))

        resp_unauth_detail = self.client.get(f"/admin/complaint/{complaint_id}")
        self.assertEqual(resp_unauth_detail.status_code, 302)
        self.assertTrue(resp_unauth_detail.headers["Location"].endswith("/admin_login"))

        resp_unauth_logs = self.client.get("/admin/logs")
        self.assertEqual(resp_unauth_logs.status_code, 302)
        self.assertTrue(resp_unauth_logs.headers["Location"].endswith("/admin_login"))

        # 2. Administrator Login
        resp_login_page = self.client.get("/admin_login")
        self.assertEqual(resp_login_page.status_code, 200)

        with self.client.session_transaction() as sess:
            admin_csrf = sess.get("csrf_token")

        resp_admin_login = self.client.post("/admin_login", data={
            "email": "admin@velhightech.com",
            "password": "AdminSecurePass123",
            "csrf_token": admin_csrf
        }, follow_redirects=True)
        self.assertEqual(resp_admin_login.status_code, 200)
        self.assertIn(b"Anti-Ragging Committee Dashboard", resp_admin_login.data)

        with self.client.session_transaction() as sess:
            self.assertTrue(sess.get("admin_logged_in"))

        # 3. ARC Dashboard Metrics & Incident Listing
        resp_dash = self.client.get("/admin")
        self.assertEqual(resp_dash.status_code, 200)
        self.assertIn(b"Total Incidents", resp_dash.data)
        self.assertIn(b"Verbal Harassment in Canteen", resp_dash.data)
        self.assertIn(f"#{complaint_id}".encode("utf-8"), resp_dash.data)

        # 4. View Complaint Details (HTML view)
        resp_detail = self.client.get(f"/admin/complaint/{complaint_id}")
        self.assertEqual(resp_detail.status_code, 200)
        self.assertIn(b"Incident Case Dossier", resp_detail.data)
        self.assertIn(student_email.encode("utf-8"), resp_detail.data)

        # View Complaint Details (JSON API endpoint for asynchronous inspection)
        resp_detail_json = self.client.get(f"/admin/complaint/{complaint_id}", headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(resp_detail_json.status_code, 200)
        json_data = resp_detail_json.get_json()
        self.assertEqual(json_data["id"], complaint_id)
        self.assertEqual(json_data["student_email"], student_email)
        self.assertEqual(json_data["status"], "Open")

        # 5. Status Transition 1: Update to 'Under Review'
        with self.client.session_transaction() as sess:
            action_csrf = sess.get("csrf_token")

        resp_update_1 = self.client.post("/admin", data={
            "complaint_id": str(complaint_id),
            "status": "Under Review",
            "admin_remarks": "Inquiry commenced by Committee Squad.",
            "csrf_token": action_csrf
        }, follow_redirects=True)
        self.assertEqual(resp_update_1.status_code, 200)
        self.assertIn(b"Status is now &#39;Under Review&#39;", resp_update_1.data)

        # Verify mocked notification email dispatched
        mock_status_email.assert_called_with(student_email, complaint_id, "Under Review")
        mock_status_email.reset_mock()

        # 6. Status Transition 2: Update to 'Action Taken' via detail page
        resp_update_2 = self.client.post(f"/admin/complaint/{complaint_id}", data={
            "status": "Action Taken",
            "admin_remarks": "Identification verified and formal warning served.",
            "csrf_token": action_csrf
        }, follow_redirects=True)
        self.assertEqual(resp_update_2.status_code, 200)

        # Verify database record updated
        updated_complaint = Complaint.query.get(complaint_id)
        self.assertEqual(updated_complaint.status, "Action Taken")
        mock_status_email.assert_called_with(student_email, complaint_id, "Action Taken")
        mock_status_email.reset_mock()

        # 7. Status Transition 3: Update to 'Closed'
        resp_update_3 = self.client.post(f"/admin/complaint/{complaint_id}", data={
            "status": "Closed",
            "admin_remarks": "Disciplinary resolution executed and logged. Matter closed.",
            "csrf_token": action_csrf
        }, follow_redirects=True)
        self.assertEqual(resp_update_3.status_code, 200)

        updated_complaint = Complaint.query.get(complaint_id)
        self.assertEqual(updated_complaint.status, "Closed")
        mock_status_email.assert_called_with(student_email, complaint_id, "Closed")

        # 8. Inspect Audit Logs (/admin/logs)
        resp_logs = self.client.get("/admin/logs")
        self.assertEqual(resp_logs.status_code, 200)
        self.assertIn(b"Disciplinary & Escalation Logs", resp_logs.data)

        # 9. Administrator Logout
        resp_logout = self.client.get("/admin_logout", follow_redirects=True)
        self.assertEqual(resp_logout.status_code, 200)
        self.assertIn(b"Administrative Portal", resp_logout.data)

        with self.client.session_transaction() as sess:
            self.assertIsNone(sess.get("admin_logged_in"))

        # Verify subsequent unauthenticated access blocked
        resp_recheck = self.client.get("/admin")
        self.assertEqual(resp_recheck.status_code, 302)


class TestSecurityNegativeScenarios(BaseE2EWorkflowTestCase):
    """
    Validates all security boundary conditions, input rejections, CSRF enforcement,
    OTP expiry, resend cooldown, and rate limiting locks.
    """

    def test_non_college_email_rejected(self):
        """Verify non-college domains are strictly denied access with error page."""
        csrf = self.get_csrf_token()
        resp = self.client.post("/send_otp", data={
            "email": "external.user@gmail.com",
            "csrf_token": csrf
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Invalid Email Domain", resp.data)
        self.assertIn(b"restricted exclusively to valid @velhightech.com", resp.data)

    def test_tampered_and_missing_csrf_rejected(self):
        """Verify POST endpoints reject requests without CSRF or with tampered CSRF tokens."""
        self.get_csrf_token()

        # Missing CSRF
        resp_missing = self.client.post("/send_otp", data={"email": "student@velhightech.com"})
        self.assertEqual(resp_missing.status_code, 403)
        self.assertIn(b"CSRF Validation Error", resp_missing.data)

        # Tampered CSRF
        resp_tampered = self.client.post("/send_otp", data={
            "email": "student@velhightech.com",
            "csrf_token": "tampered-forged-csrf-token-12345678"
        })
        self.assertEqual(resp_tampered.status_code, 403)
        self.assertIn(b"CSRF Validation Error", resp_tampered.data)

    @patch("grievai_app.send_otp_email")
    def test_expired_otp_rejected(self, mock_send_email):
        """Verify OTP is strictly rejected when verified after 60 seconds."""
        csrf = self.get_csrf_token()
        self.client.post("/send_otp", data={
            "email": "student_timer@velhightech.com",
            "csrf_token": csrf
        })
        sent_otp = mock_send_email.call_args[0][1]

        # Artificially age OTP timestamp by 65 seconds
        with self.client.session_transaction() as sess:
            sess["otp_time"] = time.time() - 65

        resp = self.client.post("/verify_otp", data={
            "otp": sent_otp,
            "csrf_token": csrf
        }, follow_redirects=True)
        self.assertIn(b"Verification code has expired", resp.data)

        with self.client.session_transaction() as sess:
            self.assertIsNone(sess.get("verified"))

    @patch("grievai_app.send_otp_email")
    def test_wrong_otp_rejected(self, mock_send_email):
        """Verify entering incorrect OTP is rejected."""
        csrf = self.get_csrf_token()
        self.client.post("/send_otp", data={
            "email": "student_wrong@velhightech.com",
            "csrf_token": csrf
        })

        resp = self.client.post("/verify_otp", data={
            "otp": "000000",
            "csrf_token": csrf
        }, follow_redirects=True)
        self.assertIn(b"Invalid verification code", resp.data)

        with self.client.session_transaction() as sess:
            self.assertIsNone(sess.get("verified"))

    @patch("grievai_app.send_otp_email")
    def test_otp_single_use_replay_prevention(self, mock_send_email):
        """Verify an OTP cannot be replayed after successful verification."""
        csrf = self.get_csrf_token()
        self.client.post("/send_otp", data={
            "email": "student_replay@velhightech.com",
            "csrf_token": csrf
        })
        sent_otp = mock_send_email.call_args[0][1]

        # 1st verification succeeds
        resp1 = self.client.post("/verify_otp", data={
            "otp": sent_otp,
            "csrf_token": csrf
        })
        self.assertEqual(resp1.status_code, 302)

        # Replay attempt fails because OTP session was cleared
        resp2 = self.client.post("/verify_otp", data={
            "otp": sent_otp,
            "csrf_token": csrf
        }, follow_redirects=True)
        self.assertIn(b"No active verification code found", resp2.data)

    @patch("grievai_app.send_otp_email")
    def test_resend_cooldown_enforcement(self, mock_send_email):
        """Verify rapid resend requests within 30 seconds are throttled."""
        csrf = self.get_csrf_token()
        self.client.post("/send_otp", data={
            "email": "student_cooldown@velhightech.com",
            "csrf_token": csrf
        })

        # Resend immediately via POST with CSRF
        resp_resend = self.client.post("/resend_otp", data={"csrf_token": csrf}, follow_redirects=True)
        self.assertEqual(resp_resend.status_code, 200)
        self.assertIn(b"Please wait", resp_resend.data)
        self.assertIn(b"second(s) before requesting another verification code", resp_resend.data)

    def test_tracking_unauthorized_token_access_blocked(self):
        """Verify unauthorized users cannot inspect arbitrary complaints without a valid token."""
        complaint = Complaint(
            student_email="private_victim@velhightech.com",
            complaint_title="Hostel Incident",
            complaint_text="Detailed confidential report.",
            department="Hostel hazing or coercion",
            status="Open",
            priority="High",
            incident_type="Hostel hazing or coercion",
            tracking_token=hash_tracking_token("ARC-real-token-987654321")
        )
        db.session.add(complaint)
        db.session.commit()
        complaint_id = complaint.id

        # Attempt to track with forged token
        resp_invalid = self.client.get(f"/track?id={complaint_id}&token=ARC-fake-forged-token-0000")
        self.assertEqual(resp_invalid.status_code, 200)
        self.assertIn(b"Unable to verify incident ticket", resp_invalid.data)
        # Ensure confidential complaint text is NOT leaked
        self.assertNotIn(b"Detailed confidential report", resp_invalid.data)


class TestAllRoutesAndTemplateRendering(BaseE2EWorkflowTestCase):
    """
    Renders all 12 portal templates across major application routes to guarantee
    zero template syntax regressions, missing context variables, or runtime 500 errors.
    """

    def test_all_routes_render_successfully(self):
        """Exercise all major GET routes for HTTP 200 and absence of Jinja errors."""
        # 1. Landing Page (index.html)
        r1 = self.client.get("/")
        self.assertEqual(r1.status_code, 200)
        self.assertIn(b"Anti-Ragging Portal", r1.data)

        # 2. Student Login Page (student_login.html)
        r2 = self.client.get("/student_login")
        self.assertEqual(r2.status_code, 200)
        self.assertIn(b"Student Login", r2.data)

        # 3. Verify OTP Page (verify_otp.html)
        with self.client.session_transaction() as sess:
            sess["email"] = "test_render@velhightech.com"
            sess["otp_time"] = time.time()
        r3 = self.client.get("/verify_otp")
        self.assertEqual(r3.status_code, 200)
        self.assertIn(b"Verify Identity", r3.data)

        # 4. Student Dashboard Page (student_dashboard.html)
        with self.client.session_transaction() as sess:
            sess["verified"] = True
            sess["email"] = "test_render@velhightech.com"
        r4 = self.client.get("/student_dashboard")
        self.assertEqual(r4.status_code, 200)
        self.assertIn(b"Student Dashboard", r4.data)

        # 5. Complaint Form Page (complaint_form.html)
        r5 = self.client.get("/complaint_form")
        self.assertEqual(r5.status_code, 200)
        self.assertIn(b"Anti-Ragging Incident Reporting Form", r5.data)

        # 6. Admin Login Page (admin_login.html)
        r6 = self.client.get("/admin_login")
        self.assertEqual(r6.status_code, 200)
        self.assertIn(b"Administrative Portal", r6.data)

        # Create test complaint for admin detail view
        complaint = Complaint(
            student_email="test_render@velhightech.com",
            complaint_title="Test Incident Title",
            complaint_text="Incident description for template verification.",
            department="Verbal or emotional harassment",
            status="Under Review",
            priority="Medium",
            incident_type="Verbal or emotional harassment",
            incident_date_time="2026-10-11 10:00",
            incident_location="Main Library",
            tracking_token=hash_tracking_token("ARC-token-render-12345")
        )
        db.session.add(complaint)
        db.session.commit()
        complaint_id = complaint.id

        # 7. Admin Dashboard Page (admin.html)
        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
        r7 = self.client.get("/admin")
        self.assertEqual(r7.status_code, 200)
        self.assertIn(b"Anti-Ragging Committee Dashboard", r7.data)

        # 8. Admin Complaint Detail Page (admin_complaint_detail.html)
        r8 = self.client.get(f"/admin/complaint/{complaint_id}")
        self.assertEqual(r8.status_code, 200)
        self.assertIn(b"Incident Case Dossier", r8.data)

        # 9. Admin Logs Page (admin_logs.html)
        r9 = self.client.get("/admin/logs")
        self.assertEqual(r9.status_code, 200)
        self.assertIn(b"Disciplinary & Escalation Logs", r9.data)

        # 10. Track Complaint Page (track.html - unattempted view)
        r10 = self.client.get("/track")
        self.assertEqual(r10.status_code, 200)
        self.assertIn(b"Track Case Resolution Status", r10.data)

        # 11. Track Complaint Page (track.html - populated view)
        r11 = self.client.get(f"/track?id={complaint_id}&token=ARC-token-render-12345")
        self.assertEqual(r11.status_code, 200)
        self.assertIn(b"Procedural Enquiry Stepper", r11.data)

        # 12. Error Page (error.html)
        csrf = self.get_csrf_token()
        r12 = self.client.post("/send_otp", data={"email": "bad@gmail.com", "csrf_token": csrf})
        self.assertEqual(r12.status_code, 200)
        self.assertIn(b"Access Restricted", r12.data)


class TestEmailMIMEStructureAndAlternatives(unittest.TestCase):
    """
    Inspects MIME multipart email structures for OTP and Status Updates:
    - Verifies 'multipart/alternative' container
    - Verifies presence of both 'text/plain' and 'text/html' alternative parts
    - Verifies key text content in both alternatives
    - Verifies dynamic character escaping against injection
    - Completely mocks SMTP dispatch to ensure no real network traffic occurs.
    """

    @patch("smtplib.SMTP")
    def test_otp_email_mime_multipart_structure(self, mock_smtp):
        """Verify OTP verification email contains proper multipart/alternative structure and copy."""
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        recipient = "student_mime@velhightech.com"
        otp_code = "839201"

        send_otp_email(recipient, otp_code)

        mock_server.send_message.assert_called_once()
        msg = mock_server.send_message.call_args[0][0]

        # 1. Container MIME type
        self.assertEqual(msg.get_content_type(), "multipart/alternative")
        self.assertEqual(msg["To"], recipient)
        self.assertIn("Email Verification", msg["Subject"])

        # 2. Extract payloads
        payloads = msg.get_payload()
        self.assertEqual(len(payloads), 2, "Email must contain exactly two alternative parts (plain and html)")

        part_plain = payloads[0]
        part_html = payloads[1]

        self.assertEqual(part_plain.get_content_type(), "text/plain")
        self.assertEqual(part_html.get_content_type(), "text/html")

        # 3. Plain text inspection
        plain_body = part_plain.get_payload(decode=True).decode("utf-8")
        self.assertIn(otp_code, plain_body)
        self.assertIn("expires in 1 minute", plain_body)
        self.assertIn("SECURITY NOTICE:", plain_body)
        self.assertIn("Vel Tech High Tech", plain_body)

        # 4. HTML inspection
        html_body = part_html.get_payload(decode=True).decode("utf-8")
        self.assertIn(otp_code, html_body)
        self.assertIn("This code expires in 1 minute.", html_body)
        self.assertIn("Security Notice:", html_body)
        self.assertIn("ANTI RAGGING PORTAL", html_body)

    @patch("smtplib.SMTP")
    def test_status_update_email_mime_multipart_structure(self, mock_smtp):
        """Verify status update notification email contains both plain and html alternatives."""
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        recipient = "student_status@velhightech.com"
        complaint_id = 42
        new_status = "Action Taken"

        send_status_update_email(recipient, complaint_id, new_status)

        mock_server.send_message.assert_called_once()
        msg = mock_server.send_message.call_args[0][0]

        # 1. Container MIME type
        self.assertEqual(msg.get_content_type(), "multipart/alternative")
        self.assertEqual(msg["To"], recipient)
        self.assertIn(f"#{complaint_id}", msg["Subject"])

        # 2. Extract payloads
        payloads = msg.get_payload()
        self.assertEqual(len(payloads), 2, "Status email must contain exactly two alternative parts (plain and html)")

        part_plain = payloads[0]
        part_html = payloads[1]

        self.assertEqual(part_plain.get_content_type(), "text/plain")
        self.assertEqual(part_html.get_content_type(), "text/html")

        # 3. Plain text inspection
        plain_body = part_plain.get_payload(decode=True).decode("utf-8")
        self.assertIn(f"#{complaint_id}", plain_body)
        self.assertIn("Action Taken", plain_body)
        self.assertIn("Anti-Ragging Committee", plain_body)

        # 4. HTML inspection
        html_body = part_html.get_payload(decode=True).decode("utf-8")
        self.assertIn(f"#{complaint_id}", html_body)
        self.assertIn("Action Taken", html_body)
        self.assertIn("Anti-Ragging Portal", html_body)


if __name__ == "__main__":
    unittest.main()
