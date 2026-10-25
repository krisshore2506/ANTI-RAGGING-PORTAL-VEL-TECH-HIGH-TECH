import os
import hashlib
import secrets
from flask import request

def get_client_ip() -> str:
    """
    Safely returns client IP address without trusting spoofed X-Forwarded-For headers
    unless explicit trusted reverse proxy configuration is enabled.
    """
    if os.getenv("BEHIND_TRUSTED_PROXY", "False").lower() in ("true", "1"):
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"

def generate_secure_otp() -> str:
    """
    Generates a cryptographically secure 6-digit numeric OTP in the range 100000 to 999999.
    Uses Python's standard library secrets module (CSPRNG).
    """
    return str(secrets.randbelow(900000) + 100000)

def hash_tracking_token(raw_token: str) -> str:
    """
    Computes deterministic SHA-256 hex digest of the raw tracking token
    for secure storage in anti_ragging_dev.db.
    """
    if not raw_token:
        return ""
    return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()

def verify_tracking_token(stored_hash_or_token: str, provided_token: str) -> bool:
    """
    Verifies a user-provided tracking token against the stored hash in constant time.
    Supports backwards compatibility with unhashed prototype tokens if any exist.
    """
    if not stored_hash_or_token or not provided_token:
        return False
    provided_hash = hash_tracking_token(provided_token)
    if secrets.compare_digest(stored_hash_or_token, provided_hash):
        return True
    if secrets.compare_digest(stored_hash_or_token, provided_token.strip()):
        return True
    return False
