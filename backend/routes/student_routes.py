import os
import secrets
from datetime import datetime
from flask import (
    Blueprint,
    request,
    render_template,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
    send_from_directory,
    send_file
)
from backend.config.settings import (
    EVIDENCE_UPLOAD_FOLDER,
    logger
)
from backend.models.base import db
from backend.models.complaint import Complaint, ComplaintEvidence, ComplaintStatusHistory
from backend.models.notification import Notification
from backend.utils.security import (
    get_client_ip,
    hash_tracking_token,
    verify_tracking_token
)
from backend.utils.triage import (
    determine_incident_category,
    suggest_provisional_severity
)
from backend.services.evidence_service import save_uploaded_evidence, get_evidence_upload_folder, fetch_evidence_file_stream
from backend.services.notification_service import create_notification
from backend.middleware.rate_limit import (
    check_tracking_rate_limit,
    record_tracking_failure,
    reset_tracking_attempts
)

student_bp = Blueprint("student", __name__)

@student_bp.route("/", endpoint="home")
def home():
    return render_template("index.html")


@student_bp.route("/student_dashboard", endpoint="student_dashboard")
def student_dashboard():
    if not session.get("verified") or not session.get("email"):
        return redirect(url_for("student_login"))

    email = session.get("email")
    complaints = Complaint.query.filter_by(student_email=email).order_by(Complaint.timestamp.desc()).all()

    total = len(complaints)
    open_count = sum(1 for c in complaints if c.status.strip().lower() == "open")
    in_progress = sum(1 for c in complaints if "progress" in c.status.strip().lower() or "assigned" in c.status.strip().lower())
    resolved = sum(1 for c in complaints if c.status.strip().lower() in ["resolved", "closed"])

    student_notifs = Notification.query.filter_by(
        recipient_role="student",
        recipient_email=email.strip().lower()
    ).order_by(Notification.created_at.desc()).limit(20).all()

    notif_unread_count = Notification.query.filter_by(
        recipient_role="student",
        recipient_email=email.strip().lower(),
        is_read=False
    ).count()

    return render_template(
        "student_dashboard.html",
        email=email,
        complaints=complaints,
        total=total,
        open_count=open_count,
        in_progress=in_progress,
        resolved=resolved,
        notifications=student_notifs,
        unread_count=notif_unread_count
    )


@student_bp.route("/complaint_form", endpoint="complaint_form")
def complaint_form():
    if not session.get("verified") or not session.get("email"):
        return redirect(url_for("student_login"))

    return render_template(
        "complaint_form.html",
        email=session.get("email")
    )


@student_bp.route("/predict", methods=["POST"], endpoint="predict")
def predict():
    if not session.get("verified") or not session.get("email"):
        return redirect(url_for("student_login"))

    email = session.get("email")
    complaint_text = (request.form.get("complaint") or request.form.get("complaint_text", "")).strip()
    selected_category = (request.form.get("incident_type") or request.form.get("incident_category", "")).strip()
    incident_date_time = request.form.get("incident_date_time", "").strip()
    location = request.form.get("location", "").strip()
    location_details = request.form.get("location_details", "").strip()
    complaint_title = (request.form.get("title") or request.form.get("complaint_title", "")).strip()
    student_priority = (request.form.get("priority") or request.form.get("student_priority", "Medium")).strip()
    accused_details = request.form.get("accused_details", "").strip() or None
    witness_details = request.form.get("witness_details", "").strip() or None
    is_confidential = request.form.get("is_confidential") in ["true", "True", "1", "on", True]
    declaration_agreed = request.form.get("accuracy_declaration") in ["true", "True", "1", "on", True]

    if not complaint_text:
        flash("Please provide a detailed description of the incident.", "error")
        return redirect(url_for("complaint_form"))

    if not declaration_agreed:
        flash("You must confirm the truthfulness declaration before submitting a report.", "error")
        return redirect(url_for("complaint_form"))

    if not incident_date_time:
        incident_date_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    if location_details and location_details.lower() not in location.lower():
        full_location = f"{location} ({location_details})"
    else:
        full_location = location or "Campus Premises"

    incident_category = determine_incident_category(selected_category, complaint_text)

    if not complaint_title:
        complaint_title = f"{incident_category} Report - {datetime.utcnow().strftime('%d %b %Y')}"

    provisional_severity = suggest_provisional_severity(incident_category, complaint_text, student_priority)

    raw_token = f"ARC-{secrets.token_urlsafe(16)}"
    stored_token_hash = hash_tracking_token(raw_token)

    initial_status = "Open"
    committee_assigned = "Anti-Ragging Committee"
    complaint_ref = f"ARC-{datetime.utcnow().strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"

    complaint = Complaint(
        complaint_reference=complaint_ref,
        student_email=email,
        complaint_title=complaint_title,
        complaint_text=complaint_text,
        department=incident_category,
        assigned_cleaner="None (Anti-Ragging Squad)",
        status=initial_status,
        priority=student_priority,
        incident_type=incident_category,
        severity_level=provisional_severity,
        incident_date_time=incident_date_time,
        incident_location=full_location,
        accused_details=accused_details,
        witness_details=witness_details,
        is_confidential=is_confidential,
        tracking_token=stored_token_hash,
        committee_assigned=committee_assigned,
        admin_remarks=f"[Triage Note] Provisional Severity: {provisional_severity} (Automated triage suggestion for ARC review)."
    )

    db.session.add(complaint)
    db.session.flush()

    # Record initial audit entry in status history
    initial_audit = ComplaintStatusHistory(
        complaint_id=complaint.id,
        previous_status=None,
        new_status=initial_status,
        action_type="submitted",
        actor_role="student",
        actor_email=email,
        remarks=f"Incident report submitted. Category: {incident_category}, Priority: {student_priority}."
    )
    db.session.add(initial_audit)

    saved_file_paths = []
    try:
        evidence_files = request.files.getlist("evidence_files")
        for f in evidence_files:
            if f and f.filename and f.filename.strip():
                ev_record, f_path = save_uploaded_evidence(f, complaint.id, is_voice=False)
                if ev_record and f_path:
                    saved_file_paths.append(f_path)
                    db.session.add(ev_record)

        voice_file = request.files.get("voice_recording")
        if voice_file and voice_file.filename and voice_file.filename.strip():
            ev_record, f_path = save_uploaded_evidence(voice_file, complaint.id, is_voice=True)
            if ev_record and f_path:
                saved_file_paths.append(f_path)
                db.session.add(ev_record)

        db.session.commit()
    except ValueError as ve:
        db.session.rollback()
        for p in saved_file_paths:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        logger.warning("Evidence validation failed: %s", str(ve))
        flash(f"Evidence submission error: {str(ve)}", "error")
        return redirect(url_for("complaint_form"))
    except Exception as ex:
        db.session.rollback()
        for p in saved_file_paths:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        logger.error("Evidence processing error for incident #%s: %s", complaint.id, str(ex))
        flash("An error occurred while uploading evidence files. Please check file sizes and formats.", "error")
        return redirect(url_for("complaint_form"))

    logger.info("New incident #%s submitted successfully by student.", complaint.id)

    try:
        create_notification(
            recipient_role="admin",
            complaint_id=complaint.id,
            notification_type="new_complaint",
            title=f"New Incident Report Submitted (#{complaint.id})",
            message=f"Incident report #{complaint.id} ({complaint.incident_type or 'General'}) with severity '{complaint.severity_level}' submitted for ARC review."
        )
    except Exception as notif_err:
        logger.error("Failed to create admin notification for incident #%s: %s", complaint.id, str(notif_err))

    return render_template(
        "success.html",
        complaint_id=complaint.id,
        tracking_token=raw_token,
        category=incident_category,
        provisional_severity=provisional_severity,
        is_confidential=is_confidential,
        incident_location=full_location,
        incident_date_time=incident_date_time,
        cleaner_name=committee_assigned,
        cleaner_phone="ARC Support Desk",
        is_cleaning=False
    )


@student_bp.route("/evidence/<int:evidence_id>", endpoint="access_evidence")
def access_evidence(evidence_id):
    evidence = ComplaintEvidence.query.get(evidence_id)
    if not evidence:
        return render_template(
            "error.html",
            error_title="File Unavailable",
            error_heading="Evidence Not Found",
            error_message="The requested evidence record could not be found."
        ), 404

    complaint = Complaint.query.get(evidence.complaint_id)
    if not complaint:
        return render_template(
            "error.html",
            error_title="File Unavailable",
            error_heading="Incident Record Missing",
            error_message="The associated incident report could not be found."
        ), 404

    is_admin = session.get("admin_logged_in") is True
    is_owner = (
        session.get("verified") is True
        and session.get("email")
        and session.get("email").strip().lower() == complaint.student_email.strip().lower()
    )

    if not is_admin and not is_owner:
        logger.warning(
            "Unauthorized access attempt to evidence #%s (incident #%s) from IP %s",
            evidence_id,
            complaint.id,
            get_client_ip()
        )
        return render_template(
            "error.html",
            error_title="Access Denied",
            error_heading="Unauthorized Evidence Access",
            error_message="You are not authorized to view or download evidence attached to this incident report."
        ), 403

    safe_storage_name = os.path.basename(evidence.storage_filename)
    file_path = os.path.join(get_evidence_upload_folder(), safe_storage_name)
    is_inline_type = evidence.file_type in ["photo", "audio", "video"]

    if os.path.exists(file_path):
        return send_from_directory(
            directory=get_evidence_upload_folder(),
            path=safe_storage_name,
            mimetype=evidence.mime_type,
            as_attachment=not is_inline_type,
            download_name=evidence.original_filename
        )

    stream, _ = fetch_evidence_file_stream(safe_storage_name)
    if stream is not None:
        return send_file(
            stream,
            mimetype=evidence.mime_type,
            as_attachment=not is_inline_type,
            download_name=evidence.original_filename
        )

    logger.warning("Evidence file missing from disk and storage: %s", file_path)
    return render_template(
        "error.html",
        error_title="File Unavailable",
        error_heading="Evidence File Not Found",
        error_message="The evidence file could not be located on the server storage."
    ), 404


@student_bp.route("/track", methods=["GET", "POST"], endpoint="track")
def track():
    client_ip = get_client_ip()

    if request.method == "GET":
        prefill_id = request.args.get("id", "").strip()
        prefill_token = request.args.get("token", "").strip()
        if prefill_id and prefill_token:
            allowed, rate_msg = check_tracking_rate_limit(client_ip)
            if not allowed:
                return render_template(
                    "track.html",
                    complaint=None,
                    error_message=rate_msg,
                    lookup_attempted=True
                )
            try:
                cid = int(prefill_id)
                comp = Complaint.query.get(cid)
                if comp and verify_tracking_token(comp.tracking_token, prefill_token):
                    reset_tracking_attempts(client_ip)
                    return render_template("track.html", complaint=comp, error_message=None, lookup_attempted=True)
                else:
                    record_tracking_failure(client_ip)
                    return render_template("track.html", complaint=None, error_message="Unable to verify incident ticket. Please verify your Reference ID and Tracking Token.", lookup_attempted=True)
            except Exception:
                record_tracking_failure(client_ip)
                return render_template("track.html", complaint=None, error_message="Unable to verify incident ticket. Please verify your Reference ID and Tracking Token.", lookup_attempted=True)

        return render_template("track.html", complaint=None, error_message=None, lookup_attempted=False)

    allowed, rate_msg = check_tracking_rate_limit(client_ip)
    if not allowed:
        return render_template("track.html", complaint=None, error_message=rate_msg, lookup_attempted=True)

    complaint_id_str = request.form.get("complaint_id", "").strip()
    provided_token = request.form.get("tracking_token", "").strip()

    if not complaint_id_str:
        return render_template(
            "track.html",
            complaint=None,
            error_message="Please enter a valid Reference ID.",
            lookup_attempted=False
        )

    try:
        complaint_id = int(complaint_id_str)
    except (ValueError, TypeError):
        record_tracking_failure(client_ip)
        return render_template(
            "track.html",
            complaint=None,
            error_message="Unable to verify incident ticket. Please verify your Reference ID and Tracking Token.",
            lookup_attempted=True
        )

    complaint = Complaint.query.get(complaint_id)

    is_authenticated_owner = (
        session.get("verified") and
        session.get("email") and
        complaint is not None and
        complaint.student_email == session.get("email")
    )

    is_valid_token = (
        complaint is not None and
        provided_token and
        verify_tracking_token(complaint.tracking_token, provided_token)
    )

    if not is_authenticated_owner and not is_valid_token:
        record_tracking_failure(client_ip)
        return render_template(
            "track.html",
            complaint=None,
            error_message="Unable to verify incident ticket. Please verify your Reference ID and Tracking Token.",
            lookup_attempted=True
        )

    reset_tracking_attempts(client_ip)

    return render_template(
        "track.html",
        complaint=complaint,
        error_message=None,
        lookup_attempted=True
    )


@student_bp.route("/student/notifications", methods=["GET"], endpoint="student_notifications")
def student_notifications():
    if not session.get("verified") or not session.get("email"):
        return jsonify({"error": "Unauthorized"}), 401

    email = session.get("email").strip().lower()
    notifs = Notification.query.filter_by(
        recipient_role="student",
        recipient_email=email
    ).order_by(Notification.created_at.desc()).limit(50).all()

    unread_count = Notification.query.filter_by(
        recipient_role="student",
        recipient_email=email,
        is_read=False
    ).count()

    return jsonify({
        "notifications": [n.to_dict() for n in notifs],
        "unread_count": unread_count
    })


@student_bp.route("/student/notifications/<int:notification_id>/read", methods=["POST"], endpoint="student_mark_notification_read")
def student_mark_notification_read(notification_id):
    if not session.get("verified") or not session.get("email"):
        return jsonify({"error": "Unauthorized"}), 401

    email = session.get("email").strip().lower()
    notif = Notification.query.filter_by(
        id=notification_id,
        recipient_role="student",
        recipient_email=email
    ).first()

    if not notif:
        return jsonify({"error": "Notification not found"}), 404

    notif.is_read = True
    notif.read_at = datetime.utcnow()
    db.session.commit()

    unread_count = Notification.query.filter_by(
        recipient_role="student",
        recipient_email=email,
        is_read=False
    ).count()

    return jsonify({"success": True, "id": notif.id, "unread_count": unread_count})


@student_bp.route("/student/notifications/mark_all_read", methods=["POST"], endpoint="student_mark_all_notifications_read")
def student_mark_all_notifications_read():
    if not session.get("verified") or not session.get("email"):
        return jsonify({"error": "Unauthorized"}), 401

    email = session.get("email").strip().lower()
    unread_notifs = Notification.query.filter_by(
        recipient_role="student",
        recipient_email=email,
        is_read=False
    ).all()

    now = datetime.utcnow()
    for n in unread_notifs:
        n.is_read = True
        n.read_at = now

    db.session.commit()
    return jsonify({"success": True, "marked_count": len(unread_notifs), "unread_count": 0})
