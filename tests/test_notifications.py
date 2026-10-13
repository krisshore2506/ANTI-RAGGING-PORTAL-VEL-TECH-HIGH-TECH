# -*- coding: utf-8 -*-
"""
Automated Test Suite for Two-Way Persistent Notification System and Reminder Engine
Anti-Ragging Portal - Vel Tech High Tech
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Ensure isolated test database
os.environ["SECRET_KEY"] = "test-secret-key-32-chars-minimum-length-for-testing"
os.environ["ADMIN_EMAIL"] = "admin@velhightech.com"
os.environ["ADMIN_PASSWORD"] = "AdminSecurePass123"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import grievai_app
from grievai_app import (
    app, db, Complaint, Notification,
    create_notification, process_pending_action_reminders, get_admin_pending_action_queue,
    REMINDER_OPEN_HOURS, REMINDER_IN_PROGRESS_HOURS,
    admin_failed_attempts, hash_tracking_token
)


class BaseNotificationTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        app.config["SESSION_TYPE"] = "filesystem"
        app.config["CSRF_ENABLED"] = True

        self.app_context = app.app_context()
        self.app_context.push()
        db.create_all()

        self.client = app.test_client()
        admin_failed_attempts.clear()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.app_context.pop()

    def get_csrf_token(self):
        """Helper to establish a session and retrieve the session CSRF token."""
        self.client.get("/student_login")
        with self.client.session_transaction() as sess:
            token = sess.get("csrf_token")
        return token

    def create_sample_complaint(self, email="student@velhightech.com", status="Open", timestamp=None):
        """Helper to create and commit a complaint."""
        c = Complaint(
            student_email=email,
            complaint_title="Hostel Coercion Report",
            complaint_text="Detailed complaint narrative.",
            department="Hostel hazing or coercion",
            incident_type="Hostel hazing or coercion",
            severity_level="High",
            incident_date_time="2026-10-10 20:00",
            incident_location="Hostel Block A",
            status=status,
            tracking_token=hash_tracking_token("CONFIDENTIAL-TEST-TOKEN"),
            committee_assigned="Anti-Ragging Squad",
            timestamp=timestamp or datetime.utcnow(),
            created_at=timestamp or datetime.utcnow()
        )
        db.session.add(c)
        db.session.commit()
        return c


class TestAdminNotifications(BaseNotificationTestCase):
    def test_new_complaint_creates_admin_notification(self):
        """1. Verify new complaint submission creates persistent admin notification without leaking tokens."""
        with self.client.session_transaction() as sess:
            sess["verified"] = True
            sess["email"] = "student@velhightech.com"
        csrf = self.get_csrf_token()

        response = self.client.post("/predict", data={
            "csrf_token": csrf,
            "title": "Harassment Incident",
            "complaint": "First year students were forced to perform chores in hostel block.",
            "incident_type": "Hostel hazing or coercion",
            "priority": "High",
            "incident_date_time": "2026-10-12 21:00",
            "location": "Hostel Block C Room 204",
            "location_details": "Room 204",
            "accused_details": "Senior student group",
            "witness_details": "Roommates",
            "is_confidential": "false",
            "accuracy_declaration": "true"
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)

        # Verify admin notification was created
        notif = Notification.query.filter_by(
            recipient_role="admin",
            notification_type="new_complaint"
        ).first()

        self.assertIsNotNone(notif)
        self.assertIn("New Incident Report Submitted", notif.title)
        self.assertFalse(notif.is_read)
        # Verify confidentiality: no raw tracking token or OTP in notification
        self.assertNotIn("CONFIDENTIAL-TEST-TOKEN", notif.message)
        self.assertNotIn("otp", notif.message.lower())

    def test_admin_pending_action_queue_detection(self):
        """Verify pending action queue correctly detects overdue Open and Under Review complaints."""
        now = datetime.utcnow()

        # Complaint 1: Open for 28 hours (exceeds 24h threshold)
        c1 = self.create_sample_complaint(
            email="s1@velhightech.com",
            status="Open",
            timestamp=now - timedelta(hours=28)
        )

        # Complaint 2: Open for 5 hours (within 24h threshold)
        c2 = self.create_sample_complaint(
            email="s2@velhightech.com",
            status="Open",
            timestamp=now - timedelta(hours=5)
        )

        # Complaint 3: Under Review for 80 hours (exceeds 72h threshold)
        c3 = self.create_sample_complaint(
            email="s3@velhightech.com",
            status="Under Review",
            timestamp=now - timedelta(hours=80)
        )

        # Complaint 4: Closed for 90 hours (must NOT appear)
        c4 = self.create_sample_complaint(
            email="s4@velhightech.com",
            status="Closed",
            timestamp=now - timedelta(hours=90)
        )

        queue = get_admin_pending_action_queue(now=now)
        queue_ids = [item["id"] for item in queue]

        self.assertIn(c1.id, queue_ids)
        self.assertNotIn(c2.id, queue_ids)
        self.assertIn(c3.id, queue_ids)
        self.assertNotIn(c4.id, queue_ids)

    def test_duplicate_reminders_suppressed_via_dedup_key(self):
        """Verify reminder engine is idempotent and suppresses duplicate notifications for the same interval."""
        now = datetime.utcnow()
        c = self.create_sample_complaint(
            status="Open",
            timestamp=now - timedelta(hours=26)
        )

        # First run: should generate 1 reminder
        count1 = process_pending_action_reminders(now=now)
        self.assertEqual(count1, 1)

        notifs = Notification.query.filter_by(
            recipient_role="admin",
            complaint_id=c.id,
            notification_type="pending_reminder"
        ).all()
        self.assertEqual(len(notifs), 1)
        self.assertEqual(notifs[0].dedup_key, f"reminder:open:{c.id}:24h")

        # Second run with same timeframe: should generate 0 new reminders
        count2 = process_pending_action_reminders(now=now)
        self.assertEqual(count2, 0)

        notifs_after = Notification.query.filter_by(
            recipient_role="admin",
            complaint_id=c.id,
            notification_type="pending_reminder"
        ).all()
        self.assertEqual(len(notifs_after), 1)

    def test_closed_complaints_excluded_from_reminders(self):
        """Verify complaints in Closed or Resolved status never receive pending action reminders."""
        now = datetime.utcnow()
        c_closed = self.create_sample_complaint(
            status="Closed",
            timestamp=now - timedelta(hours=100)
        )
        c_resolved = self.create_sample_complaint(
            status="Resolved",
            timestamp=now - timedelta(hours=100)
        )

        gen_count = process_pending_action_reminders(now=now)
        self.assertEqual(gen_count, 0)

        notifs = Notification.query.filter_by(recipient_role="admin").all()
        self.assertEqual(len(notifs), 0)


class TestStudentNotifications(BaseNotificationTestCase):
    @patch("grievai_app.send_status_update_email")
    def test_status_change_creates_student_notification(self, mock_email):
        """Verify successful status update on dashboard creates persistent student notification."""
        c = self.create_sample_complaint(email="student@velhightech.com", status="Open")

        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
        csrf = self.get_csrf_token()

        response = self.client.post("/admin", data={
            "csrf_token": csrf,
            "complaint_id": str(c.id),
            "status": "Under Review",
            "admin_remarks": "Assigned to committee review panel."
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)

        # Verify student notification was persisted
        notif = Notification.query.filter_by(
            recipient_role="student",
            recipient_email="student@velhightech.com",
            complaint_id=c.id
        ).first()

        self.assertIsNotNone(notif)
        self.assertIn("Under Review", notif.title)
        self.assertFalse(notif.is_read)
        self.assertIn(str(c.id), notif.message)

    @patch("grievai_app.send_status_update_email")
    def test_status_detail_page_creates_student_notification(self, mock_email):
        """Verify status update on complaint detail page creates persistent student notification."""
        c = self.create_sample_complaint(email="student@velhightech.com", status="Open")

        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
        csrf = self.get_csrf_token()

        response = self.client.post(f"/admin/complaint/{c.id}", data={
            "csrf_token": csrf,
            "status": "Action Taken",
            "admin_remarks": "Disciplinary proceedings initiated."
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)

        notif = Notification.query.filter_by(
            recipient_role="student",
            recipient_email="student@velhightech.com",
            complaint_id=c.id
        ).first()

        self.assertIsNotNone(notif)
        self.assertIn("Action Taken", notif.title)
        self.assertEqual(notif.notification_type, "status_update")

    @patch("grievai_app.send_status_update_email")
    def test_failed_status_change_creates_no_notification(self, mock_email):
        """Verify that a failed status change (invalid ID) creates no student notification."""
        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
        csrf = self.get_csrf_token()

        response = self.client.post("/admin", data={
            "csrf_token": csrf,
            "complaint_id": "999999",  # Non-existent ID
            "status": "Under Review",
            "admin_remarks": "Testing invalid ID."
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)

        # No notification should exist
        notifs = Notification.query.filter_by(recipient_role="student").all()
        self.assertEqual(len(notifs), 0)

    @patch("grievai_app.send_status_update_email")
    def test_email_failure_preserves_notification_and_status_update(self, mock_email):
        """Verify SMTP failure does not roll back status update or remove in-app notification."""
        mock_email.side_effect = Exception("SMTP Server Connection Timeout")

        c = self.create_sample_complaint(email="student@velhightech.com", status="Open")

        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
        csrf = self.get_csrf_token()

        response = self.client.post("/admin", data={
            "csrf_token": csrf,
            "complaint_id": str(c.id),
            "status": "Under Review",
            "admin_remarks": "Committee assigned despite email outage."
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)

        # Verify DB status update was preserved
        updated_c = db.session.get(Complaint, c.id)
        self.assertEqual(updated_c.status, "Under Review")

        # Verify in-app notification is still saved
        notif = Notification.query.filter_by(
            recipient_role="student",
            recipient_email="student@velhightech.com",
            complaint_id=c.id
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("Under Review", notif.title)

    def test_student_notification_ownership_isolation(self):
        """Verify student can only access their own notifications and cannot mark another student's notification."""
        notif_alice = create_notification(
            recipient_role="student",
            recipient_email="alice@velhightech.com",
            complaint_id=101,
            notification_type="status_update",
            title="Alice Notification",
            message="Message for Alice"
        )
        notif_bob = create_notification(
            recipient_role="student",
            recipient_email="bob@velhightech.com",
            complaint_id=102,
            notification_type="status_update",
            title="Bob Notification",
            message="Message for Bob"
        )

        # Login as Alice
        with self.client.session_transaction() as sess:
            sess["verified"] = True
            sess["email"] = "alice@velhightech.com"
        csrf = self.get_csrf_token()

        # Alice fetches her notifications
        resp = self.client.get("/student/notifications")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        notif_ids = [n["id"] for n in data["notifications"]]

        self.assertIn(notif_alice.id, notif_ids)
        self.assertNotIn(notif_bob.id, notif_ids)

        # Alice attempts to mark Bob's notification as read -> must return 404
        resp_attack = self.client.post(
            f"/student/notifications/{notif_bob.id}/read",
            headers={"X-CSRFToken": csrf},
            json={"csrf_token": csrf}
        )
        self.assertEqual(resp_attack.status_code, 404)

        # Verify Bob's notification remains unread
        bob_check = db.session.get(Notification, notif_bob.id)
        self.assertFalse(bob_check.is_read)


class TestNotificationSecurityAndCSRF(BaseNotificationTestCase):
    def test_unauthenticated_notification_access_blocked(self):
        """Verify unauthenticated requests cannot access student or admin notification endpoints."""
        csrf = self.get_csrf_token()

        # Student endpoints without login
        self.assertEqual(self.client.get("/student/notifications").status_code, 401)
        self.assertEqual(self.client.post("/student/notifications/1/read", json={"csrf_token": csrf}, headers={"X-CSRFToken": csrf}).status_code, 401)
        self.assertEqual(self.client.post("/student/notifications/mark_all_read", json={"csrf_token": csrf}, headers={"X-CSRFToken": csrf}).status_code, 401)

        # Admin endpoints without login
        self.assertEqual(self.client.get("/admin/notifications").status_code, 401)
        self.assertEqual(self.client.post("/admin/notifications/1/read", json={"csrf_token": csrf}, headers={"X-CSRFToken": csrf}).status_code, 401)
        self.assertEqual(self.client.post("/admin/notifications/mark_all_read", json={"csrf_token": csrf}, headers={"X-CSRFToken": csrf}).status_code, 401)
        self.assertEqual(self.client.get("/admin/pending_queue").status_code, 401)

    def test_csrf_protection_on_notification_read_and_mark_all(self):
        """Verify state-changing notification actions strictly enforce CSRF protection."""
        notif = create_notification(
            recipient_role="student",
            recipient_email="student@velhightech.com",
            complaint_id=201,
            notification_type="status_update",
            title="Status Update",
            message="Notification text"
        )

        with self.client.session_transaction() as sess:
            sess["verified"] = True
            sess["email"] = "student@velhightech.com"

        # Request WITHOUT CSRF token -> must be rejected with 403
        resp_no_csrf = self.client.post(
            f"/student/notifications/{notif.id}/read",
            json={}
        )
        self.assertEqual(resp_no_csrf.status_code, 403)

        # Request with valid CSRF token -> must succeed
        csrf = self.get_csrf_token()
        resp_valid_csrf = self.client.post(
            f"/student/notifications/{notif.id}/read",
            headers={"X-CSRFToken": csrf},
            json={"csrf_token": csrf}
        )
        self.assertEqual(resp_valid_csrf.status_code, 200)

        # Verify marked as read
        updated_notif = db.session.get(Notification, notif.id)
        self.assertTrue(updated_notif.is_read)
        self.assertIsNotNone(updated_notif.read_at)

    def test_student_and_admin_mark_all_read(self):
        """Verify mark all read functionality for both student and admin roles."""
        # Create student notifications
        create_notification("student", "T1", "M1", "status_update", recipient_email="student@velhightech.com")
        create_notification("student", "T2", "M2", "status_update", recipient_email="student@velhightech.com")

        # Create admin notifications
        create_notification("admin", "A1", "M1", "new_complaint")
        create_notification("admin", "A2", "M2", "pending_reminder")

        # Student mark all read
        with self.client.session_transaction() as sess:
            sess["verified"] = True
            sess["email"] = "student@velhightech.com"
        csrf = self.get_csrf_token()

        resp_student = self.client.post(
            "/student/notifications/mark_all_read",
            headers={"X-CSRFToken": csrf},
            json={"csrf_token": csrf}
        )
        self.assertEqual(resp_student.status_code, 200)
        self.assertEqual(resp_student.get_json()["marked_count"], 2)

        student_unread = Notification.query.filter_by(
            recipient_role="student",
            recipient_email="student@velhightech.com",
            is_read=False
        ).count()
        self.assertEqual(student_unread, 0)

        # Admin mark all read
        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
        csrf = self.get_csrf_token()

        resp_admin = self.client.post(
            "/admin/notifications/mark_all_read",
            headers={"X-CSRFToken": csrf},
            json={"csrf_token": csrf}
        )
        self.assertEqual(resp_admin.status_code, 200)
        self.assertEqual(resp_admin.get_json()["marked_count"], 2)

        admin_unread = Notification.query.filter_by(
            recipient_role="admin",
            is_read=False
        ).count()
        self.assertEqual(admin_unread, 0)


if __name__ == "__main__":
    unittest.main()
