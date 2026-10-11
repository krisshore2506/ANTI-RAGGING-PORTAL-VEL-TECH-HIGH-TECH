import os
import sys
import time
import unittest
from unittest.mock import patch, MagicMock

# Set test environment variables before importing app
os.environ["SECRET_KEY"] = "test-secret-key-32-chars-minimum-length-for-testing"
os.environ["ADMIN_EMAIL"] = "admin@velhightech.com"
os.environ["ADMIN_PASSWORD"] = "AdminSecurePass123"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import grievai_app
from grievai_app import app, db, Complaint, hash_tracking_token, admin_failed_attempts


class BaseSecurityTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        app.config["SESSION_TYPE"] = "filesystem"
        app.config["CSRF_ENABLED"] = True

        self.app_context = app.app_context()
        self.app_context.push()
        db.create_all()

        self.client = app.test_client()

        # Clear in-memory admin lockout table between tests
        admin_failed_attempts.clear()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def get_csrf_token(self):
        """Helper to establish a session and retrieve the session CSRF token."""
        response = self.client.get("/student_login")
        with self.client.session_transaction() as sess:
            token = sess.get("csrf_token")
        return token


class TestSessionAndConfigSecurity(BaseSecurityTestCase):
    def test_stable_secret_key_loaded(self):
        """Verify SECRET_KEY is stable and correctly set from environment."""
        self.assertTrue(bool(app.secret_key))
        self.assertEqual(app.secret_key, os.environ["SECRET_KEY"])

    def test_session_cookie_security_flags(self):
        """Verify session cookies have HttpOnly and SameSite configured."""
        self.assertTrue(app.config.get("SESSION_COOKIE_HTTPONLY"))
        self.assertEqual(app.config.get("SESSION_COOKIE_SAMESITE"), "Lax")

    def test_student_logout_clears_session(self):
        """Verify student logout completely invalidates the session."""
        with self.client.session_transaction() as sess:
            sess["verified"] = True
            sess["email"] = "student@velhightech.com"

        resp = self.client.get("/student_logout", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.client.session_transaction() as sess:
            self.assertIsNone(sess.get("verified"))
            self.assertIsNone(sess.get("email"))

    def test_admin_logout_invalidates_admin_session(self):
        """Verify admin logout revokes admin credentials from session."""
        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True

        resp = self.client.get("/admin_logout", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.client.session_transaction() as sess:
            self.assertIsNone(sess.get("admin_logged_in"))


class TestOTPSecurity(BaseSecurityTestCase):
    @patch("grievai_app.send_otp_email")
    def test_email_normalization_and_domain_restriction(self, mock_send_email):
        """Verify emails are normalized (.strip().lower()) and non-college domains rejected."""
        csrf = self.get_csrf_token()

        # Valid college domain with uppercase and surrounding whitespace
        resp = self.client.post("/send_otp", data={
            "email": "  Student.Ragging@VelHighTech.com  ",
            "csrf_token": csrf
        }, follow_redirects=True)

        self.assertEqual(resp.status_code, 200)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("email"), "student.ragging@velhightech.com")
        self.assertTrue(mock_send_email.called)

        # Unauthorized domain (e.g., gmail.com)
        resp_unauth = self.client.post("/send_otp", data={
            "email": "intruder@gmail.com",
            "csrf_token": csrf
        })
        self.assertIn(b"Invalid Email Domain", resp_unauth.data)

    @patch("grievai_app.send_otp_email")
    def test_otp_expiry_after_60_seconds(self, mock_send_email):
        """Verify OTP is accepted within 60s and strictly rejected after 60s."""
        csrf = self.get_csrf_token()

        # Send OTP
        self.client.post("/send_otp", data={
            "email": "test@velhightech.com",
            "csrf_token": csrf
        })

        with self.client.session_transaction() as sess:
            stored_otp = sess.get("otp")
            # Simulate timestamp 65 seconds in the past
            sess["otp_time"] = time.time() - 65

        # Attempt verification with expired OTP
        resp = self.client.post("/verify_otp", data={
            "otp": stored_otp,
            "csrf_token": csrf
        }, follow_redirects=True)

        self.assertIn(b"Verification code has expired", resp.data)
        with self.client.session_transaction() as sess:
            self.assertIsNone(sess.get("verified"))
            self.assertIsNone(sess.get("otp"))

    @patch("grievai_app.send_otp_email")
    def test_otp_single_use_invalidation(self, mock_send_email):
        """Verify OTP cannot be reused once verified."""
        csrf = self.get_csrf_token()

        self.client.post("/send_otp", data={
            "email": "test@velhightech.com",
            "csrf_token": csrf
        })

        with self.client.session_transaction() as sess:
            stored_otp = sess.get("otp")

        # First verification succeeds
        resp1 = self.client.post("/verify_otp", data={
            "otp": stored_otp,
            "csrf_token": csrf
        }, follow_redirects=False)
        self.assertEqual(resp1.status_code, 302)
        self.assertIn("/student_dashboard", resp1.headers.get("Location", ""))

        # OTP is now invalidated in session
        with self.client.session_transaction() as sess:
            self.assertTrue(sess.get("verified"))
            self.assertIsNone(sess.get("otp"))

        # Reusing the same OTP must fail
        resp2 = self.client.post("/verify_otp", data={
            "otp": stored_otp,
            "csrf_token": csrf
        }, follow_redirects=True)
        self.assertIn(b"No active verification code found", resp2.data)

    @patch("grievai_app.send_otp_email")
    def test_otp_resend_cooldown_abuse_prevention(self, mock_send_email):
        """Verify rapid resends are throttled within 30-second cooldown."""
        csrf = self.get_csrf_token()

        self.client.post("/send_otp", data={
            "email": "test@velhightech.com",
            "csrf_token": csrf
        })

        # Immediate resend attempt should hit 30s cooldown
        resp_resend = self.client.get("/resend_otp", follow_redirects=True)
        self.assertIn(b"Please wait", resp_resend.data)


class TestCSRFProtection(BaseSecurityTestCase):
    def test_post_without_csrf_is_rejected(self):
        """Verify state-changing POST without CSRF token returns 403 Forbidden."""
        resp = self.client.post("/send_otp", data={
            "email": "test@velhightech.com"
        })
        self.assertEqual(resp.status_code, 403)
        self.assertIn(b"Security Verification Failed", resp.data)

    def test_post_with_tampered_csrf_is_rejected(self):
        """Verify POST with invalid CSRF token returns 403 Forbidden."""
        self.get_csrf_token()
        resp = self.client.post("/send_otp", data={
            "email": "test@velhightech.com",
            "csrf_token": "tampered_invalid_token_12345"
        })
        self.assertEqual(resp.status_code, 403)

    @patch("grievai_app.send_otp_email")
    def test_post_with_valid_csrf_succeeds(self, mock_send):
        """Verify POST with matching CSRF token is accepted."""
        csrf = self.get_csrf_token()
        resp = self.client.post("/send_otp", data={
            "email": "test@velhightech.com",
            "csrf_token": csrf
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Enter 6-Digit OTP", resp.data)


class TestAuthenticationGuards(BaseSecurityTestCase):
    def test_student_dashboard_requires_verified_session(self):
        """Unauthenticated student accessing dashboard is redirected to login."""
        resp = self.client.get("/student_dashboard")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/student_login", resp.headers.get("Location", ""))

    def test_complaint_form_requires_verified_session(self):
        """Unauthenticated student accessing complaint form is redirected to login."""
        resp = self.client.get("/complaint_form")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/student_login", resp.headers.get("Location", ""))

    def test_predict_requires_verified_session(self):
        """Unauthenticated POST submission to /predict is blocked."""
        csrf = self.get_csrf_token()
        resp = self.client.post("/predict", data={
            "complaint": "Incident description",
            "csrf_token": csrf
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/student_login", resp.headers.get("Location", ""))

    def test_admin_routes_require_admin_session(self):
        """Unauthenticated visitor accessing admin routes is redirected to admin login."""
        for endpoint in ["/admin", "/admin/complaint/1", "/admin/logs"]:
            resp = self.client.get(endpoint)
            self.assertEqual(resp.status_code, 302)
            self.assertIn("/admin_login", resp.headers.get("Location", ""))


class TestComplaintOwnershipAndTracking(BaseSecurityTestCase):
    def test_student_dashboard_only_shows_own_complaints(self):
        """Verify student dashboard enforces data ownership isolation."""
        # Insert complaints for two different students
        c1 = Complaint(
            student_email="alice@velhightech.com",
            complaint_title="Alice Harassment Report",
            complaint_text="Details for Alice",
            department="Verbal or emotional harassment",
            status="Open"
        )
        c2 = Complaint(
            student_email="bob@velhightech.com",
            complaint_title="Bob Hazard Report",
            complaint_text="Details for Bob",
            department="Physical ragging",
            status="Open"
        )
        db.session.add_all([c1, c2])
        db.session.commit()

        # Log in as Alice
        with self.client.session_transaction() as sess:
            sess["verified"] = True
            sess["email"] = "alice@velhightech.com"

        resp = self.client.get("/student_dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Alice Harassment Report", resp.data)
        self.assertNotIn(b"Bob Hazard Report", resp.data)

    def test_tracking_requires_valid_token_or_ownership(self):
        """Verify tracking dual-authorization model."""
        raw_token = "ARC-SecretVerificationToken99"
        c = Complaint(
            student_email="alice@velhightech.com",
            complaint_title="Confidential Ragging Complaint",
            complaint_text="Confidential incident report text",
            department="Physical ragging",
            status="Open",
            tracking_token=hash_tracking_token(raw_token)
        )
        db.session.add(c)
        db.session.commit()
        complaint_id = c.id

        csrf = self.get_csrf_token()

        # 1. Tracking with valid token succeeds
        resp_valid = self.client.post("/track", data={
            "complaint_id": str(complaint_id),
            "tracking_token": raw_token,
            "csrf_token": csrf
        })
        self.assertEqual(resp_valid.status_code, 200)
        self.assertIn(b"Physical ragging", resp_valid.data)

        # 2. Tracking with invalid token fails with uniform generic error
        resp_invalid = self.client.post("/track", data={
            "complaint_id": str(complaint_id),
            "tracking_token": "WrongToken1234",
            "csrf_token": csrf
        })
        self.assertIn(b"Unable to verify incident ticket", resp_invalid.data)
        self.assertNotIn(b"Confidential incident report text", resp_invalid.data)


class TestAdminAuthenticationAndBruteForceLockout(BaseSecurityTestCase):
    def test_admin_login_success(self):
        """Verify admin login with valid credentials succeeds."""
        csrf = self.get_csrf_token()
        resp = self.client.post("/admin_login", data={
            "email": "admin@velhightech.com",
            "password": "AdminSecurePass123",
            "csrf_token": csrf
        }, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin", resp.headers.get("Location", ""))

    def test_admin_brute_force_lockout(self):
        """Verify 5 failed admin login attempts locks out the client."""
        csrf = self.get_csrf_token()

        for attempt in range(5):
            self.client.post("/admin_login", data={
                "email": "admin@velhightech.com",
                "password": "WrongPasswordAttempt",
                "csrf_token": csrf
            })

        # 6th attempt should be blocked by rate limit lockout
        resp_blocked = self.client.post("/admin_login", data={
            "email": "admin@velhightech.com",
            "password": "AdminSecurePass123",  # Even with correct password!
            "csrf_token": csrf
        }, follow_redirects=True)

        self.assertIn(b"Too many failed administrator login attempts", resp_blocked.data)


class TestSafeSMTPErrorHandling(BaseSecurityTestCase):
    @patch("grievai_app.send_otp_email")
    def test_smtp_failure_does_not_leak_technical_traceback(self, mock_send):
        """Verify SMTP exceptions return clean user message and do not expose stack trace."""
        mock_send.side_effect = Exception("SMTPConnectionRefused: [WinError 10061] No connection could be made")
        csrf = self.get_csrf_token()

        resp = self.client.post("/send_otp", data={
            "email": "student@velhightech.com",
            "csrf_token": csrf
        }, follow_redirects=True)

        # Must NOT expose raw exception
        self.assertNotIn(b"WinError 10061", resp.data)
        self.assertNotIn(b"SMTPConnectionRefused", resp.data)
        # Must show safe professional error message
        self.assertIn(b"Unable to send verification code", resp.data)


class TestOTPEmailContentAndDelivery(BaseSecurityTestCase):
    @patch("smtplib.SMTP")
    def test_otp_email_multipart_alternative_and_content(self, mock_smtp_class):
        """Verify OTP email contains both text/plain and text/html with required elements."""
        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__.return_value = mock_server

        test_email = "student@velhightech.com"
        test_otp = "739201"

        grievai_app.send_otp_email(test_email, test_otp)

        self.assertTrue(mock_server.send_message.called)
        sent_msg = mock_server.send_message.call_args[0][0]

        # 1. Subject verification
        self.assertEqual(sent_msg["Subject"], "Anti-Ragging Portal | Email Verification")
        self.assertEqual(sent_msg["To"], test_email)

        # 2. Multipart/alternative check
        self.assertTrue(sent_msg.is_multipart())
        self.assertEqual(sent_msg.get_content_type(), "multipart/alternative")
        parts = sent_msg.get_payload()
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0].get_content_type(), "text/plain")
        self.assertEqual(parts[1].get_content_type(), "text/html")

        plain_content = parts[0].get_payload(decode=True).decode("utf-8")
        html_content = parts[1].get_payload(decode=True).decode("utf-8")

        # 3. Dynamic OTP in both plain text and HTML
        self.assertIn("739201", plain_content)
        self.assertIn("739201", html_content)

        # 4. Expiry notice verification
        self.assertIn("This code expires in 1 minute.", plain_content)
        self.assertIn("This code expires in 1 minute.", html_content)

        # 5. Header and Heading verification
        self.assertIn("ANTI RAGGING PORTAL", html_content)
        self.assertIn("Verify Your Email Address", html_content)
        self.assertIn("ANTI-RAGGING PORTAL", plain_content)
        self.assertIn("VERIFY YOUR EMAIL ADDRESS", plain_content)

        # 6. Security notice verification
        self.assertIn("Do not share this", plain_content)
        self.assertIn("Security Notice:", html_content)
        self.assertIn("Do not share this", html_content)

        # 7. Footer verification
        self.assertIn("Student Safety | Confidentiality | Secure Access", plain_content)
        self.assertIn("Student Safety | Confidentiality | Secure Access", html_content)

        # 8. HTML structure check
        self.assertTrue(html_content.strip().startswith("<!DOCTYPE html>"))
        self.assertIn("</html>", html_content)

    @patch("smtplib.SMTP")
    def test_otp_email_dynamic_html_escaping(self, mock_smtp_class):
        """Verify dynamic values in OTP email are properly escaped."""
        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__.return_value = mock_server

        dangerous_otp = "<987654>&"
        grievai_app.send_otp_email("student@velhightech.com", dangerous_otp)

        sent_msg = mock_server.send_message.call_args[0][0]
        html_content = sent_msg.get_payload()[1].get_payload(decode=True).decode("utf-8")

        self.assertNotIn("<987654>&", html_content)
        self.assertIn("&lt;987654&gt;&amp;", html_content)


if __name__ == "__main__":
    unittest.main()
