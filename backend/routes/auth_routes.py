import time
import secrets
from flask import Blueprint, request, render_template, redirect, url_for, session, flash
from backend.config.settings import (
    STUDENT_EMAIL_REGEX,
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    logger
)
from backend.utils.security import generate_secure_otp, get_client_ip
from backend.utils.email_sms import send_otp_email
from backend.middleware.rate_limit import (
    check_admin_rate_limit,
    record_admin_login_failure,
    reset_admin_login_failures
)

auth_bp = Blueprint("auth", __name__)

def _dispatch_otp_email(email, otp):
    import sys
    g_app = sys.modules.get("grievai_app")
    if g_app and hasattr(g_app, "send_otp_email"):
        return g_app.send_otp_email(email, otp)
    from backend.utils.email_sms import send_otp_email as _real_send
    return _real_send(email, otp)


@auth_bp.route("/student_login", endpoint="student_login")
def student_login():
    return render_template("student_login.html")


@auth_bp.route("/send_otp", methods=["POST"], endpoint="send_otp")
def send_otp():
    raw_email = request.form.get("email", "")
    email = raw_email.strip().lower()

    if not email or not STUDENT_EMAIL_REGEX.match(email):
        logger.warning("Student authentication rejected for invalid/non-student email: '%s'", email)
        return render_template(
            "error.html",
            error_title="Access Restricted",
            error_heading="Invalid Email Domain / Student Roll Number",
            error_message="The Anti-Ragging Portal is restricted exclusively to valid @velhightech.com student roll email accounts in the format 'vh<digits>@velhightech.com' (e.g., vh13838@velhightech.com). Staff accounts and non-college email domains are not permitted for student logins."
        )

    now = time.time()
    last_otp_time = session.get("otp_time", 0)
    if now - last_otp_time < 30 and session.get("email") == email:
        wait_sec = int(30 - (now - last_otp_time)) + 1
        flash(f"A verification code was recently generated. Please wait {wait_sec} second(s) before requesting again.", "error")
        return redirect(url_for("verify_otp_page"))

    session.pop("verified", None)
    session.pop("admin_logged_in", None)

    otp = generate_secure_otp()
    session["otp"] = otp
    session["email"] = email
    session["otp_time"] = now
    session["otp_resend_count"] = 0

    try:
        _dispatch_otp_email(email, otp)
        return redirect(url_for("verify_otp_page"))
    except Exception as e:
        logger.error("Failed to deliver OTP email: %s", str(e))
        flash("Unable to send verification code. Please check server connectivity or try again shortly.", "error")
        return render_template("student_login.html")


@auth_bp.route("/verify_otp", methods=["GET"], endpoint="verify_otp_page")
def verify_otp_page():
    return render_template("verify_otp.html", email=session.get("email"))


@auth_bp.route("/verify_otp", methods=["POST"], endpoint="verify_otp")
def verify_otp():
    user_otp = request.form.get("otp", "").strip()
    stored_otp = session.get("otp")
    email = session.get("email")
    otp_time = session.get("otp_time", 0)

    if not stored_otp or not otp_time:
        flash("No active verification code found. Please request a new code.", "error")
        return render_template("verify_otp.html", email=email)

    now = time.time()
    if now - otp_time > 60:
        session.pop("otp", None)
        session.pop("otp_time", None)
        flash("Verification code has expired (valid for 60 seconds). Please request a new code.", "error")
        return render_template("verify_otp.html", email=email)

    if secrets.compare_digest(str(user_otp), str(stored_otp)):
        session.pop("otp", None)
        session.pop("otp_time", None)
        session.pop("otp_resend_count", None)
        session.pop("admin_logged_in", None)
        session["verified"] = True
        logger.info("Student authenticated successfully via OTP.")
        return redirect(url_for("student_dashboard"))

    flash("Invalid verification code. Please try again.", "error")
    return render_template("verify_otp.html", email=email)


@auth_bp.route("/resend_otp", methods=["POST"], endpoint="resend_otp")
def resend_otp():
    email = session.get("email")

    if not email:
        flash("Session expired. Please enter your email again.", "error")
        return redirect(url_for("student_login"))

    now = time.time()
    last_otp_time = session.get("otp_time", 0)
    resend_count = session.get("otp_resend_count", 0)

    if now - last_otp_time < 30:
        wait_sec = int(30 - (now - last_otp_time)) + 1
        flash(f"Please wait {wait_sec} second(s) before requesting another verification code.", "error")
        return redirect(url_for("verify_otp_page"))

    if resend_count >= 5:
        flash("Maximum resend attempts reached. Please return to login and try again later.", "error")
        return redirect(url_for("student_login"))

    new_otp = generate_secure_otp()
    session["otp"] = new_otp
    session["otp_time"] = now
    session["otp_resend_count"] = resend_count + 1

    try:
        _dispatch_otp_email(email, new_otp)
        flash("A new verification code has been sent to your email (valid for 60 seconds).", "success")
    except Exception as e:
        logger.error("Failed to resend OTP email: %s", str(e))
        flash("Unable to resend verification code. Please try again shortly.", "error")

    return redirect(url_for("verify_otp_page"))


@auth_bp.route("/student_logout", endpoint="student_logout")
def student_logout():
    session.clear()
    return redirect(url_for("home"))


@auth_bp.route("/admin_login", methods=["GET", "POST"], endpoint="admin_login")
def admin_login():
    client_ip = get_client_ip()

    if request.method == "POST":
        allowed, lockout_msg = check_admin_rate_limit(client_ip)
        if not allowed:
            flash(lockout_msg, "error")
            return render_template("admin_login.html"), 429

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        configured_email = ADMIN_EMAIL or "admin@velhightech.com"
        configured_password = ADMIN_PASSWORD or "veltech@arc2026"

        email_match = secrets.compare_digest(str(email).lower(), str(configured_email).lower())
        password_match = secrets.compare_digest(str(password), str(configured_password))

        if email_match and password_match:
            reset_admin_login_failures(client_ip)
            session.pop("verified", None)
            session.pop("email", None)
            session["admin_logged_in"] = True
            logger.info("Administrator successfully authenticated from IP %s", client_ip)
            return redirect(url_for("admin"))

        record_admin_login_failure(client_ip)
        logger.warning("Failed administrator login attempt from IP %s", client_ip)
        flash("Invalid administrator credentials. Access restricted to authorized personnel.", "error")

    return render_template("admin_login.html")


@auth_bp.route("/admin_logout", methods=["GET", "POST"], endpoint="admin_logout")
@auth_bp.route("/admin/logout", methods=["GET", "POST"])
def admin_logout():
    session.pop("admin_logged_in", None)
    flash("Administrator session closed.", "info")
    return redirect(url_for("admin_login"))
