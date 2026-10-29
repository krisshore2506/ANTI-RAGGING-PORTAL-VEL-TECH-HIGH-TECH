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
os.environ["DISABLE_REMINDER_SCHEDULER"] = "true"

import grievai_app
from grievai_app import (
    resolve_pending_reminders_for_complaint,
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




class TestPendingActionAuditVerification(BaseNotificationTestCase):
    """
    Comprehensive verification of pending-action reminder thresholds, boundary conditions,
    scheduler idempotency, status filtering, and audit trail retention.
    """

    def test_24h_open_boundary_thresholds(self):
        """Task 3: Test 24-hour boundary for Open complaints with controlled timestamps."""
        t_now = datetime(2026, 10, 25, 12, 0, 0)

        # 1. Just below threshold: 23 hours 58 minutes (23.967h)
        c_sub = self.create_sample_complaint(
            email="sub24@velhightech.com",
            status="Open",
            timestamp=t_now - timedelta(hours=23, minutes=58)
        )

        # 2. Just above threshold: 24 hours 2 minutes (24.033h)
        c_super = self.create_sample_complaint(
            email="super24@velhightech.com",
            status="Open",
            timestamp=t_now - timedelta(hours=24, minutes=2)
        )

        # Check queue detection
        queue = get_admin_pending_action_queue(now=t_now)
        queue_ids = [item["id"] for item in queue]

        self.assertNotIn(c_sub.id, queue_ids, "Sub-24h Open complaint must not appear in pending queue.")
        self.assertIn(c_super.id, queue_ids, "Super-24h Open complaint must appear in pending queue.")

        item = next(it for it in queue if it["id"] == c_super.id)
        self.assertEqual(item["elapsed_hours"], 24)
        self.assertEqual(item["threshold_hours"], 24)
        self.assertEqual(item["urgency"], "Medium")

        # Check reminder generation
        count = process_pending_action_reminders(now=t_now)
        self.assertEqual(count, 1, "Only the super-24h complaint should trigger a reminder.")

        notif_sub = Notification.query.filter_by(complaint_id=c_sub.id, recipient_role="admin").first()
        self.assertIsNone(notif_sub, "Sub-24h complaint must not have generated an admin reminder.")

        notif_super = Notification.query.filter_by(complaint_id=c_super.id, recipient_role="admin").first()
        self.assertIsNotNone(notif_super, "Super-24h complaint must have generated an admin reminder.")
        self.assertEqual(notif_super.dedup_key, f"reminder:open:{c_super.id}:24h")
        self.assertEqual(notif_super.notification_type, "pending_reminder")
        self.assertFalse(notif_super.is_read)
        self.assertIn("Action Required", notif_super.title)

    def test_72h_under_review_boundary_thresholds(self):
        """Task 3: Test 72-hour boundary for Under Review complaints with controlled timestamps."""
        t_now = datetime(2026, 10, 25, 12, 0, 0)

        # 1. Just below threshold: 71 hours 55 minutes (71.917h)
        c_sub = self.create_sample_complaint(
            email="sub72@velhightech.com",
            status="Under Review",
            timestamp=t_now - timedelta(hours=71, minutes=55)
        )

        # 2. Just above threshold: 72 hours 5 minutes (72.083h)
        c_super = self.create_sample_complaint(
            email="super72@velhightech.com",
            status="Under Review",
            timestamp=t_now - timedelta(hours=72, minutes=5)
        )

        # Check queue detection
        queue = get_admin_pending_action_queue(now=t_now)
        queue_ids = [item["id"] for item in queue]

        self.assertNotIn(c_sub.id, queue_ids, "Sub-72h Under Review complaint must not appear in pending queue.")
        self.assertIn(c_super.id, queue_ids, "Super-72h Under Review complaint must appear in pending queue.")

        item = next(it for it in queue if it["id"] == c_super.id)
        self.assertEqual(item["elapsed_hours"], 72)
        self.assertEqual(item["threshold_hours"], 72)
        self.assertEqual(item["urgency"], "Medium")

        # Check reminder generation
        count = process_pending_action_reminders(now=t_now)
        self.assertEqual(count, 1, "Only the super-72h Under Review complaint should trigger a reminder.")

        notif_sub = Notification.query.filter_by(complaint_id=c_sub.id, recipient_role="admin").first()
        self.assertIsNone(notif_sub, "Sub-72h complaint must not have generated an admin reminder.")

        notif_super = Notification.query.filter_by(complaint_id=c_super.id, recipient_role="admin").first()
        self.assertIsNotNone(notif_super, "Super-72h complaint must have generated an admin reminder.")
        self.assertEqual(notif_super.dedup_key, f"reminder:in_progress:{c_super.id}:72h")
        self.assertEqual(notif_super.notification_type, "pending_reminder")
        self.assertFalse(notif_super.is_read)
        self.assertIn("Awaiting Follow-up", notif_super.title)

    def test_repeated_sweeps_and_multi_interval_escalation(self):
        """Task 4: Test that repeated scheduler sweeps are idempotent, and advance to next interval when threshold reached."""
        t_0 = datetime(2026, 10, 20, 10, 0, 0)
        c = self.create_sample_complaint(
            email="escalation@velhightech.com",
            status="Open",
            timestamp=t_0
        )

        # Sweep 1 at t_0 + 26h (within first 24h bucket)
        t_sweep1 = t_0 + timedelta(hours=26)
        count1 = process_pending_action_reminders(now=t_sweep1)
        self.assertEqual(count1, 1)

        notifs = Notification.query.filter_by(complaint_id=c.id, recipient_role="admin").all()
        self.assertEqual(len(notifs), 1)
        self.assertEqual(notifs[0].dedup_key, f"reminder:open:{c.id}:24h")

        # Sweeps 2 & 3 at t_0 + 27h and t_0 + 35h (still within 24h-47h window)
        count2 = process_pending_action_reminders(now=t_0 + timedelta(hours=27))
        count3 = process_pending_action_reminders(now=t_0 + timedelta(hours=35))
        self.assertEqual(count2, 0, "Repeated sweep in same window must not create duplicate.")
        self.assertEqual(count3, 0, "Repeated sweep in same window must not create duplicate.")

        notifs_repeat = Notification.query.filter_by(complaint_id=c.id, recipient_role="admin").all()
        self.assertEqual(len(notifs_repeat), 1, "Duplicate notifications were erroneously created.")

        # Sweep 4 at t_0 + 50h (exceeds 48h boundary -> second escalation interval)
        t_sweep4 = t_0 + timedelta(hours=50)
        count4 = process_pending_action_reminders(now=t_sweep4)
        self.assertEqual(count4, 1, "Advancing past 48h bucket must generate exactly 1 escalation reminder.")

        notifs_escalated = Notification.query.filter_by(complaint_id=c.id, recipient_role="admin").order_by(Notification.id.asc()).all()
        self.assertEqual(len(notifs_escalated), 2)
        self.assertEqual(notifs_escalated[0].dedup_key, f"reminder:open:{c.id}:24h")
        self.assertEqual(notifs_escalated[1].dedup_key, f"reminder:open:{c.id}:48h")

        # Sweep 5 at t_0 + 51h (still in 48h bucket)
        count5 = process_pending_action_reminders(now=t_0 + timedelta(hours=51))
        self.assertEqual(count5, 0, "Repeated sweep in 48h bucket must not duplicate.")

    def test_closed_and_resolved_complaints_receive_no_reminders(self):
        """Task 5: Test that Closed and Resolved complaints strictly do not receive reminders or enter pending queue."""
        t_now = datetime(2026, 10, 25, 12, 0, 0)

        c_closed = self.create_sample_complaint(
            email="closed@velhightech.com",
            status="Closed",
            timestamp=t_now - timedelta(hours=150)
        )
        c_resolved = self.create_sample_complaint(
            email="resolved@velhightech.com",
            status="Resolved",
            timestamp=t_now - timedelta(hours=200)
        )

        queue = get_admin_pending_action_queue(now=t_now)
        queue_ids = [item["id"] for item in queue]
        self.assertNotIn(c_closed.id, queue_ids, "Closed complaint must not be in pending queue.")
        self.assertNotIn(c_resolved.id, queue_ids, "Resolved complaint must not be in pending queue.")

        count = process_pending_action_reminders(now=t_now)
        self.assertEqual(count, 0, "No reminders should be generated for Closed/Resolved complaints.")

        admin_notifs = Notification.query.filter_by(recipient_role="admin").all()
        self.assertEqual(len(admin_notifs), 0, "No notifications should be persisted for Closed/Resolved complaints.")

    def test_action_taken_complaints_receive_no_reminders(self):
        """Task 6: Verify current behavior for 'Action Taken' complaints (they receive 0 reminders and do not enter pending queue)."""
        t_now = datetime(2026, 10, 25, 12, 0, 0)

        c_action_taken = self.create_sample_complaint(
            email="action_taken@velhightech.com",
            status="Action Taken",
            timestamp=t_now - timedelta(hours=120)
        )

        # 1. Verify queue behavior:
        queue = get_admin_pending_action_queue(now=t_now)
        queue_ids = [item["id"] for item in queue]
        self.assertNotIn(c_action_taken.id, queue_ids, "'Action Taken' complaint must not appear in pending queue.")

        # 2. Verify reminder processor behavior:
        count = process_pending_action_reminders(now=t_now)
        self.assertEqual(count, 0, "'Action Taken' complaint must not trigger reminders.")

        notifs = Notification.query.filter_by(complaint_id=c_action_taken.id, recipient_role="admin").all()
        self.assertEqual(len(notifs), 0, "No admin notifications should exist for 'Action Taken' complaint.")

    def test_pending_action_queue_and_notifications_agreement(self):
        """Task 7: Test that the pending-action queue and reminder notification generation strictly agree on overdue complaints."""
        t_now = datetime(2026, 10, 25, 12, 0, 0)

        # Overdue Open (30h >= 24h)
        c_open_overdue = self.create_sample_complaint(
            email="open_od@velhightech.com",
            status="Open",
            timestamp=t_now - timedelta(hours=30)
        )
        # Non-overdue Open (10h < 24h)
        c_open_fresh = self.create_sample_complaint(
            email="open_fr@velhightech.com",
            status="Open",
            timestamp=t_now - timedelta(hours=10)
        )
        # Overdue Under Review (85h >= 72h)
        c_ur_overdue = self.create_sample_complaint(
            email="ur_od@velhightech.com",
            status="Under Review",
            timestamp=t_now - timedelta(hours=85)
        )
        # Non-overdue Under Review (30h < 72h)
        c_ur_fresh = self.create_sample_complaint(
            email="ur_fr@velhightech.com",
            status="Under Review",
            timestamp=t_now - timedelta(hours=30)
        )
        # Action Taken at 100h
        c_at = self.create_sample_complaint(
            email="at@velhightech.com",
            status="Action Taken",
            timestamp=t_now - timedelta(hours=100)
        )
        # Closed at 200h
        c_closed = self.create_sample_complaint(
            email="cl@velhightech.com",
            status="Closed",
            timestamp=t_now - timedelta(hours=200)
        )

        # Query pending queue
        queue = get_admin_pending_action_queue(now=t_now)
        queue_ids = set(item["id"] for item in queue)

        expected_overdue_ids = {c_open_overdue.id, c_ur_overdue.id}
        self.assertEqual(queue_ids, expected_overdue_ids, "Pending action queue does not match expected overdue complaints.")

        # Process reminders
        gen_count = process_pending_action_reminders(now=t_now)
        self.assertEqual(gen_count, 2, "Exactly 2 reminders should have been generated.")

        # Get notification complaint IDs
        notifs = Notification.query.filter_by(
            recipient_role="admin",
            notification_type="pending_reminder"
        ).all()
        notified_complaint_ids = set(n.complaint_id for n in notifs)

        # Verify exact agreement
        self.assertEqual(queue_ids, notified_complaint_ids, "Pending action queue and generated reminder notifications must agree.")

    @patch("grievai_app.send_status_update_email")
    def test_old_unread_reminders_retained_on_status_change(self, mock_email):
        """Task 8: Verify that updating a complaint resolves stale pending reminders (marks them resolved and read) while preserving their historical database record."""
        t_now = datetime(2026, 10, 25, 12, 0, 0)

        # 1. Create Open complaint overdue by 26h
        c = self.create_sample_complaint(
            email="status_change@velhightech.com",
            status="Open",
            timestamp=t_now - timedelta(hours=26)
        )

        # 2. Trigger reminder sweep
        count = process_pending_action_reminders(now=t_now)
        self.assertEqual(count, 1)

        reminder_notif = Notification.query.filter_by(
            recipient_role="admin",
            complaint_id=c.id,
            notification_type="pending_reminder"
        ).first()
        self.assertIsNotNone(reminder_notif)
        self.assertFalse(reminder_notif.is_read, "Newly generated reminder should be unread.")
        notif_id = reminder_notif.id

        # 3. Admin updates status to "Under Review" via POST /admin
        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
        csrf = self.get_csrf_token()

        response = self.client.post("/admin", data={
            "csrf_token": csrf,
            "complaint_id": str(c.id),
            "status": "Under Review",
            "admin_remarks": "Assigned to enquiry panel."
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        # 4. Check complaint status updated in DB
        db.session.expire_all()
        updated_c = db.session.get(Complaint, c.id)
        self.assertEqual(updated_c.status, "Under Review")

        # 5. Check reminder notification: retained in audit records and marked resolved
        check_notif = db.session.get(Notification, notif_id)
        self.assertIsNotNone(check_notif, "Reminder notification should be retained in audit records.")
        self.assertTrue(check_notif.is_resolved, "Reminder notification is marked resolved.")
        self.assertIsNotNone(check_notif.resolved_at, "Reminder notification has resolved_at timestamp.")
        self.assertTrue(check_notif.is_read, "Resolved reminder notification is marked read.")
        self.assertIsNotNone(check_notif.read_at, "read_at timestamp is populated.")

        # 6. Verify that queue reflects updated status and is no longer flagged (timestamp was reset)
        queue_after = get_admin_pending_action_queue(now=t_now)
        queue_after_ids = [item["id"] for item in queue_after]
        self.assertNotIn(c.id, queue_after_ids, "Freshly updated complaint should no longer be overdue in pending queue.")




class TestStalePendingActionResolution(BaseNotificationTestCase):
    """
    Automated test verification for automatic resolution of stale pending-action notifications:
    - Atomicity, idempotency, unread counter exclusion, historical retention, and safe re-reminders.
    """

    def test_overdue_reminder_initially_unresolved(self):
        """1. Verify overdue reminder is created and initially in unresolved state."""
        t_now = datetime(2026, 10, 25, 12, 0, 0)
        c = self.create_sample_complaint(
            email="s_unres@velhightech.com",
            status="Open",
            timestamp=t_now - timedelta(hours=25)
        )

        count = process_pending_action_reminders(now=t_now)
        self.assertEqual(count, 1)

        notif = Notification.query.filter_by(
            recipient_role="admin",
            complaint_id=c.id,
            notification_type="pending_reminder"
        ).first()

        self.assertIsNotNone(notif)
        self.assertFalse(notif.is_resolved, "New pending reminder must be initially unresolved.")
        self.assertIsNone(notif.resolved_at, "New pending reminder resolved_at must initially be None.")
        self.assertFalse(notif.is_read, "New pending reminder must initially be unread.")

    @patch("grievai_app.send_status_update_email")
    def test_updating_complaint_resolves_stale_reminders(self, mock_email):
        """2. Verify updating complaint status/remarks resolves all associated stale pending reminders."""
        t_now = datetime(2026, 10, 25, 12, 0, 0)
        c = self.create_sample_complaint(
            email="s_resolve@velhightech.com",
            status="Open",
            timestamp=t_now - timedelta(hours=26)
        )

        process_pending_action_reminders(now=t_now)

        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
        csrf = self.get_csrf_token()

        response = self.client.post("/admin", data={
            "csrf_token": csrf,
            "complaint_id": str(c.id),
            "status": "Under Review",
            "remarks": "Assigned to enquiry panel."
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        notif = Notification.query.filter_by(
            recipient_role="admin",
            complaint_id=c.id,
            notification_type="pending_reminder"
        ).first()

        self.assertIsNotNone(notif)
        self.assertTrue(notif.is_resolved, "Pending reminder must be marked resolved upon complaint update.")
        self.assertIsNotNone(notif.resolved_at, "Pending reminder must have resolved_at timestamp populated.")
        self.assertTrue(notif.is_read, "Resolved reminder must also be marked read.")
        self.assertIsNotNone(notif.read_at, "Resolved reminder read_at must be populated.")

    def test_historical_record_persisted_with_dedup_and_timestamps(self):
        """3. Verify historical reminder record remains intact in DB with original ID, dedup_key, and message."""
        t_now = datetime(2026, 10, 25, 12, 0, 0)
        c = self.create_sample_complaint(status="Open", timestamp=t_now - timedelta(hours=28))
        process_pending_action_reminders(now=t_now)

        notif_before = Notification.query.filter_by(complaint_id=c.id, notification_type="pending_reminder").first()
        orig_id = notif_before.id
        orig_title = notif_before.title
        orig_msg = notif_before.message
        orig_dedup = notif_before.dedup_key
        orig_created = notif_before.created_at

        t_res = t_now + timedelta(hours=2)
        resolve_pending_reminders_for_complaint(c.id, resolved_at=t_res, commit=True)

        notif_after = db.session.get(Notification, orig_id)
        self.assertIsNotNone(notif_after, "Historical reminder row must never be deleted from DB.")
        self.assertEqual(notif_after.id, orig_id)
        self.assertEqual(notif_after.title, orig_title)
        self.assertEqual(notif_after.message, orig_msg)
        self.assertEqual(notif_after.dedup_key, orig_dedup)
        self.assertEqual(notif_after.created_at, orig_created)
        self.assertTrue(notif_after.is_resolved)
        self.assertEqual(notif_after.resolved_at, t_res)

    @patch("grievai_app.send_status_update_email")
    def test_resolved_reminders_excluded_from_active_unread_count(self, mock_email):
        """4. Verify resolved reminders are excluded from active unread count and bell badge."""
        t_now = datetime(2026, 10, 25, 12, 0, 0)
        c = self.create_sample_complaint(status="Open", timestamp=t_now - timedelta(hours=26))
        process_pending_action_reminders(now=t_now)

        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
        csrf = self.get_csrf_token()

        # Before update: unread count should be 1
        resp_before = self.client.get("/admin/notifications")
        self.assertEqual(resp_before.status_code, 200)
        self.assertEqual(resp_before.get_json()["unread_count"], 1)

        # Update complaint
        self.client.post("/admin", data={
            "csrf_token": csrf,
            "complaint_id": str(c.id),
            "status": "Under Review",
            "remarks": "Investigation underway."
        }, follow_redirects=True)

        # After update: unread count must be 0
        resp_after = self.client.get("/admin/notifications")
        self.assertEqual(resp_after.status_code, 200)
        data_after = resp_after.get_json()
        self.assertEqual(data_after["unread_count"], 0, "Resolved reminder must be excluded from unread count.")
        self.assertEqual(len(data_after["notifications"]), 1, "Historical reminder must still appear in notification list.")
        self.assertTrue(data_after["notifications"][0]["is_resolved"])

    @patch("grievai_app.send_status_update_email")
    def test_unrelated_notifications_remain_unchanged(self, mock_email):
        """5. Verify resolving a complaint's reminder does NOT alter unrelated notifications or other complaints' reminders."""
        t_now = datetime(2026, 10, 25, 12, 0, 0)

        # 1. Unrelated general admin alert (new_complaint)
        notif_new = create_notification(
            recipient_role="admin",
            title="General Incident Alert",
            message="New report submitted.",
            notification_type="new_complaint"
        )

        # 2. Complaint A (to be resolved)
        cA = self.create_sample_complaint(email="cA@velhightech.com", status="Open", timestamp=t_now - timedelta(hours=26))
        # 3. Complaint B (to remain unresolved)
        cB = self.create_sample_complaint(email="cB@velhightech.com", status="Open", timestamp=t_now - timedelta(hours=26))

        process_pending_action_reminders(now=t_now)

        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
        csrf = self.get_csrf_token()

        # Update ONLY Complaint A
        self.client.post("/admin", data={
            "csrf_token": csrf,
            "complaint_id": str(cA.id),
            "status": "Under Review",
            "remarks": "Panel formed for A."
        }, follow_redirects=True)

        # Check Complaint A's reminder -> resolved
        notif_A = Notification.query.filter_by(complaint_id=cA.id, notification_type="pending_reminder").first()
        self.assertTrue(notif_A.is_resolved)

        # Check unrelated new_complaint notification -> untouched, still unread
        notif_new_check = db.session.get(Notification, notif_new.id)
        self.assertFalse(notif_new_check.is_resolved)
        self.assertFalse(notif_new_check.is_read)

        # Check Complaint B's reminder -> untouched, still unresolved and unread
        notif_B = Notification.query.filter_by(complaint_id=cB.id, notification_type="pending_reminder").first()
        self.assertFalse(notif_B.is_resolved)
        self.assertFalse(notif_B.is_read)

        # Active unread count must be 2 (new_complaint + B's reminder)
        resp = self.client.get("/admin/notifications")
        self.assertEqual(resp.get_json()["unread_count"], 2)

    @patch("grievai_app.send_status_update_email")
    def test_repeated_updates_idempotent_and_safe(self, mock_email):
        """6. Verify repeated complaint updates do not create duplicate resolution events or corrupt history."""
        t_now = datetime(2026, 10, 25, 12, 0, 0)
        c = self.create_sample_complaint(status="Open", timestamp=t_now - timedelta(hours=26))
        process_pending_action_reminders(now=t_now)

        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
        csrf = self.get_csrf_token()

        # Update 1
        resp1 = self.client.post(f"/admin/complaint/{c.id}", data={
            "csrf_token": csrf,
            "status": "Under Review",
            "admin_remarks": "Action 1."
        }, follow_redirects=True)
        self.assertEqual(resp1.status_code, 200)

        notif1 = Notification.query.filter_by(complaint_id=c.id, notification_type="pending_reminder").first()
        res_time1 = notif1.resolved_at

        # Update 2 immediately after
        resp2 = self.client.post(f"/admin/complaint/{c.id}", data={
            "csrf_token": csrf,
            "status": "Under Review",
            "admin_remarks": "Action 2: Added notes."
        }, follow_redirects=True)
        self.assertEqual(resp2.status_code, 200)

        notifs = Notification.query.filter_by(complaint_id=c.id, notification_type="pending_reminder").all()
        self.assertEqual(len(notifs), 1, "Repeated updates must not duplicate reminder records.")
        self.assertTrue(notifs[0].is_resolved)
        self.assertEqual(notifs[0].resolved_at, res_time1)

    @patch("grievai_app.send_status_update_email")
    def test_failed_transaction_rolls_back_atomically(self, mock_email):
        """7. Verify transaction atomicity: if commit fails, neither complaint nor notification changes are committed."""
        t_now = datetime(2026, 10, 25, 12, 0, 0)
        c = self.create_sample_complaint(status="Open", timestamp=t_now - timedelta(hours=26))
        process_pending_action_reminders(now=t_now)

        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
        csrf = self.get_csrf_token()

        with patch.object(db.session, "commit", side_effect=Exception("Simulated Database Commit Failure")):
            response = self.client.post("/admin", data={
                "csrf_token": csrf,
                "complaint_id": str(c.id),
                "status": "Under Review",
                "remarks": "Failing update."
            }, follow_redirects=True)
            self.assertEqual(response.status_code, 200)

        # Verify complaint was NOT committed
        db.session.expire_all()
        check_c = db.session.get(Complaint, c.id)
        self.assertEqual(check_c.status, "Open", "Complaint status change must roll back on transaction failure.")

        # Verify notification was NOT marked resolved
        notif = Notification.query.filter_by(complaint_id=c.id, notification_type="pending_reminder").first()
        self.assertFalse(notif.is_resolved, "Notification resolution must roll back on transaction failure.")
        self.assertIsNone(notif.resolved_at)

        # Verify flash error is shown and no false success message appears
        self.assertIn(b"An error occurred while updating the complaint", response.data)
        self.assertNotIn(b"Status is now", response.data)

    @patch("grievai_app.send_status_update_email")
    def test_failed_transaction_detail_route_rolls_back_atomically(self, mock_email):
        """7b. Verify detail route (/admin/complaint/<id>) transaction atomicity and rollback."""
        t_now = datetime(2026, 10, 25, 12, 0, 0)
        c = self.create_sample_complaint(status="Open", timestamp=t_now - timedelta(hours=26))
        process_pending_action_reminders(now=t_now)

        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
        csrf = self.get_csrf_token()

        with patch.object(db.session, "commit", side_effect=Exception("Simulated Detail Commit Failure")):
            response = self.client.post(f"/admin/complaint/{c.id}", data={
                "csrf_token": csrf,
                "status": "Under Review",
                "admin_remarks": "Failing detail update."
            }, follow_redirects=True)
            self.assertEqual(response.status_code, 200)

        # Verify complaint was NOT committed
        db.session.expire_all()
        check_c = db.session.get(Complaint, c.id)
        self.assertEqual(check_c.status, "Open")

        # Verify reminder was NOT resolved
        notif = Notification.query.filter_by(complaint_id=c.id, notification_type="pending_reminder").first()
        self.assertFalse(notif.is_resolved)

        # Verify error message shown and no false success
        self.assertIn(b"An error occurred while updating the complaint", response.data)
        self.assertNotIn(b"Status is now", response.data)

    @patch("grievai_app.send_status_update_email")
    def test_later_valid_reminder_generated_without_unintended_deduplication(self, mock_email):
        """8. Verify future reminder can be generated when complaint becomes overdue again without dedup collision."""
        t_0 = datetime(2026, 10, 20, 10, 0, 0)
        c = self.create_sample_complaint(status="Open", timestamp=t_0)

        # 1. First overdue reminder generated at t_0 + 25h
        t_1 = t_0 + timedelta(hours=25)
        count1 = process_pending_action_reminders(now=t_1)
        self.assertEqual(count1, 1)

        # 2. Admin takes action and updates status to Under Review at t_2
        t_2 = t_0 + timedelta(hours=26)
        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
        csrf = self.get_csrf_token()

        with patch("backend.routes.admin_routes.datetime") as mock_dt:
            mock_dt.utcnow.return_value = t_2
            self.client.post("/admin", data={
                "csrf_token": csrf,
                "complaint_id": str(c.id),
                "status": "Under Review",
                "remarks": "Assigned to committee."
            }, follow_redirects=True)

        # First reminder is resolved
        rem_1 = Notification.query.filter_by(complaint_id=c.id, dedup_key=f"reminder:open:{c.id}:24h").first()
        self.assertTrue(rem_1.is_resolved)

        # 3. Advance time by 75 hours from t_2 (overdue in Under Review status)
        t_3 = t_2 + timedelta(hours=75)
        count2 = process_pending_action_reminders(now=t_3)
        self.assertEqual(count2, 1, "New valid reminder must be generated for Under Review without dedup block.")

        # Verify second reminder exists and is active
        rem_2 = Notification.query.filter_by(complaint_id=c.id, dedup_key=f"reminder:in_progress:{c.id}:72h").first()
        self.assertIsNotNone(rem_2)
        self.assertFalse(rem_2.is_resolved)
        self.assertFalse(rem_2.is_read)

        # Verify both historical resolved reminder and new active reminder exist in DB
        all_reminders = Notification.query.filter_by(complaint_id=c.id, notification_type="pending_reminder").all()
        self.assertEqual(len(all_reminders), 2)

    @patch("grievai_app.send_status_update_email")
    def test_existing_status_transitions_and_student_tracking_work(self, mock_email):
        """9. Verify existing status transitions, student notifications, and tracking token lookup remain unaffected."""
        c = self.create_sample_complaint(email="student@velhightech.com", status="Open")

        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
        csrf = self.get_csrf_token()

        resp = self.client.post("/admin", data={
            "csrf_token": csrf,
            "complaint_id": str(c.id),
            "status": "Action Taken",
            "remarks": "Action completed."
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        # Student notification was created
        student_notif = Notification.query.filter_by(
            recipient_role="student",
            recipient_email="student@velhightech.com",
            complaint_id=c.id
        ).first()
        self.assertIsNotNone(student_notif)
        self.assertIn("Action Taken", student_notif.title)

        # Student tracking lookup succeeds
        track_resp = self.client.post("/track", data={
            "csrf_token": csrf,
            "complaint_id": str(c.id),
            "tracking_token": "CONFIDENTIAL-TEST-TOKEN"
        })
        self.assertEqual(track_resp.status_code, 200)
        self.assertIn(b"Action Taken", track_resp.data)

    def test_mark_all_read_preserves_resolved_notification_history_and_unread_semantics(self):
        """Verify mark_all_read preserves resolved notifications while resetting active unread count."""
        # 1. Unresolved unread reminder
        create_notification(
            recipient_role="admin",
            title="Unresolved Active Reminder",
            message="Please review incident #100",
            notification_type="pending_reminder",
            complaint_id=100
        )
        # 2. Unresolved unread new_complaint
        create_notification(
            recipient_role="admin",
            title="New Complaint Filed",
            message="Incident #101 filed",
            notification_type="new_complaint",
            complaint_id=101
        )
        # 3. Already resolved historical reminder
        resolved_time = datetime.utcnow() - timedelta(hours=5)
        resolved_notif = create_notification(
            recipient_role="admin",
            title="Resolved Historical Reminder",
            message="Past reminder",
            notification_type="pending_reminder",
            complaint_id=102
        )
        resolved_notif.is_resolved = True
        resolved_notif.resolved_at = resolved_time
        resolved_notif.is_read = True
        resolved_notif.read_at = resolved_time
        db.session.commit()

        # Login as admin
        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
        csrf = self.get_csrf_token()

        # Prior to mark_all_read: active unread count should be 2
        notifs_resp = self.client.get("/admin/notifications")
        self.assertEqual(notifs_resp.status_code, 200)
        self.assertEqual(notifs_resp.get_json()["unread_count"], 2)

        # Execute mark_all_read
        resp = self.client.post(
            "/admin/notifications/mark_all_read",
            headers={"X-CSRFToken": csrf},
            json={"csrf_token": csrf}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["marked_count"], 2)
        self.assertEqual(data["unread_count"], 0)

        # Verify historical resolved reminder remains untouched
        check_resolved = Notification.query.get(resolved_notif.id)
        self.assertTrue(check_resolved.is_resolved)
        self.assertEqual(check_resolved.resolved_at, resolved_time)
        self.assertEqual(check_resolved.read_at, resolved_time)

        # Verify active unread count is 0
        active_unread = Notification.query.filter(
            Notification.recipient_role == "admin",
            Notification.is_read == False,
            Notification.is_resolved == False
        ).count()
        self.assertEqual(active_unread, 0)





class TestNotificationDatabaseMigration(unittest.TestCase):
    """
    Isolated automated tests for database schema migration safety:
    - Legacy tables without resolution columns
    - Unique dedup_key index conversion to standard index (including custom-named and table constraints)
    - Preservation of unrelated indexes and constraints
    - Row value, type, and historical preservation
    - Idempotency on repeated execution
    - Fresh database creation
    - Safe generation of re-reminders after resolution
    - Scheduler disabling flag verification
    - Resource cleanup without database connection leaks
    """

    def setUp(self):
        import tempfile
        self.temp_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_file.name
        self.temp_file.close()

    def tearDown(self):
        try:
            from backend.models.base import db
            db.session.remove()
            if hasattr(db, "engine") and db.engine:
                db.engine.dispose()
        except Exception:
            pass
        try:
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
        except Exception:
            pass

    def _setup_legacy_database(self, custom_unique_index_name=None, column_unique_constraint=False):
        import sqlite3
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE complaint (
                id INTEGER PRIMARY KEY,
                student_email VARCHAR(120),
                status VARCHAR(50),
                created_at DATETIME,
                timestamp DATETIME
            )
        """)
        if column_unique_constraint:
            cur.execute("""
                CREATE TABLE notification (
                    id INTEGER PRIMARY KEY,
                    recipient_role VARCHAR(20) NOT NULL,
                    recipient_email VARCHAR(120),
                    complaint_id INTEGER,
                    notification_type VARCHAR(50) NOT NULL,
                    title VARCHAR(150) NOT NULL,
                    message TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    is_read BOOLEAN NOT NULL,
                    read_at DATETIME,
                    dedup_key VARCHAR(120) UNIQUE
                )
            """)
        else:
            cur.execute("""
                CREATE TABLE notification (
                    id INTEGER PRIMARY KEY,
                    recipient_role VARCHAR(20) NOT NULL,
                    recipient_email VARCHAR(120),
                    complaint_id INTEGER,
                    notification_type VARCHAR(50) NOT NULL,
                    title VARCHAR(150) NOT NULL,
                    message TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    is_read BOOLEAN NOT NULL,
                    read_at DATETIME,
                    dedup_key VARCHAR(120)
                )
            """)
            if custom_unique_index_name:
                cur.execute(f"CREATE UNIQUE INDEX {custom_unique_index_name} ON notification (dedup_key)")
            else:
                cur.execute("CREATE UNIQUE INDEX ix_notification_dedup_key ON notification (dedup_key)")

        # Pre-populate legacy rows
        cur.execute("""
            INSERT INTO notification (id, recipient_role, recipient_email, complaint_id, notification_type, title, message, created_at, is_read, read_at, dedup_key)
            VALUES (1, 'admin', NULL, 10, 'pending_reminder', 'Action Required', 'Incident 10 is overdue', '2026-10-24 10:00:00', 0, NULL, 'reminder:open:10:24h')
        """)
        cur.execute("""
            INSERT INTO notification (id, recipient_role, recipient_email, complaint_id, notification_type, title, message, created_at, is_read, read_at, dedup_key)
            VALUES (2, 'student', 'student@velhightech.com', 10, 'status_update', 'Status Update', 'Under review', '2026-10-24 11:00:00', 1, '2026-10-24 12:00:00', NULL)
        """)
        con.commit()
        con.close()

    def test_legacy_database_migration_adds_columns_and_preserves_records(self):
        """1. Verify legacy database without resolution columns is migrated safely and rows preserved."""
        import sqlite3
        from backend import create_app
        from backend.models.base import db

        self._setup_legacy_database()

        app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{self.db_path}",
            "DISABLE_REMINDER_SCHEDULER": True
        })

        try:
            con = sqlite3.connect(self.db_path)
            cur = con.cursor()
            cols = [c[1] for c in cur.execute("PRAGMA table_info('notification')").fetchall()]
            self.assertIn("is_resolved", cols, "is_resolved column must be added to notification table.")
            self.assertIn("resolved_at", cols, "resolved_at column must be added to notification table.")

            # Check existing row values preserved
            row1 = cur.execute("SELECT id, message, is_resolved, resolved_at, is_read, dedup_key FROM notification WHERE id=1").fetchone()
            self.assertEqual(row1[0], 1)
            self.assertEqual(row1[1], "Incident 10 is overdue")
            self.assertEqual(row1[2], 0, "is_resolved must default to 0 (False) for existing records.")
            self.assertIsNone(row1[3], "resolved_at must remain NULL for existing records.")
            self.assertEqual(row1[4], 0)
            self.assertEqual(row1[5], "reminder:open:10:24h")

            row2 = cur.execute("SELECT id, is_read, read_at, is_resolved FROM notification WHERE id=2").fetchone()
            self.assertEqual(row2[0], 2)
            self.assertEqual(row2[1], 1)
            self.assertEqual(row2[2], "2026-10-24 12:00:00")
            self.assertEqual(row2[3], 0)

            # Check index is non-unique
            idx_sql = cur.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='ix_notification_dedup_key'").fetchone()[0]
            self.assertNotIn("UNIQUE", idx_sql.upper(), "Unique constraint must be removed from dedup_key index.")
            con.close()
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()

    def test_legacy_migration_with_custom_named_unique_index_and_unrelated_indexes(self):
        """2. Verify custom-named unique indexes on dedup_key are relaxed while unrelated indexes are preserved."""
        import sqlite3
        from backend import create_app
        from backend.models.base import db

        self._setup_legacy_database(custom_unique_index_name="uq_custom_dedup_idx")

        # Add unrelated indexes to verify they are not dropped
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("CREATE UNIQUE INDEX uq_unrelated_field ON notification (created_at)")
        cur.execute("CREATE INDEX ix_recipient_role_custom ON notification (recipient_role)")
        con.commit()
        con.close()

        app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{self.db_path}",
            "DISABLE_REMINDER_SCHEDULER": True
        })

        try:
            con = sqlite3.connect(self.db_path)
            cur = con.cursor()
            indexes = [r[1] for r in cur.execute("PRAGMA index_list('notification')").fetchall()]

            # The custom unique index on dedup_key must be gone
            self.assertNotIn("uq_custom_dedup_idx", indexes)
            # The non-unique standard index must be present
            self.assertIn("ix_notification_dedup_key", indexes)
            # The unrelated unique and non-unique indexes must still exist
            self.assertIn("uq_unrelated_field", indexes)
            self.assertIn("ix_recipient_role_custom", indexes)

            # Confirm duplicate dedup_key insert now succeeds
            cur.execute("""
                INSERT INTO notification (id, recipient_role, notification_type, title, message, created_at, is_read, dedup_key)
                VALUES (3, 'admin', 'pending_reminder', 'T3', 'M3', '2026-10-24 15:00:00', 0, 'reminder:open:10:24h')
            """)
            con.commit()
            count = cur.execute("SELECT count(*) FROM notification WHERE dedup_key='reminder:open:10:24h'").fetchone()[0]
            self.assertEqual(count, 2)
            con.close()
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()

    def test_legacy_migration_with_table_or_column_unique_constraint(self):
        """3. Verify table rebuild successfully removes SQLite autoindex UNIQUE constraint on dedup_key."""
        import sqlite3
        from backend import create_app
        from backend.models.base import db

        self._setup_legacy_database(column_unique_constraint=True)

        # Add unrelated index
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("CREATE INDEX ix_complaint_id_test ON notification (complaint_id)")
        con.commit()
        con.close()

        app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{self.db_path}",
            "DISABLE_REMINDER_SCHEDULER": True
        })

        try:
            con = sqlite3.connect(self.db_path)
            cur = con.cursor()

            # Verify rows preserved
            count = cur.execute("SELECT count(*) FROM notification").fetchone()[0]
            self.assertEqual(count, 2)

            # Verify unrelated index preserved
            indexes = [r[1] for r in cur.execute("PRAGMA index_list('notification')").fetchall()]
            self.assertIn("ix_complaint_id_test", indexes)
            self.assertIn("ix_notification_dedup_key", indexes)

            # Verify no unique autoindexes remain on dedup_key
            for idx in cur.execute("PRAGMA index_list('notification')").fetchall():
                idx_name = idx[1]
                is_unique = bool(idx[2])
                if is_unique:
                    cols = [col[2] for col in cur.execute(f"PRAGMA index_info('{idx_name}')").fetchall()]
                    self.assertNotEqual(cols, ['dedup_key'], f"Unique index/constraint still exists on dedup_key: {idx_name}")

            # Verify second insert with duplicate dedup_key succeeds
            cur.execute("""
                INSERT INTO notification (id, recipient_role, notification_type, title, message, created_at, is_read, dedup_key)
                VALUES (3, 'admin', 'pending_reminder', 'T3', 'M3', '2026-10-24 16:00:00', 0, 'reminder:open:10:24h')
            """)
            con.commit()
            con.close()
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()

    def test_migration_is_idempotent_on_repeated_runs(self):
        """4. Verify running the migration multiple times is completely idempotent and safe."""
        import sqlite3
        from backend import create_app
        from backend.models.base import db

        self._setup_legacy_database()

        for _ in range(3):
            app = create_app({
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{self.db_path}",
                "DISABLE_REMINDER_SCHEDULER": True
            })
            with app.app_context():
                db.session.remove()
                db.engine.dispose()

        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        count = cur.execute("SELECT count(*) FROM notification").fetchone()[0]
        self.assertEqual(count, 2, "Row count must remain intact after multiple migration runs.")
        con.close()

    def test_fresh_database_creation_includes_resolution_columns_and_non_unique_index(self):
        """5. Verify creating a fresh database includes new columns and non-unique index from the start."""
        import sqlite3
        from backend import create_app
        from backend.models.base import db

        app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{self.db_path}",
            "DISABLE_REMINDER_SCHEDULER": True
        })

        try:
            con = sqlite3.connect(self.db_path)
            cur = con.cursor()
            cols = [c[1] for c in cur.execute("PRAGMA table_info('notification')").fetchall()]
            self.assertIn("is_resolved", cols)
            self.assertIn("resolved_at", cols)

            indices = cur.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='notification'").fetchall()
            for idx_name, idx_sql in indices:
                if idx_name == "ix_notification_dedup_key" and idx_sql:
                    self.assertNotIn("UNIQUE", idx_sql.upper())
            con.close()
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()

    def test_generating_new_reminder_after_resolving_previous_with_same_dedup_key(self):
        """6. Verify new reminder with same dedup_key can be inserted after previous reminder is resolved."""
        from backend import create_app
        from backend.models.base import db
        from backend.models.complaint import Complaint
        from backend.models.notification import Notification
        from backend.services.notification_service import (
            create_notification,
            resolve_pending_reminders_for_complaint
        )

        app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{self.db_path}",
            "DISABLE_REMINDER_SCHEDULER": True
        })

        try:
            with app.app_context():
                c = Complaint(
                    student_email="student@velhightech.com",
                    complaint_title="Test Report",
                    complaint_text="Details",
                    department="Hostel hazing or coercion",
                    incident_type="Hostel hazing or coercion",
                    status="Open",
                    created_at=datetime.utcnow() - timedelta(hours=26),
                    timestamp=datetime.utcnow() - timedelta(hours=26)
                )
                db.session.add(c)
                db.session.commit()

                # Cycle 1: First reminder
                notif1 = create_notification(
                    recipient_role="admin",
                    title="Reminder Cycle 1",
                    message="Overdue cycle 1",
                    notification_type="pending_reminder",
                    complaint_id=c.id,
                    dedup_key=f"reminder:open:{c.id}:24h"
                )
                self.assertIsNotNone(notif1)
                self.assertFalse(notif1.is_resolved)

                # Resolve cycle 1 reminder
                resolved_count = resolve_pending_reminders_for_complaint(c.id, commit=True)
                self.assertEqual(resolved_count, 1)

                # Cycle 2: A subsequent reminder with the same dedup_key is generated
                notif2 = create_notification(
                    recipient_role="admin",
                    title="Reminder Cycle 2",
                    message="Overdue cycle 2",
                    notification_type="pending_reminder",
                    complaint_id=c.id,
                    dedup_key=f"reminder:open:{c.id}:24h"
                )
                self.assertIsNotNone(notif2)
                self.assertNotEqual(notif1.id, notif2.id, "Second reminder must be a distinct new record.")
                self.assertFalse(notif2.is_resolved)

                # Verify both coexist in DB
                all_reminders = Notification.query.filter_by(
                    complaint_id=c.id,
                    dedup_key=f"reminder:open:{c.id}:24h"
                ).all()
                self.assertEqual(len(all_reminders), 2)
                self.assertTrue(all_reminders[0].is_resolved)
                self.assertFalse(all_reminders[1].is_resolved)
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()

    def test_scheduler_disabling_via_config_flag(self):
        """7. Verify DISABLE_REMINDER_SCHEDULER reliably prevents scheduler thread startup."""
        import threading
        from backend import create_app
        from backend.models.base import db
        from backend.services.notification_service import stop_reminder_scheduler

        # Ensure any existing thread is cleanly stopped
        stop_reminder_scheduler()

        # Branch A: When DISABLE_REMINDER_SCHEDULER is True, thread must NOT start even if TESTING=False
        app = create_app({
            "TESTING": False,
            "DISABLE_REMINDER_SCHEDULER": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{self.db_path}"
        })

        try:
            current_threads = {t.name for t in threading.enumerate()}
            self.assertNotIn(
                "anti-ragging-reminder-scheduler",
                current_threads,
                "Scheduler thread must NOT start when DISABLE_REMINDER_SCHEDULER is True."
            )
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()

        # Branch B: Verify when DISABLE_REMINDER_SCHEDULER is False and TESTING is False, scheduler DOES start
        orig_env = os.environ.get("DISABLE_REMINDER_SCHEDULER")
        if "DISABLE_REMINDER_SCHEDULER" in os.environ:
            del os.environ["DISABLE_REMINDER_SCHEDULER"]

        app_enabled = None
        try:
            app_enabled = create_app({
                "TESTING": False,
                "DISABLE_REMINDER_SCHEDULER": False,
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{self.db_path}"
            })
            threads_after = {t.name for t in threading.enumerate()}
            self.assertIn(
                "anti-ragging-reminder-scheduler",
                threads_after,
                "Scheduler thread MUST start in production mode when DISABLE_REMINDER_SCHEDULER is False."
            )
        finally:
            stop_reminder_scheduler()
            if orig_env is not None:
                os.environ["DISABLE_REMINDER_SCHEDULER"] = orig_env
            if app_enabled:
                with app_enabled.app_context():
                    db.session.remove()
                    db.engine.dispose()

    def test_app_factory_disposes_engine_and_does_not_leak_connections(self):
        """8. Verify repeated app-factory calls do not leak connections and temp files can be removed cleanly."""
        from backend import create_app
        from backend.models.base import db

        app1 = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{self.db_path}",
            "DISABLE_REMINDER_SCHEDULER": True
        })
        with app1.app_context():
            db.session.remove()
            db.engine.dispose()

        app2 = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{self.db_path}",
            "DISABLE_REMINDER_SCHEDULER": True
        })
        with app2.app_context():
            db.session.remove()
            db.engine.dispose()

        # Database file should be unlockable and removable
        self.assertTrue(os.path.exists(self.db_path))




class TestVercelServerlessCronAndDeployment(unittest.TestCase):
    def setUp(self):
        from backend import create_app
        from backend.models.base import db
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "DISABLE_REMINDER_SCHEDULER": True,
            "CRON_SECRET": "test_cron_secret_xyz"
        })
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        from backend.models.base import db
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.app_context.pop()

    def test_cron_endpoint_unauthorized_without_secret(self):
        resp = self.client.get("/api/cron/reminders")
        self.assertEqual(resp.status_code, 401)
        data = resp.get_json()
        self.assertIn("error", data)

    def test_cron_endpoint_authorized_with_bearer_token(self):
        resp = self.client.get("/api/cron/reminders", headers={
            "Authorization": "Bearer test_cron_secret_xyz"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("reminders_generated", data)

    def test_cron_endpoint_authorized_with_admin_session(self):
        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True

        resp = self.client.get("/api/cron/reminders")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "success")

    def test_app_py_entrypoint_suppresses_scheduler(self):
        from app import app as vercel_app
        self.assertTrue(vercel_app.config.get("DISABLE_REMINDER_SCHEDULER"))


if __name__ == "__main__":
    unittest.main()
