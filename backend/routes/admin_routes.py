from datetime import datetime
from flask import (
    Blueprint,
    request,
    render_template,
    redirect,
    url_for,
    session,
    flash,
    jsonify
)
from backend.config.settings import (
    REMINDER_OPEN_HOURS,
    REMINDER_IN_PROGRESS_HOURS,
    logger
)
from backend.models.base import db
from backend.models.complaint import Complaint
from backend.models.notification import Notification
from backend.models.audit import SMSLog, CallLog
from backend.utils.email_sms import send_status_update_email
from backend.services.notification_service import (
    create_notification,
    get_admin_pending_action_queue,
    resolve_pending_reminders_for_complaint
)

admin_bp = Blueprint("admin_bp", __name__)

def _dispatch_status_update_email(student_email, complaint_id, new_status):
    import sys
    g_app = sys.modules.get("grievai_app")
    if g_app and hasattr(g_app, "send_status_update_email"):
        return g_app.send_status_update_email(student_email, complaint_id, new_status)
    from backend.utils.email_sms import send_status_update_email as _real_send
    return _real_send(student_email, complaint_id, new_status)


@admin_bp.route("/admin", methods=["GET", "POST"], endpoint="admin")
def admin():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        complaint_id = request.form.get("complaint_id")
        new_status = request.form.get("status")
        remarks = request.form.get("remarks", "").strip()

        complaint = Complaint.query.get(complaint_id)
        if complaint:
            valid_statuses = ["Open", "Under Review", "Action Taken", "Closed"]
            status_map = {
                "In Progress": "Under Review",
                "In-Progress": "Under Review",
                "Resolved": "Closed",
                "Assigned": "Under Review"
            }
            target_status = status_map.get(new_status, new_status)

            try:
                if target_status in valid_statuses:
                    complaint.status = target_status

                complaint.admin_remarks = remarks
                complaint.timestamp = datetime.utcnow()

                # Atomically resolve stale pending reminders for this complaint
                resolve_pending_reminders_for_complaint(complaint.id, resolved_at=complaint.timestamp, commit=False)

                db.session.commit()
            except Exception as commit_err:
                db.session.rollback()
                logger.error("Failed to commit status update for incident #%s: %s", complaint.id, str(commit_err))
                flash("An error occurred while updating the complaint. Changes were not saved.", "error")
                return redirect(url_for("admin"))

            try:
                summary_msg = f"Your incident report #{complaint.id} status is now '{complaint.status}'."
                if complaint.status == "Under Review":
                    summary_msg = f"Your incident report #{complaint.id} is now under active review by the Anti-Ragging Committee."
                elif complaint.status == "Action Taken":
                    summary_msg = f"Formal institutional action has been recorded for your incident report #{complaint.id}."
                elif complaint.status == "Closed":
                    summary_msg = f"Your incident report #{complaint.id} has been formally closed and concluded."

                create_notification(
                    recipient_role="student",
                    recipient_email=complaint.student_email,
                    complaint_id=complaint.id,
                    notification_type="status_update",
                    title=f"Incident #{complaint.id} Status Update: {complaint.status}",
                    message=summary_msg
                )
            except Exception as notif_err:
                logger.error("Failed to record student notification for incident #%s: %s", complaint.id, str(notif_err))

            try:
                _dispatch_status_update_email(complaint.student_email, complaint.id, complaint.status)
            except Exception as notify_err:
                logger.error("Failed to dispatch status update notification: %s", str(notify_err))
            flash(f"Incident #{complaint.id} updated: Status is now '{complaint.status}'.", "success")

        return redirect(url_for("admin"))

    total_count = Complaint.query.count()
    open_count = Complaint.query.filter_by(status="Open").count()
    under_review_count = Complaint.query.filter(
        (Complaint.status == "Under Review") | (Complaint.status == "In Progress") | (Complaint.status == "In-Progress") | (Complaint.status == "Assigned")
    ).count()
    action_taken_count = Complaint.query.filter_by(status="Action Taken").count()
    closed_count = Complaint.query.filter(
        (Complaint.status == "Closed") | (Complaint.status == "Resolved")
    ).count()

    stats = {
        "total": total_count,
        "open": open_count,
        "under_review": under_review_count,
        "action_taken": action_taken_count,
        "closed": closed_count
    }

    categories = [
        "Verbal or emotional harassment",
        "Physical ragging",
        "Hostel hazing or coercion",
        "Cyber-ragging",
        "Financial extortion",
        "Other / unsure"
    ]
    category_counts = {}
    for cat in categories:
        cnt = Complaint.query.filter(
            (Complaint.incident_type == cat) | (Complaint.department == cat)
        ).count()
        category_counts[cat] = cnt

    complaints = Complaint.query.order_by(Complaint.timestamp.desc()).all()

    admin_notifs = Notification.query.filter_by(
        recipient_role="admin"
    ).order_by(Notification.created_at.desc()).limit(20).all()

    admin_unread_count = Notification.query.filter(
        Notification.recipient_role == "admin",
        Notification.is_read == False,
        Notification.is_resolved == False
    ).count()

    pending_queue = get_admin_pending_action_queue()

    return render_template(
        "admin.html",
        complaints=complaints,
        stats=stats,
        category_counts=category_counts,
        notifications=admin_notifs,
        unread_count=admin_unread_count,
        pending_queue=pending_queue,
        reminder_open_hours=REMINDER_OPEN_HOURS,
        reminder_in_progress_hours=REMINDER_IN_PROGRESS_HOURS
    )


@admin_bp.route("/admin/complaint/<int:complaint_id>", methods=["GET", "POST"], endpoint="admin_complaint_details")
def admin_complaint_details(complaint_id):
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    complaint = Complaint.query.get_or_404(complaint_id)

    if request.method == "POST":
        new_status = request.form.get("status", "").strip()
        remarks = request.form.get("admin_remarks", "").strip()
        valid_statuses = ["Open", "Under Review", "Action Taken", "Closed"]
        status_map = {
            "In Progress": "Under Review",
            "In-Progress": "Under Review",
            "Resolved": "Closed",
            "Assigned": "Under Review"
        }
        target_status = status_map.get(new_status, new_status)

        try:
            if target_status in valid_statuses:
                complaint.status = target_status
            complaint.admin_remarks = remarks
            complaint.timestamp = datetime.utcnow()

            # Atomically resolve stale pending reminders for this complaint
            resolve_pending_reminders_for_complaint(complaint.id, resolved_at=complaint.timestamp, commit=False)

            db.session.commit()
        except Exception as commit_err:
            db.session.rollback()
            logger.error("Failed to commit detail status update for incident #%s: %s", complaint.id, str(commit_err))
            flash("An error occurred while updating the complaint. Changes were not saved.", "error")
            return redirect(url_for("admin_complaint_details", complaint_id=complaint.id))

        try:
            summary_msg = f"Your incident report #{complaint.id} status is now '{complaint.status}'."
            if complaint.status == "Under Review":
                summary_msg = f"Your incident report #{complaint.id} is now under active review by the Anti-Ragging Committee."
            elif complaint.status == "Action Taken":
                summary_msg = f"Formal institutional action has been recorded for your incident report #{complaint.id}."
            elif complaint.status == "Closed":
                summary_msg = f"Your incident report #{complaint.id} has been formally closed and concluded."

            create_notification(
                recipient_role="student",
                recipient_email=complaint.student_email,
                complaint_id=complaint.id,
                notification_type="status_update",
                title=f"Incident #{complaint.id} Status Update: {complaint.status}",
                message=summary_msg
            )
        except Exception as notif_err:
            logger.error("Failed to record student notification for incident #%s: %s", complaint.id, str(notif_err))

        try:
            _dispatch_status_update_email(complaint.student_email, complaint.id, complaint.status)
        except Exception as notify_err:
            logger.error("Failed to dispatch status update notification: %s", str(notify_err))
        flash(f"Incident #{complaint.id} updated: Status is now '{complaint.status}'.", "success")
        return redirect(url_for("admin_complaint_details", complaint_id=complaint.id))

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("format") == "json":
        return jsonify({
            "id": complaint.id,
            "student_email": complaint.student_email,
            "incident_type": complaint.incident_type or complaint.department or "Other / unsure",
            "severity_level": complaint.severity_level or complaint.priority or "Medium",
            "incident_date_time": complaint.incident_date_time or "N/A",
            "incident_location": complaint.incident_location or "N/A",
            "complaint_text": complaint.complaint_text,
            "accused_details": complaint.accused_details or "None specified",
            "witness_details": complaint.witness_details or "None specified",
            "is_confidential": bool(complaint.is_confidential),
            "status": complaint.status,
            "committee_assigned": complaint.committee_assigned or "Anti-Ragging Squad",
            "admin_remarks": complaint.admin_remarks or "",
            "timestamp": complaint.timestamp.strftime("%Y-%m-%d %H:%M") if complaint.timestamp else "N/A"
        })

    return render_template("admin_complaint_detail.html", complaint=complaint)


@admin_bp.route("/admin/logs", endpoint="admin_logs")
def admin_logs():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    sms_logs = SMSLog.query.order_by(SMSLog.timestamp.desc()).all()
    call_logs = CallLog.query.order_by(CallLog.timestamp.desc()).all()

    return render_template(
        "admin_logs.html",
        sms_logs=sms_logs,
        call_logs=call_logs
    )


@admin_bp.route("/admin/notifications", methods=["GET"], endpoint="admin_notifications")
def admin_notifications():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401

    notifs = Notification.query.filter_by(
        recipient_role="admin"
    ).order_by(Notification.created_at.desc()).limit(50).all()

    unread_count = Notification.query.filter(
        Notification.recipient_role == "admin",
        Notification.is_read == False,
        Notification.is_resolved == False
    ).count()

    return jsonify({
        "notifications": [n.to_dict() for n in notifs],
        "unread_count": unread_count
    })


@admin_bp.route("/admin/notifications/<int:notification_id>/read", methods=["POST"], endpoint="admin_mark_notification_read")
def admin_mark_notification_read(notification_id):
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401

    notif = Notification.query.filter_by(
        id=notification_id,
        recipient_role="admin"
    ).first()

    if not notif:
        return jsonify({"error": "Notification not found"}), 404

    try:
        notif.is_read = True
        if not notif.read_at:
            notif.read_at = datetime.utcnow()
        db.session.commit()

        unread_count = Notification.query.filter(
            Notification.recipient_role == "admin",
            Notification.is_read == False,
            Notification.is_resolved == False
        ).count()

        return jsonify({"success": True, "id": notif.id, "unread_count": unread_count})
    except Exception as e:
        db.session.rollback()
        logger.error("Error marking notification %s as read: %s", notification_id, str(e))
        return jsonify({"error": "Failed to update notification"}), 500


@admin_bp.route("/admin/notifications/mark_all_read", methods=["POST"], endpoint="admin_mark_all_notifications_read")
def admin_mark_all_notifications_read():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        unread_notifs = Notification.query.filter_by(
            recipient_role="admin",
            is_read=False
        ).all()

        now = datetime.utcnow()
        for n in unread_notifs:
            n.is_read = True
            if not n.read_at:
                n.read_at = now

        db.session.commit()

        unread_count = Notification.query.filter(
            Notification.recipient_role == "admin",
            Notification.is_read == False,
            Notification.is_resolved == False
        ).count()

        return jsonify({
            "success": True,
            "marked_count": len(unread_notifs),
            "unread_count": unread_count
        })
    except Exception as e:
        db.session.rollback()
        logger.error("Error marking all admin notifications as read: %s", str(e))
        return jsonify({"error": "Failed to mark notifications as read"}), 500


@admin_bp.route("/admin/pending_queue", methods=["GET"], endpoint="admin_pending_queue")
def admin_pending_queue():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401

    queue = get_admin_pending_action_queue()
    return jsonify({
        "queue": queue,
        "count": len(queue),
        "thresholds": {
            "open_hours": REMINDER_OPEN_HOURS,
            "in_progress_hours": REMINDER_IN_PROGRESS_HOURS
        }
    })
