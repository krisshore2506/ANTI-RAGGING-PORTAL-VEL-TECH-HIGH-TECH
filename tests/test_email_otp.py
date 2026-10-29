import os
import smtplib
import unittest
from unittest.mock import patch, MagicMock

# Set test environment variables before importing app
os.environ["SECRET_KEY"] = "test-secret-key-32-chars-minimum-length-for-testing"
os.environ["ADMIN_EMAIL"] = "admin@velhightech.com"
os.environ["ADMIN_PASSWORD"] = "AdminSecurePass123"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import grievai_app
from grievai_app import app, db
from backend.utils.email_sms import mask_email_address, _deliver_smtp_message


class TestEmailOTPSystem(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        app.config["SESSION_TYPE"] = "filesystem"
        app.config["CSRF_ENABLED"] = True

        self.app_context = app.app_context()
        self.app_context.push()
        db.create_all()

        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.app_context.pop()

    def get_csrf_token(self):
        response = self.client.get("/student_login")
        with self.client.session_transaction() as sess:
            token = sess.get("csrf_token")
        return token

    def test_mask_email_address_security(self):
        self.assertEqual(mask_email_address("edusort@gmail.com"), "ed***t@gmail.com")
        self.assertEqual(mask_email_address("ab@velhightech.com"), "a*@velhightech.com")
        self.assertEqual(mask_email_address(None), "[UNCONFIGURED]")
        self.assertEqual(mask_email_address("invalid"), "[UNCONFIGURED]")

    @patch("backend.routes.auth_routes._dispatch_otp_email")
    def test_send_otp_success_with_valid_email(self, mock_dispatch):
        csrf = self.get_csrf_token()
        resp = self.client.post("/send_otp", data={
            "email": "vh13838@velhightech.com",
            "csrf_token": csrf
        }, follow_redirects=False)

        self.assertEqual(resp.status_code, 302)
        self.assertIn("/verify_otp", resp.headers["Location"])
        self.assertTrue(mock_dispatch.called)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("email"), "vh13838@velhightech.com")
            self.assertTrue(bool(sess.get("otp")))

    def test_send_otp_invalid_domain_blocked(self):
        csrf = self.get_csrf_token()
        resp = self.client.post("/send_otp", data={
            "email": "student@gmail.com",
            "csrf_token": csrf
        }, follow_redirects=True)

        self.assertIn(b"Invalid Email Domain", resp.data)
        self.assertIn(b"Access Restricted", resp.data)

    @patch("backend.routes.auth_routes._dispatch_otp_email")
    def test_smtp_auth_error_handled_cleanly_without_secret_leak(self, mock_dispatch):
        mock_dispatch.side_effect = smtplib.SMTPAuthenticationError(535, b"5.7.8 BadCredentials")
        csrf = self.get_csrf_token()

        resp = self.client.post("/send_otp", data={
            "email": "vh13838@velhightech.com",
            "csrf_token": csrf
        }, follow_redirects=True)

        self.assertNotIn(b"BadCredentials", resp.data)
        self.assertNotIn(b"Traceback", resp.data)
        self.assertIn(b"Unable to send verification code due to an email authentication issue", resp.data)

    @patch("backend.routes.auth_routes._dispatch_otp_email")
    def test_smtp_connection_disconnect_handled_cleanly(self, mock_dispatch):
        mock_dispatch.side_effect = smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        csrf = self.get_csrf_token()

        resp = self.client.post("/send_otp", data={
            "email": "vh13838@velhightech.com",
            "csrf_token": csrf
        }, follow_redirects=True)

        self.assertNotIn(b"SMTPServerDisconnected", resp.data)
        self.assertNotIn(b"Traceback", resp.data)
        self.assertIn(b"Unable to send verification code", resp.data)

    @patch("backend.routes.auth_routes._dispatch_otp_email")
    def test_smtp_recipient_refused_handled_cleanly(self, mock_dispatch):
        mock_dispatch.side_effect = smtplib.SMTPRecipientsRefused({"vh13838@velhightech.com": (550, b"User unknown")})
        csrf = self.get_csrf_token()

        resp = self.client.post("/send_otp", data={
            "email": "vh13838@velhightech.com",
            "csrf_token": csrf
        }, follow_redirects=True)

        self.assertNotIn(b"User unknown", resp.data)
        self.assertNotIn(b"Traceback", resp.data)
        self.assertIn(b"Unable to send verification code to this email address", resp.data)

    @patch("backend.routes.auth_routes._dispatch_otp_email")
    def test_missing_smtp_credentials_handled_cleanly(self, mock_dispatch):
        mock_dispatch.side_effect = ValueError("SMTP_EMAIL or SMTP_PASSWORD is not configured.")
        csrf = self.get_csrf_token()

        resp = self.client.post("/send_otp", data={
            "email": "vh13838@velhightech.com",
            "csrf_token": csrf
        }, follow_redirects=True)

        self.assertNotIn(b"ValueError", resp.data)
        self.assertIn(b"Unable to send verification code: email service is not configured", resp.data)

    @patch("backend.utils.email_sms.DEV_SIMULATE_EMAIL", True)
    @patch("smtplib.SMTP")
    def test_dev_simulate_email_mode_skips_smtp_network(self, mock_smtp):
        grievai_app.send_otp_email("vh13838@velhightech.com", "123456")
        self.assertFalse(mock_smtp.called)

    @patch("backend.utils.email_sms.SMTP_USE_SSL", True)
    @patch("backend.utils.email_sms.SMTP_PORT", 465)
    @patch("smtplib.SMTP_SSL")
    def test_deliver_smtp_message_uses_ssl_on_port_465(self, mock_ssl):
        mock_server = MagicMock()
        mock_ssl.return_value.__enter__.return_value = mock_server

        from email.mime.text import MIMEText
        dummy_msg = MIMEText("test")
        dummy_msg["Subject"] = "Test"
        dummy_msg["From"] = "test@example.com"
        dummy_msg["To"] = "vh13838@velhightech.com"

        with patch("backend.utils.email_sms.SMTP_EMAIL", "test@example.com"), \
             patch("backend.utils.email_sms.SMTP_PASSWORD", "mockpass"):
            _deliver_smtp_message(dummy_msg)

        self.assertTrue(mock_ssl.called)
        self.assertTrue(mock_server.send_message.called)


if __name__ == "__main__":
    unittest.main()
