import time
from flask import session
from backend.config.settings import (
    MAX_TRACKING_ATTEMPTS,
    LOCKOUT_DURATION_SECONDS,
    MAX_ADMIN_ATTEMPTS,
    ADMIN_LOCKOUT_SECONDS
)

tracking_ip_attempts = {}
admin_failed_attempts = {}

def check_tracking_rate_limit(client_ip: str):
    """
    Enforces attempt throttling on tracking lookups using both session and client IP
    to prevent brute-force token guessing across private/incognito browsing.
    """
    now = time.time()
    # 1. Session lockout check (persists in server-side session)
    sess_lockout = session.get("track_lockout_until", 0)
    if now < sess_lockout:
        remaining_mins = max(1, int(sess_lockout - now) // 60 + 1)
        return False, f"Too many failed verification attempts. Please wait {remaining_mins} minute(s) before trying again."

    # 2. Client IP lockout check (mitigates cookie-clearing bypasses)
    ip_record = tracking_ip_attempts.get(client_ip)
    if ip_record:
        lockout_until = ip_record.get("lockout_until", 0)
        if now < lockout_until:
            remaining_mins = max(1, int(lockout_until - now) // 60 + 1)
            return False, f"Too many failed verification attempts. Please wait {remaining_mins} minute(s) before trying again."

    return True, None

def record_tracking_failure(client_ip: str):
    """
    Increments failed tracking attempts on both session and IP, triggering temporary lockout if exceeded.
    """
    now = time.time()
    attempts = session.get("track_attempts", 0) + 1
    session["track_attempts"] = attempts
    if attempts >= MAX_TRACKING_ATTEMPTS:
        session["track_lockout_until"] = now + LOCKOUT_DURATION_SECONDS
        session["track_attempts"] = 0

    ip_rec = tracking_ip_attempts.get(client_ip, {"count": 0, "lockout_until": 0})
    ip_rec["count"] += 1
    if ip_rec["count"] >= MAX_TRACKING_ATTEMPTS:
        ip_rec["lockout_until"] = now + LOCKOUT_DURATION_SECONDS
        ip_rec["count"] = 0
    tracking_ip_attempts[client_ip] = ip_rec

def reset_tracking_attempts(client_ip: str):
    """
    Clears failure counters upon successful verification.
    """
    session["track_attempts"] = 0
    session.pop("track_lockout_until", None)
    tracking_ip_attempts.pop(client_ip, None)


def check_admin_rate_limit(client_ip: str):
    """
    Throttles administrator login attempts across both session state and client IP
    to mitigate brute-force attempts across multi-worker environments.
    """
    now = time.time()
    # 1. Session-based lockout check
    sess_lockout = session.get("admin_lockout_until", 0)
    if now < sess_lockout:
        remaining_mins = max(1, int(sess_lockout - now) // 60 + 1)
        return False, f"Too many failed administrator login attempts. Account temporarily locked for {remaining_mins} minute(s)."

    # 2. IP-based lockout check
    record = admin_failed_attempts.get(client_ip)
    if record:
        lockout_until = record.get("lockout_until", 0)
        if now < lockout_until:
            remaining_mins = max(1, int(lockout_until - now) // 60 + 1)
            return False, f"Too many failed administrator login attempts. Account temporarily locked for {remaining_mins} minute(s)."
    return True, None

def record_admin_login_failure(client_ip: str):
    """
    Records failed administrator attempt on both session and IP, enforcing lockout if threshold met.
    """
    now = time.time()
    sess_attempts = session.get("admin_failed_attempts", 0) + 1
    session["admin_failed_attempts"] = sess_attempts
    if sess_attempts >= MAX_ADMIN_ATTEMPTS:
        session["admin_lockout_until"] = now + ADMIN_LOCKOUT_SECONDS
        session["admin_failed_attempts"] = 0

    record = admin_failed_attempts.get(client_ip, {"count": 0, "lockout_until": 0})
    record["count"] += 1
    if record["count"] >= MAX_ADMIN_ATTEMPTS:
        record["lockout_until"] = now + ADMIN_LOCKOUT_SECONDS
        record["count"] = 0
    admin_failed_attempts[client_ip] = record

def reset_admin_login_failures(client_ip: str):
    """
    Clears failed administrator login counters upon successful authentication.
    """
    session.pop("admin_failed_attempts", None)
    session.pop("admin_lockout_until", None)
    admin_failed_attempts.pop(client_ip, None)

# Alias for backwards compatibility with tests
reset_admin_login_attempts = reset_admin_login_failures
