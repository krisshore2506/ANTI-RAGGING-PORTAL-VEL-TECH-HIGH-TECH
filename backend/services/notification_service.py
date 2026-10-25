import os
import threading
from datetime import datetime
from flask import current_app
from backend.config.settings import (
    REMINDER_OPEN_HOURS,
    REMINDER_IN_PROGRESS_HOURS,
    REMINDER_CHECK_INTERVAL_SECONDS,
    logger
)
from backend.models.base import db
from backend.models.complaint import Complaint
from backend.models.notification import Notification

def create_notification(recipient_role, title, message, notification_type, recipient_email=None, complaint_id=None, dedup_key=None):
    """
    Creates and persists an in-app notification.
    Idempotent when dedup_key is provided.
    Does not expose confidential tracking tokens or OTPs.
    """
    try:
        if dedup_key:
            existing = Notification.query.filter_by(dedup_key=dedup_key).first()
            if existing:
                return existing

        notif = Notification(
            recipient_role=recipient_role,
            recipient_email=recipient_email.strip().lower() if recipient_email else None,
            complaint_id=complaint_id,
            notification_type=notification_type,
            title=title,
            message=message,
            created_at=datetime.utcnow(),
            is_read=False,
            dedup_key=dedup_key
        )
        db.session.add(notif)
        db.session.commit()
        logger.info("Notification created for %s (complaint #%s): %s", recipient_role, complaint_id, title)
        return notif
    except Exception as e:
        db.session.rollback()
        logger.error("Failed to create notification: %s", str(e))
        return None


def get_admin_pending_action_queue(now=None):
    """
    Returns complaints requiring administrative action based on configurable review windows:
    - Open complaints pending review beyond REMINDER_OPEN_HOURS (default 24h)
    - In Progress / Under Review complaints without updates beyond REMINDER_IN_PROGRESS_HOURS (default 72h)
    Excludes Closed and Resolved complaints.
    """
    if now is None:
        now = datetime.utcnow()

    pending_items = []
    try:
        active_complaints = Complaint.query.filter(
            ~Complaint.status.in_(["Closed", "Resolved"])
        ).all()

        for c in active_complaints:
            st = (c.status or "").strip().lower()
            if st == "open":
                ref_time = c.created_at or c.timestamp or now
                elapsed_hours = (now - ref_time).total_seconds() / 3600.0
                if elapsed_hours >= REMINDER_OPEN_HOURS:
                    urgency = "High" if elapsed_hours >= (REMINDER_OPEN_HOURS * 2) else "Medium"
                    pending_items.append({
                        "id": c.id,
                        "incident_type": c.incident_type or c.department or "Other / unsure",
                        "status": c.status,
                        "elapsed_hours": int(elapsed_hours),
                        "threshold_hours": REMINDER_OPEN_HOURS,
                        "urgency": urgency,
                        "reason": f"Open for {int(elapsed_hours)}h without committee action (threshold: {REMINDER_OPEN_HOURS}h)",
                        "student_email": c.student_email,
                        "created_at": ref_time.strftime("%Y-%m-%d %H:%M") if ref_time else "N/A"
                    })
            elif st in ["under review", "in progress", "in-progress", "assigned"]:
                ref_time = c.timestamp or c.created_at or now
                elapsed_hours = (now - ref_time).total_seconds() / 3600.0
                if elapsed_hours >= REMINDER_IN_PROGRESS_HOURS:
                    urgency = "High" if elapsed_hours >= (REMINDER_IN_PROGRESS_HOURS * 1.5) else "Medium"
                    pending_items.append({
                        "id": c.id,
                        "incident_type": c.incident_type or c.department or "Other / unsure",
                        "status": c.status,
                        "elapsed_hours": int(elapsed_hours),
                        "threshold_hours": REMINDER_IN_PROGRESS_HOURS,
                        "urgency": urgency,
                        "reason": f"Under Review for {int(elapsed_hours)}h without update (threshold: {REMINDER_IN_PROGRESS_HOURS}h)",
                        "student_email": c.student_email,
                        "created_at": ref_time.strftime("%Y-%m-%d %H:%M") if ref_time else "N/A"
                    })

        pending_items.sort(key=lambda x: x["elapsed_hours"], reverse=True)
    except Exception as e:
        logger.error("Error retrieving admin pending action queue: %s", str(e))

    return pending_items


def process_pending_action_reminders(now=None):
    """
    Idempotent reminder processor that generates persistent admin notifications
    for overdue complaints. Closed and Resolved complaints never receive reminders.
    """
    if now is None:
        now = datetime.utcnow()

    generated_count = 0
    try:
        active_complaints = Complaint.query.filter(
            ~Complaint.status.in_(["Closed", "Resolved"])
        ).all()

        for c in active_complaints:
            st = (c.status or "").strip().lower()
            if st == "open":
                ref_time = c.created_at or c.timestamp or now
                elapsed_hours = (now - ref_time).total_seconds() / 3600.0
                if elapsed_hours >= REMINDER_OPEN_HOURS:
                    bucket = int(elapsed_hours // REMINDER_OPEN_HOURS) * REMINDER_OPEN_HOURS
                    dedup_key = f"reminder:open:{c.id}:{bucket}h"
                    existing = Notification.query.filter_by(dedup_key=dedup_key).first()
                    if not existing:
                        title = f"Action Required: Incident #{c.id} Pending Initial Review"
                        message = f"Incident report #{c.id} has remained in 'Open' status for {int(elapsed_hours)} hours without committee action (review window: {REMINDER_OPEN_HOURS}h)."
                        notif = create_notification(
                            recipient_role="admin",
                            complaint_id=c.id,
                            notification_type="pending_reminder",
                            title=title,
                            message=message,
                            dedup_key=dedup_key
                        )
                        if notif:
                            generated_count += 1

            elif st in ["under review", "in progress", "in-progress", "assigned"]:
                ref_time = c.timestamp or c.created_at or now
                elapsed_hours = (now - ref_time).total_seconds() / 3600.0
                if elapsed_hours >= REMINDER_IN_PROGRESS_HOURS:
                    bucket = int(elapsed_hours // REMINDER_IN_PROGRESS_HOURS) * REMINDER_IN_PROGRESS_HOURS
                    dedup_key = f"reminder:in_progress:{c.id}:{bucket}h"
                    existing = Notification.query.filter_by(dedup_key=dedup_key).first()
                    if not existing:
                        title = f"Action Required: Incident #{c.id} Awaiting Follow-up"
                        message = f"Incident report #{c.id} has been 'Under Review' without updates for {int(elapsed_hours)} hours (follow-up window: {REMINDER_IN_PROGRESS_HOURS}h)."
                        notif = create_notification(
                            recipient_role="admin",
                            complaint_id=c.id,
                            notification_type="pending_reminder",
                            title=title,
                            message=message,
                            dedup_key=dedup_key
                        )
                        if notif:
                            generated_count += 1

    except Exception as e:
        logger.error("Error processing pending action reminders: %s", str(e))

    return generated_count

# Alias for backwards compatibility with tests
check_pending_action_reminders = process_pending_action_reminders


_scheduler_thread = None
_scheduler_stop_event = threading.Event()


def _reminder_daemon_loop(app):
    logger.info("Pending-action reminder daemon started (interval: %ss).", REMINDER_CHECK_INTERVAL_SECONDS)
    while not _scheduler_stop_event.is_set():
        try:
            with app.app_context():
                process_pending_action_reminders()
        except Exception as e:
            logger.error("Reminder daemon execution error: %s", str(e))

        _scheduler_stop_event.wait(timeout=REMINDER_CHECK_INTERVAL_SECONDS)


def start_reminder_scheduler(app=None):
    global _scheduler_thread
    if os.getenv("DISABLE_REMINDER_SCHEDULER", "False").lower() in ("true", "1"):
        logger.info("Reminder scheduler explicitly disabled via DISABLE_REMINDER_SCHEDULER.")
        return

    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        return

    if app is None:
        try:
            app = current_app._get_current_object()
        except Exception:
            return

    _scheduler_stop_event.clear()
    _scheduler_thread = threading.Thread(
        target=_reminder_daemon_loop,
        args=(app,),
        daemon=True,
        name="anti-ragging-reminder-scheduler"
    )
    _scheduler_thread.start()


def stop_reminder_scheduler():
    global _scheduler_thread
    _scheduler_stop_event.set()
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        _scheduler_thread.join(timeout=2)
