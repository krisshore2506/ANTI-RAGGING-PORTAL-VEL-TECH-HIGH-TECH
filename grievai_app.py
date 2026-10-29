"""
ANTI-RAGGING PORTAL - ROOT ENTRY POINT & BACKWARDS-COMPATIBILITY EXPORTS
Vel Tech High Tech Dr. Rangarajan Dr. Sakunthala Engineering College
NBA - Tier 1 SAR Guideline: Criterion No. 10.1.4

This module serves as the primary entry point for launching the application
(python grievai_app.py) and exports the modularized backend subsystems so that
all existing unit tests, scripts, and imports continue to function seamlessly.
"""

from backend import create_app
from backend.config.settings import (
    BASE_DIR,
    PORTAL_ROOT,
    DB_NAME,
    DB_PATH,
    SECRET_KEY,
    FLASK_ENV,
    IS_PRODUCTION,
    EVIDENCE_UPLOAD_FOLDER,
    STUDENT_EMAIL_REGEX,
    SMTP_EMAIL,
    SMTP_PASSWORD,
    EMAIL_USER,
    EMAIL_PASS,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USE_SSL,
    SMTP_USE_TLS,
    SMTP_TIMEOUT,
    DEV_SIMULATE_EMAIL,
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    MAX_TRACKING_ATTEMPTS,
    LOCKOUT_DURATION_SECONDS,
    MAX_ADMIN_ATTEMPTS,
    ADMIN_LOCKOUT_SECONDS,
    REMINDER_OPEN_HOURS,
    REMINDER_IN_PROGRESS_HOURS,
    REMINDER_CHECK_INTERVAL_SECONDS,
    normalize_database_url,
    mask_database_url,
    sanitize_db_error,
    logger
)
from backend.models.base import db, migrate
from backend.models.complaint import Complaint, ComplaintEvidence, ComplaintStatusHistory
from backend.models.committee import CommitteeMember, InvestigationAction
from backend.models.notification import Notification
from backend.models.audit import Cleaner, CallLog, SMSLog
from backend.middleware.csrf import (
    get_or_create_csrf_token,
    validate_csrf,
    inject_csrf_token
)
from backend.middleware.rate_limit import (
    tracking_ip_attempts,
    admin_failed_attempts,
    check_tracking_rate_limit,
    record_tracking_failure,
    reset_tracking_attempts,
    check_admin_rate_limit,
    record_admin_login_failure,
    reset_admin_login_failures,
    reset_admin_login_attempts
)
from backend.utils.security import (
    get_client_ip,
    generate_secure_otp,
    hash_tracking_token,
    verify_tracking_token
)
from backend.utils.triage import (
    VALID_INCIDENT_CATEGORIES,
    determine_incident_category,
    suggest_provisional_severity
)
from backend.utils.email_sms import (
    send_sms_simulated,
    send_otp_email,
    send_sms,
    trigger_auto_call,
    send_status_update_email
)
from backend.services.evidence_service import (
    ALLOWED_EVIDENCE_EXTENSIONS,
    ALLOWED_EXTENSIONS,
    format_file_size,
    inspect_file_signature,
    save_uploaded_evidence
)
from backend.services.notification_service import (
    create_notification,
    get_admin_pending_action_queue,
    process_pending_action_reminders,
    check_pending_action_reminders,
    resolve_pending_reminders_for_complaint,
    start_reminder_scheduler,
    stop_reminder_scheduler
)

# Instantiate application
app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
