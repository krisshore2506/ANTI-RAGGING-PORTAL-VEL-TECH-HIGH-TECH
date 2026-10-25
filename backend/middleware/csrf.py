import secrets
from flask import session, request, flash, render_template, jsonify, current_app
from backend.config.settings import logger

def get_or_create_csrf_token():
    """
    Generates and returns a cryptographically secure session CSRF token.
    """
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]

def inject_csrf_token():
    """
    Makes the CSRF token accessible to all Jinja2 templates via `csrf_token`.
    """
    return {"csrf_token": get_or_create_csrf_token()}

def validate_csrf():
    """
    Validates CSRF tokens on all state-changing HTTP requests.
    Supports form fields ('csrf_token') and HTTP headers ('X-CSRFToken', 'X-CSRF-Token').
    Can be bypassed during automated test suites when CSRF_ENABLED is False.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None

    # Exempt automated testing when explicitly disabled
    if current_app.config.get("TESTING") and not current_app.config.get("CSRF_ENABLED", True):
        return None

    json_token = None
    if request.is_json:
        try:
            body = request.get_json(silent=True)
            if isinstance(body, dict):
                json_token = body.get("csrf_token")
        except Exception:
            pass

    submitted_token = (
        request.form.get("csrf_token")
        or request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or json_token
    )
    stored_token = session.get("csrf_token")

    if not submitted_token or not stored_token or not secrets.compare_digest(str(submitted_token), str(stored_token)):
        logger.warning(
            "CSRF validation failed for route '%s' from client IP '%s'.",
            request.path,
            request.remote_addr or "unknown"
        )
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("format") == "json":
            return jsonify({"error": "CSRF validation failed. Please refresh and try again."}), 403

        flash("Your security verification token has expired or is invalid. Please try again.", "error")
        return render_template(
            "error.html",
            error_title="Security Verification Failed",
            error_heading="CSRF Validation Error",
            error_message="Your security verification token was invalid or has expired. Please return to the portal and try again."
        ), 403
