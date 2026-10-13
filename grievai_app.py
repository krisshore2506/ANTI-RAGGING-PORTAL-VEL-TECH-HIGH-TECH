from flask import Flask, request, render_template, redirect, url_for, session, flash, jsonify, send_from_directory, abort
from flask_sqlalchemy import SQLAlchemy
from flask_session import Session
from werkzeug.utils import secure_filename
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
import threading
import pickle
import smtplib
import secrets
import uuid
import re
import hashlib
import time
import os
import sqlite3
import logging
import html

from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

# ================= SECURE LOGGING CONFIG =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("anti_ragging_portal")

# ================= TWILIO CONFIG =================
account_sid = os.getenv("TWILIO_ACCOUNT_SID")
auth_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_number = os.getenv("TWILIO_PHONE_NUMBER")

client = None
if account_sid and auth_token:
    try:
        client = Client(account_sid, auth_token)
        logger.info("Twilio configured successfully.")
    except Exception as twilio_err:
        logger.warning("Twilio initialization error: %s. Continuing in simulated mode.", twilio_err)
        client = None
else:
    logger.info("Twilio not configured. SMS/call features will run in simulated mode.")


# ================= SECURITY HELPERS & RATE LIMITING =================
MAX_TRACKING_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 600  # 10 minutes
tracking_ip_attempts = {}

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
    # Check if stored value matches computed hash (standard case)
    if secrets.compare_digest(stored_hash_or_token, provided_hash):
        return True
    # Backwards compatibility check for any legacy unhashed tokens
    if secrets.compare_digest(stored_hash_or_token, provided_token.strip()):
        return True
    return False

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


# ================= ADMINISTRATOR AUTH RATE LIMITING =================
MAX_ADMIN_ATTEMPTS = 5
ADMIN_LOCKOUT_SECONDS = 900  # 15 minutes
admin_failed_attempts = {}

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


# ================= ANTI-RAGGING CLASSIFIER & TRIAGE LOGIC =================
VALID_INCIDENT_CATEGORIES = [
    "Verbal or emotional harassment",
    "Physical ragging",
    "Hostel hazing or coercion",
    "Cyber-ragging",
    "Financial extortion",
    "Other / unsure"
]

def determine_incident_category(selected_category=None, text=""):
    """
    Determines the incident category.
    Prioritizes the student's explicit selection if valid.
    Falls back to deterministic textual heuristics if category is missing or 'Other / unsure'.
    """
    if selected_category:
        norm = selected_category.strip()
        for cat in VALID_INCIDENT_CATEGORIES:
            if norm.lower() == cat.lower():
                return cat

    text_lower = text.lower() if text else ""

    # 1. Physical ragging keywords
    if any(k in text_lower for k in [
        "hit", "beat", "slap", "punch", "assault", "physical", "push", "hurt", "injury", "bleeding", "bruise", "violence"
    ]):
        return "Physical ragging"

    # 2. Financial extortion
    if any(k in text_lower for k in [
        "money", "extort", "cash", "forced to pay", "pay for", "wallet", "demanding money", "bribe", "snatched money"
    ]):
        return "Financial extortion"

    # 3. Cyber-ragging
    if any(k in text_lower for k in [
        "whatsapp", "instagram", "social media", "cyber", "online", "troll", "meme", "posted photo", "screenshot", "group chat", "leaked"
    ]):
        return "Cyber-ragging"

    # 4. Hostel hazing or coercion
    if any(k in text_lower for k in [
        "hostel", "room", "corridor", "warden", "night", "forced to dance", "forced to undress", "kneel", "do chores", "seniors room", "locked in"
    ]):
        return "Hostel hazing or coercion"

    # 5. Verbal or emotional harassment
    if any(k in text_lower for k in [
        "verbal", "abused", "insult", "shout", "taunt", "humiliate", "mock", "threat", "slang", "vulgar words", "foul language"
    ]):
        return "Verbal or emotional harassment"

    return "Other / unsure"


def suggest_provisional_severity(category, text="", student_priority="Medium"):
    """
    Provisional severity suggestion used solely as an internal triage aid
    to help the Anti-Ragging Committee prioritize investigation timelines.
    THIS IS A TRIAGE AID ONLY, NOT A FACTUAL FINDING ON THE MERIT OF THE ALLEGATION.
    Options: 'Critical', 'High', 'Medium'.
    """
    text_lower = text.lower() if text else ""

    # Critical indicators (immediate threat, severe violence, confinement, weapons)
    critical_indicators = [
        "hospital", "bleeding", "weapon", "knife", "suicide", "kill", "locked in",
        "unconscious", "broken", "severe beating", "assaulted", "sexual", "emergency"
    ]
    if any(ind in text_lower for ind in critical_indicators):
        return "Critical"

    # High indicators (physical contact, extortion, repeated coercion)
    high_indicators = [
        "beat", "slap", "punch", "extort", "blackmail", "forced to",
        "threatened", "every day", "daily", "gang", "cornered", "strip"
    ]
    if category in ["Physical ragging", "Financial extortion"]:
        return "High"
    if any(ind in text_lower for ind in high_indicators):
        return "High"

    # Respect student's selected priority if specified as High/Critical
    if str(student_priority).strip().lower() in ["high", "critical"]:
        return "High"

    return "Medium" 


# ================= APP CONFIG & SESSION SECURITY =================
app = Flask(__name__)

# Mandatory Requirement A: Stable SECRET_KEY loaded from environment configuration
# Fail safely with a clear startup error when required production secrets are missing.
SECRET_KEY = os.getenv("SECRET_KEY")
FLASK_ENV = os.getenv("FLASK_ENV", "development").lower()
IS_PRODUCTION = FLASK_ENV == "production" or os.getenv("ENV", "").lower() == "production"

if not SECRET_KEY:
    raise RuntimeError(
        "CRITICAL SECURITY CONFIGURATION ERROR: SECRET_KEY environment variable is required but missing. "
        "Define a persistent, secure SECRET_KEY in your environment or .env file before running."
    )

app.secret_key = SECRET_KEY

# Mandatory Requirement A: Server-side sessions and secure cookie attributes
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_USE_SIGNER"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "False").lower() in ("true", "1")
Session(app)


# ================= DATABASE CONFIG =================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_NAME = "anti_ragging_dev.db"
DB_PATH = os.path.join(BASE_DIR, DB_NAME)

env_db_url = os.getenv("DATABASE_URL", "").strip()
if env_db_url and env_db_url.lower() not in ("your_db_url", "none", ""):
    if env_db_url.startswith("postgres://"):
        env_db_url = env_db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = env_db_url
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + DB_PATH

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# ================= EVIDENCE UPLOAD CONFIG =================
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_SIZE_BYTES", 25 * 1024 * 1024))  # 25 MB
EVIDENCE_UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "evidence")
os.makedirs(EVIDENCE_UPLOAD_FOLDER, exist_ok=True)

# Strict Student Email Pattern: ^vh[0-9]+@velhightech\.com$
STUDENT_EMAIL_REGEX = re.compile(r"^vh[0-9]+@velhightech\.com$")

ALLOWED_EVIDENCE_EXTENSIONS = {
    ".jpg": ("photo", "image/jpeg"),
    ".jpeg": ("photo", "image/jpeg"),
    ".png": ("photo", "image/png"),
    ".webp": ("photo", "image/webp"),
    ".mp3": ("audio", "audio/mpeg"),
    ".wav": ("audio", "audio/wav"),
    ".ogg": ("audio", "audio/ogg"),
    ".m4a": ("audio", "audio/mp4"),
    ".webm": ("audio", "audio/webm"),
    ".mp4": ("video", "video/mp4"),
    ".pdf": ("document", "application/pdf"),
}


def format_file_size(num_bytes):
    if not num_bytes:
        return "0 B"
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} TB"


@app.template_filter("format_file_size")
def template_format_file_size(num_bytes):
    return format_file_size(num_bytes)


def inspect_file_signature(ext, header, is_voice=False):
    """
    Verifies magic-byte header against claimed extension.
    Returns (category, mime_type) if verified, or (None, None).
    """
    ext = ext.lower().strip()
    if ext in [".jpg", ".jpeg"]:
        if header.startswith(b"\xff\xd8\xff"):
            return "photo", "image/jpeg"
    elif ext == ".png":
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "photo", "image/png"
    elif ext == ".webp":
        if len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return "photo", "image/webp"
    elif ext == ".pdf":
        if header.startswith(b"%PDF"):
            return "document", "application/pdf"
    elif ext == ".webm":
        if header.startswith(b"\x1a\x45\xdf\xa3"):
            cat = "audio" if is_voice else "video"
            mime = "audio/webm" if is_voice else "video/webm"
            return cat, mime
    elif ext == ".mp3":
        if header.startswith(b"ID3") or header.startswith(b"\xff\xfb") or header.startswith(b"\xff\xf3") or header.startswith(b"\xff\xf2"):
            return "audio", "audio/mpeg"
    elif ext == ".wav":
        if len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WAVE":
            return "audio", "audio/wav"
    elif ext == ".ogg":
        if header.startswith(b"OggS"):
            return "audio", "audio/ogg"
    elif ext in [".mp4", ".m4a"]:
        if len(header) >= 8 and (header[4:8] == b"ftyp" or header[4:8] == b"moov" or header[0:4] in (b"\x00\x00\x00\x18", b"\x00\x00\x00\x20")):
            cat = "audio" if ext == ".m4a" else "video"
            mime = "audio/mp4" if ext == ".m4a" else "video/mp4"
            return cat, mime
    return None, None


def save_uploaded_evidence(file_obj, complaint_id, is_voice=False):
    """
    Validates, randomizes filename, and securely saves an uploaded evidence file.
    Returns (ComplaintEvidence, saved_file_path) on success, or raises ValueError.
    """
    if not file_obj or not file_obj.filename:
        return None, None

    orig_name = secure_filename(file_obj.filename)
    if not orig_name:
        orig_name = f"voice_statement_{int(time.time())}.webm" if is_voice else f"evidence_{int(time.time())}.bin"

    ext = os.path.splitext(orig_name)[1].lower()
    if not ext and is_voice:
        ext = ".webm"
        orig_name += ext

    if ext not in ALLOWED_EVIDENCE_EXTENSIONS:
        raise ValueError(f"File extension '{ext}' is not permitted for evidence attachments.")

    header = file_obj.read(512)
    file_obj.seek(0)

    category, mime_type = inspect_file_signature(ext, header, is_voice=is_voice)
    if not category:
        raise ValueError(f"File '{orig_name}' does not match its expected file signature or format.")

    storage_name = f"{uuid.uuid4().hex}{ext}"
    dest_path = os.path.join(EVIDENCE_UPLOAD_FOLDER, storage_name)

    file_obj.save(dest_path)
    file_size = os.path.getsize(dest_path)

    evidence_record = ComplaintEvidence(
        complaint_id=complaint_id,
        file_type=category,
        original_filename=orig_name,
        storage_filename=storage_name,
        mime_type=mime_type,
        file_size_bytes=file_size,
        uploaded_at=datetime.utcnow()
    )
    return evidence_record, dest_path


@app.errorhandler(413)
def request_entity_too_large(error):
    logger.warning("Upload rejected: total payload exceeded 25 MB limit (413 Request Entity Too Large).")
    return render_template(
        "error.html",
        error_title="Upload Limit Exceeded",
        error_heading="File Size Exceeded Limit",
        error_message="The total size of your uploaded evidence files exceeds the 25 MB limit. Please reduce file sizes or select fewer files and try again."
    ), 413



# ================= NATIVE CSRF PROTECTION =================
def get_or_create_csrf_token():
    """
    Generates and returns a cryptographically secure session CSRF token.
    """
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]

@app.context_processor
def inject_csrf_token():
    """
    Makes the CSRF token accessible to all Jinja2 templates via `csrf_token`.
    """
    return {"csrf_token": get_or_create_csrf_token()}

@app.before_request
def validate_csrf():
    """
    Validates CSRF tokens on all state-changing HTTP requests.
    Supports form fields ('csrf_token') and HTTP headers ('X-CSRFToken', 'X-CSRF-Token').
    Can be bypassed during automated test suites when CSRF_ENABLED is False.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None

    # Exempt automated testing when explicitly disabled
    if app.config.get("TESTING") and not app.config.get("CSRF_ENABLED", True):
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


# ================= DATABASE MODELS =================
class Complaint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_email = db.Column(db.String(120), nullable=False)
    complaint_title = db.Column(db.String(150), nullable=True)
    complaint_text = db.Column(db.Text, nullable=False)
    department = db.Column(db.String(100), nullable=False)
    assigned_cleaner = db.Column(db.String(100))
    status = db.Column(db.String(20), default="Open")
    priority = db.Column(db.String(20), default="Medium")
    escalation_level = db.Column(db.Integer, default=0)
    admin_remarks = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    # Anti-Ragging Specific Attributes
    incident_type = db.Column(db.String(100), nullable=True)
    severity_level = db.Column(db.String(50), nullable=True, default="Medium")
    incident_date_time = db.Column(db.String(100), nullable=True)
    incident_location = db.Column(db.String(150), nullable=True)
    accused_details = db.Column(db.Text, nullable=True)
    witness_details = db.Column(db.Text, nullable=True)
    is_confidential = db.Column(db.Boolean, default=False)
    tracking_token = db.Column(db.String(64), nullable=True, index=True)
    committee_assigned = db.Column(db.String(100), nullable=True)
    evidence_files = db.relationship('ComplaintEvidence', backref='complaint', lazy=True, cascade='all, delete-orphan')



class ComplaintEvidence(db.Model):
    __tablename__ = "complaint_evidence"

    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(db.Integer, db.ForeignKey("complaint.id"), nullable=False, index=True)
    file_type = db.Column(db.String(50), nullable=False)  # 'photo', 'audio', 'video', 'document'
    original_filename = db.Column(db.String(255), nullable=False)
    storage_filename = db.Column(db.String(255), nullable=False, unique=True)
    mime_type = db.Column(db.String(100), nullable=False)
    file_size_bytes = db.Column(db.Integer, nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "complaint_id": self.complaint_id,
            "file_type": self.file_type,
            "original_filename": self.original_filename,
            "mime_type": self.mime_type,
            "file_size_bytes": self.file_size_bytes,
            "file_size_human": format_file_size(self.file_size_bytes),
            "uploaded_at": self.uploaded_at.strftime("%Y-%m-%d %H:%M UTC") if self.uploaded_at else ""
        }


class Cleaner(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    department = db.Column(db.String(50), nullable=False)   # Hostel / Campus
    area = db.Column(db.String(100), nullable=False)        # Hostel_Room / Campus_Lab / etc.
    phone = db.Column(db.String(20), nullable=False)


class CallLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(db.Integer, nullable=False)
    cleaner_name = db.Column(db.String(100), nullable=False)
    phone_number = db.Column(db.String(20), nullable=False)
    call_status = db.Column(db.String(50), default="Triggered")
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


class SMSLog(db.Model):
    __tablename__ = "sms_logs"

    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(db.Integer, nullable=False)
    to_number = db.Column(db.String(20), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime)




class Notification(db.Model):
    __tablename__ = "notification"

    id = db.Column(db.Integer, primary_key=True)
    recipient_role = db.Column(db.String(20), nullable=False, index=True)  # 'admin' or 'student'
    recipient_email = db.Column(db.String(120), nullable=True, index=True)  # student email (isolated) or None
    complaint_id = db.Column(db.Integer, nullable=True, index=True)
    notification_type = db.Column(db.String(50), nullable=False)  # 'new_complaint', 'status_update', 'pending_reminder'
    title = db.Column(db.String(150), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    is_read = db.Column(db.Boolean, default=False, nullable=False, index=True)
    read_at = db.Column(db.DateTime, nullable=True)
    dedup_key = db.Column(db.String(120), nullable=True, unique=True, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "recipient_role": self.recipient_role,
            "recipient_email": self.recipient_email,
            "complaint_id": self.complaint_id,
            "notification_type": self.notification_type,
            "title": self.title,
            "message": self.message,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else None,
            "is_read": self.is_read,
            "read_at": self.read_at.strftime("%Y-%m-%d %H:%M") if self.read_at else None,
            "dedup_key": self.dedup_key
        }

# ================= EMAIL / ADMIN CONFIG =================
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")


# ================= HELPER FUNCTIONS =================
def send_sms_simulated(complaint_id, to_number, message):
    log_entry = SMSLog(
        complaint_id=complaint_id,
        to_number=to_number,
        message=message,
        status="Sent (Simulated)",
        timestamp=datetime.utcnow()
    )
    db.session.add(log_entry)
    db.session.commit()


def send_otp_email(email, otp):
    """
    Sends a responsive, professional HTML and plain-text verification OTP email to the student.
    Matches the portal's brand palette (navy/blue) with clean typography and high contrast.
    Does not log passwords or confidential data.
    """
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        raise ValueError("SMTP_EMAIL or SMTP_PASSWORD is not configured.")

    safe_otp = html.escape(str(otp).strip())

    if os.getenv("DEV_SIMULATE_EMAIL", "False").lower() in ("true", "1"):
        logger.info("[DEV SIMULATED EMAIL] Generated OTP %s for %s", safe_otp, email)
        otp_file = os.getenv("DEV_OTP_FILE")
        if otp_file:
            try:
                with open(otp_file, "w", encoding="utf-8") as f:
                    f.write(safe_otp)
            except Exception:
                pass
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Anti-Ragging Portal | Email Verification"
    msg["From"] = SMTP_EMAIL
    msg["To"] = email

    # Plain text version for minimal/text-only email clients
    plain_text = (
        "ANTI-RAGGING PORTAL\n"
        "Vel Tech High Tech Dr. Rangarajan Dr. Sakunthala Engineering College\n\n"
        "VERIFY YOUR EMAIL ADDRESS\n\n"
        "Use the verification code below to verify your official college email address "
        "and securely access the Anti-Ragging Portal.\n\n"
        f"Verification Code: {safe_otp}\n\n"
        "This code expires in 1 minute.\n\n"
        "SECURITY NOTICE:\n"
        "Do not share this code with anyone. Committee officers will never ask for your "
        "verification code. If you did not request this verification code, please ignore this email.\n\n"
        "--------------------------------------------------\n"
        "Student Safety | Confidentiality | Secure Access\n"
        "Official Anti-Ragging Monitoring System\n"
    )

    # Polished, responsive HTML version matching website design tokens
    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Verify Your Email Address</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased; color: #0f172a;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f1f5f9; padding: 30px 12px;">
        <tr>
            <td align="center">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width: 580px; background-color: #ffffff; border-radius: 10px; border: 1px solid #e2e8f0; box-shadow: 0 4px 12px rgba(15, 23, 42, 0.06); overflow: hidden;">
                    <!-- Brand Header -->
                    <tr>
                        <td style="background-color: #1e3a8a; padding: 26px 24px; text-align: center; border-bottom: 3px solid #3b82f6;">
                            <div style="color: #bfdbfe; font-size: 11px; font-weight: 600; letter-spacing: 1.2px; text-transform: uppercase; margin-bottom: 5px;">
                                Vel Tech High Tech Dr. Rangarajan Dr. Sakunthala Engineering College
                            </div>
                            <h1 style="color: #ffffff; margin: 0; font-size: 21px; font-weight: 700; letter-spacing: 0.5px;">
                                ANTI RAGGING PORTAL
                            </h1>
                        </td>
                    </tr>

                    <!-- Main Content Body -->
                    <tr>
                        <td style="padding: 34px 28px 28px 28px;">
                            <h2 style="margin: 0 0 12px 0; color: #0f172a; font-size: 20px; font-weight: 700; line-height: 1.3;">
                                Verify Your Email Address
                            </h2>
                            <p style="margin: 0 0 22px 0; color: #334155; font-size: 14.5px; line-height: 1.6;">
                                Use the verification code below to verify your official college email address and securely access the Anti-Ragging Portal.
                            </p>

                            <!-- High-Contrast OTP Card -->
                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 24px 0;">
                                <tr>
                                    <td align="center" style="background-color: #f8fafc; border: 1.5px solid #cbd5e1; border-radius: 8px; padding: 22px 16px; text-align: center;">
                                        <span style="display: block; color: #64748b; font-size: 11.5px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px;">
                                            One-Time Verification Code
                                        </span>
                                        <div style="font-family: 'SF Pro Text', -apple-system, Consolas, Monaco, monospace; font-size: 34px; font-weight: 700; letter-spacing: 8px; color: #1e3a8a; line-height: 1; padding: 6px 0;">
                                            {safe_otp}
                                        </div>
                                        <div style="margin-top: 10px;">
                                            <span style="display: inline-block; background-color: #fef3c7; color: #b45309; border: 1px solid #fde68a; font-size: 12.5px; font-weight: 600; padding: 4px 12px; border-radius: 4px;">
                                                This code expires in 1 minute.
                                            </span>
                                        </div>
                                    </td>
                                </tr>
                            </table>

                            <!-- Security Advisory Callout -->
                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top: 20px;">
                                <tr>
                                    <td style="background-color: #f8fafc; border-left: 4px solid #3b82f6; border-radius: 0 6px 6px 0; padding: 12px 16px;">
                                        <p style="margin: 0; color: #475569; font-size: 12.5px; line-height: 1.5;">
                                            <strong style="color: #1e3a8a;">Security Notice:</strong> Do not share this one-time code with anyone. Committee officers will never ask for your verification code. If you did not request this verification code, please ignore this email.
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f8fafc; border-top: 1px solid #e2e8f0; padding: 20px 24px; text-align: center;">
                            <p style="margin: 0 0 6px 0; color: #1e40af; font-size: 12px; font-weight: 600; letter-spacing: 0.5px;">
                                Student Safety | Confidentiality | Secure Access
                            </p>
                            <p style="margin: 0; color: #94a3b8; font-size: 11px; line-height: 1.4;">
                                Vel Tech High Tech Dr. Rangarajan Dr. Sakunthala Engineering College<br>
                                Official Anti-Ragging Committee Monitoring System
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.send_message(msg)

    logger.info("OTP verification email successfully dispatched to student.")


def find_cleaner(main_dept, sub_dept):
    return Cleaner.query.filter_by(
        department=main_dept,
        area=sub_dept
    ).first()


def send_sms(to_number, message):
    if not client:
        logger.info("Twilio not configured. Skipping real SMS.")
        return False

    try:
        client.messages.create(
            body=message,
            from_=twilio_number,
            to=to_number
        )
        return True
    except Exception as e:
        logger.error("SMS dispatch error: %s", str(e))
        return False


def trigger_auto_call(complaint_id, cleaner_name, phone_number):
    call_log = CallLog(
        complaint_id=complaint_id,
        cleaner_name=cleaner_name,
        phone_number=phone_number,
        call_status="Triggered"
    )

    db.session.add(call_log)
    db.session.commit()
    return True


def send_status_update_email(student_email, complaint_id, new_status):
    if not student_email or not SMTP_EMAIL or not SMTP_PASSWORD:
        return

    subject = f"Anti-Ragging Incident Status Update (ID #{complaint_id})"
    status_lower = new_status.strip().lower()

    if "under review" in status_lower or "progress" in status_lower or "assigned" in status_lower:
        title = "Incident Report Under Review"
        message = "Your incident report is currently under active review by the Anti-Ragging Committee."
        status_color = "#f59e0b"
    elif "action taken" in status_lower:
        title = "Action Taken on Incident Report"
        message = "Formal institutional action has been taken by the Anti-Ragging Committee regarding this report."
        status_color = "#3b82f6"
    elif status_lower in ["closed", "resolved"]:
        title = "Incident Report Closed"
        message = "This incident report matter has been concluded and formally closed by the Anti-Ragging Committee."
        status_color = "#16a34a"
    elif "open" in status_lower:
        title = "Incident Report Registered"
        message = "Your incident report has been registered and queued for committee review."
        status_color = "#3b82f6"
    else:
        title = f"Incident Status: {new_status}"
        message = f"Your incident report status has been updated to: {new_status}."
        status_color = "#1e40af"

    safe_status = html.escape(str(new_status).strip())
    safe_complaint_id = html.escape(str(complaint_id).strip())
    safe_title = html.escape(title)
    safe_message = html.escape(message)

    plain_text = (
        "ANTI-RAGGING PORTAL - STATUS UPDATE\n"
        "Vel Tech High Tech Dr. Rangarajan Dr. Sakunthala Engineering College\n\n"
        f"{title}\n"
        f"{message}\n\n"
        f"Complaint ID: #{complaint_id}\n"
        f"Updated Status: {new_status}\n\n"
        "You can track your incident report status at any time on the portal using your "
        "Reference ID and Tracking Token.\n\n"
        "Regards,\n"
        "Anti-Ragging Committee\n"
        "Vel Tech High Tech Engineering College\n"
    )

    html_body = f"""
    <html>
    <body style="margin:0; padding:0; background-color:#f4f6f9; font-family:Arial, sans-serif;">
        <div style="max-width:600px; margin:40px auto; background:#ffffff;
                    border-radius:12px; box-shadow:0 4px 15px rgba(0,0,0,0.1);
                    overflow:hidden;">
            <div style="background:#1e88c9; padding:20px; text-align:center;">
                <h2 style="color:#ffffff; margin:0;">Anti-Ragging Portal</h2>
                <p style="color:#e0f2fe; margin:4px 0 0;">Complaint Status Update</p>
            </div>

            <div style="padding:30px; color:#333;">
                <h3>{safe_title}</h3>
                <p>{safe_message}</p>

                <p><strong>Complaint ID:</strong> #{safe_complaint_id}</p>

                <p>
                    <strong>Status:</strong>
                    <span style="
                        display:inline-block;
                        padding:6px 12px;
                        border-radius:12px;
                        background:{status_color};
                        color:white;
                        font-weight:bold;
                        font-size:13px;
                    ">
                        {safe_status}
                    </span>
                </p>

                <p style="margin-top:20px;">
                    Regards,<br><strong>Anti-Ragging Committee</strong>
                </p>
            </div>

            <div style="background:#f1f5f9; padding:14px; text-align:center;
                        font-size:12px; color:#555;">
                &copy; 2026 Anti-Ragging Portal | Vel Tech High Tech Engineering College
            </div>
        </div>
    </body>
    </html>
    """

    if os.getenv("DEV_SIMULATE_EMAIL", "False").lower() in ("true", "1"):
        logger.info("[DEV SIMULATED EMAIL] Status update for incident #%s -> %s", complaint_id, new_status)
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_EMAIL
        msg["To"] = student_email
        msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.send_message(msg)

        logger.info("Status update email successfully sent for incident #%s", complaint_id)

    except Exception as e:
        logger.error("Failed to send status email for incident #%s: %s", complaint_id, str(e))


# ================= NOTIFICATION / REMINDER CONFIG & HELPERS =================
REMINDER_OPEN_HOURS = int(os.getenv("REMINDER_OPEN_HOURS", "24"))
REMINDER_IN_PROGRESS_HOURS = int(os.getenv("REMINDER_IN_PROGRESS_HOURS", "72"))
REMINDER_CHECK_INTERVAL_SECONDS = int(os.getenv("REMINDER_CHECK_INTERVAL_SECONDS", "3600"))


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


_scheduler_thread = None
_scheduler_stop_event = threading.Event()


def _reminder_daemon_loop():
    logger.info("Pending-action reminder daemon started (interval: %ss).", REMINDER_CHECK_INTERVAL_SECONDS)
    while not _scheduler_stop_event.is_set():
        try:
            with app.app_context():
                process_pending_action_reminders()
        except Exception as e:
            logger.error("Reminder daemon execution error: %s", str(e))
        _scheduler_stop_event.wait(timeout=float(REMINDER_CHECK_INTERVAL_SECONDS))


def start_reminder_scheduler():
    global _scheduler_thread
    if os.getenv("DISABLE_REMINDER_SCHEDULER", "False").lower() in ("true", "1") or app.config.get("TESTING"):
        return
    # Guard against duplicate scheduler threads in Flask development reloader
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    if _scheduler_thread is None or not _scheduler_thread.is_alive():
        _scheduler_thread = threading.Thread(target=_reminder_daemon_loop, daemon=True, name="ARC-Reminder-Scheduler")
        _scheduler_thread.start()


# ================= ROUTES =================
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/student_login")
def student_login():
    return render_template("student_login.html")


# ================= SEND OTP =================
@app.route("/send_otp", methods=["POST"])
def send_otp():
    # Mandatory Requirement B: Normalize email address before college-domain validation
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

    # Mandatory Requirement B: Prevent trivial resend abuse (30s cooldown between new OTP generations)
    now = time.time()
    last_otp_time = session.get("otp_time", 0)
    if now - last_otp_time < 30 and session.get("email") == email:
        wait_sec = int(30 - (now - last_otp_time)) + 1
        flash(f"A verification code was recently generated. Please wait {wait_sec} second(s) before requesting again.", "error")
        return redirect(url_for("verify_otp_page"))

    # Clear any prior authenticated identity so stale sessions cannot linger
    session.pop("verified", None)
    session.pop("admin_logged_in", None)

    otp = generate_secure_otp()
    session["otp"] = otp
    session["email"] = email
    session["otp_time"] = now
    session["otp_resend_count"] = 0

    try:
        send_otp_email(email, otp)
        return redirect(url_for("verify_otp_page"))
    except Exception as e:
        # Mandatory Requirement D: Safe user-facing error message without raw traceback exposure
        logger.error("Failed to deliver OTP email: %s", str(e))
        flash("Unable to send verification code. Please check server connectivity or try again shortly.", "error")
        return render_template("student_login.html")


# ================= OTP PAGE (GET) =================
@app.route("/verify_otp", methods=["GET"])
def verify_otp_page():
    return render_template("verify_otp.html", email=session.get("email"))


# ================= VERIFY OTP (POST) =================
@app.route("/verify_otp", methods=["POST"])
def verify_otp():
    user_otp = request.form.get("otp", "").strip()
    stored_otp = session.get("otp")
    email = session.get("email")
    otp_time = session.get("otp_time", 0)

    if not stored_otp or not otp_time:
        flash("No active verification code found. Please request a new code.", "error")
        return render_template("verify_otp.html", email=email)

    now = time.time()
    # Mandatory Requirement B: Enforce stated 60-second OTP expiry
    if now - otp_time > 60:
        session.pop("otp", None)
        session.pop("otp_time", None)
        flash("Verification code has expired (valid for 60 seconds). Please request a new code.", "error")
        return render_template("verify_otp.html", email=email)

    # Mandatory Requirement B: Single-use invalidation and constant-time comparison
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


# ================= RESEND OTP =================
@app.route("/resend_otp", methods=["POST"])
def resend_otp():
    email = session.get("email")

    if not email:
        flash("Session expired. Please enter your email again.", "error")
        return redirect(url_for("student_login"))

    now = time.time()
    last_otp_time = session.get("otp_time", 0)
    resend_count = session.get("otp_resend_count", 0)

    # Mandatory Requirement B: Review OTP resend behavior and prevent trivial resend abuse
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
        send_otp_email(email, new_otp)
        flash("A new verification code has been sent to your email (valid for 60 seconds).", "success")
    except Exception as e:
        # Mandatory Requirement D: Safe user-facing error message
        logger.error("Failed to resend OTP email: %s", str(e))
        flash("Unable to resend verification code. Please try again shortly.", "error")

    return redirect(url_for("verify_otp_page"))


# ================= STUDENT DASHBOARD =================
@app.route("/student_dashboard")
def student_dashboard():
    # Mandatory Requirement C: Check authentication and student ownership
    if not session.get("verified") or not session.get("email"):
        return redirect(url_for("student_login"))

    email = session.get("email")
    complaints = Complaint.query.filter_by(student_email=email).order_by(Complaint.timestamp.desc()).all()

    total = len(complaints)
    open_count = sum(1 for c in complaints if c.status.strip().lower() == "open")
    in_progress = sum(1 for c in complaints if "progress" in c.status.strip().lower() or "assigned" in c.status.strip().lower())
    resolved = sum(1 for c in complaints if c.status.strip().lower() in ["resolved", "closed"])

    # Notifications for student
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


# ================= STUDENT LOGOUT =================
@app.route("/student_logout")
def student_logout():
    # Mandatory Requirement A: Invalidate student session
    session.clear()
    return redirect(url_for("home"))


# ================= COMPLAINT FORM =================
@app.route("/complaint_form")
def complaint_form():
    # Mandatory Requirement C: Check authentication
    if not session.get("verified") or not session.get("email"):
        return redirect(url_for("student_login"))
    return render_template("complaint_form.html", email=session.get("email"))


# ================= PREDICT / SUBMIT COMPLAINT =================
@app.route("/predict", methods=["POST"])
def predict():
    # Mandatory Requirement C: Check authentication
    if not session.get("verified") or not session.get("email"):
        return redirect(url_for("student_login"))

    # Required form inputs
    complaint_text = request.form.get("complaint", "").strip()
    selected_category = request.form.get("incident_type", "").strip()
    incident_date_time = request.form.get("incident_date_time", "").strip()
    location = request.form.get("location", "").strip()
    location_details = request.form.get("location_details", "").strip()
    complaint_title = request.form.get("title", "").strip()
    student_priority = request.form.get("priority", "Medium").strip()
    email = session.get("email")

    # Optional fields
    accused_details = request.form.get("accused_details", "").strip() or None
    witness_details = request.form.get("witness_details", "").strip() or None
    is_confidential = request.form.get("is_confidential") in ["true", "True", "1", "on", True]
    declaration_agreed = request.form.get("accuracy_declaration") in ["true", "True", "1", "on", True]

    # Input validation
    if not complaint_text:
        flash("Please provide a detailed description of the incident.", "error")
        return redirect(url_for("complaint_form"))

    if not declaration_agreed:
        flash("You must confirm the truthfulness declaration before submitting a report.", "error")
        return redirect(url_for("complaint_form"))

    if not incident_date_time:
        incident_date_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Combine location and specific sub-location details cleanly
    if location_details and location_details.lower() not in location.lower():
        full_location = f"{location} ({location_details})"
    else:
        full_location = location or "Campus Premises"

    # 1. Deterministic Incident Category Handling
    incident_category = determine_incident_category(selected_category, complaint_text)

    # Automatically derive title if not supplied
    if not complaint_title:
        complaint_title = f"{incident_category} Report - {datetime.utcnow().strftime('%d %b %Y')}"

    # 2. Transparent Provisional Severity Triage (Queue prioritization aid, not a finding of fact)
    provisional_severity = suggest_provisional_severity(incident_category, complaint_text, student_priority)

    # 3. Unpredictable Cryptographic Tracking Token using Python's secrets module
    raw_token = f"ARC-{secrets.token_urlsafe(16)}"
    stored_token_hash = hash_tracking_token(raw_token)

    # 4. Guarantee Non-Suppression:
    # All submissions are preserved with status 'Open' for Authorized Committee Review
    initial_status = "Open"
    committee_assigned = "Anti-Ragging Committee"

    complaint = Complaint(
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

    # Process and securely store optional evidence attachments and voice recordings
    saved_file_paths = []
    try:
        # Multi-file evidence attachments
        evidence_files = request.files.getlist("evidence_files")
        for f in evidence_files:
            if f and f.filename and f.filename.strip():
                ev_record, f_path = save_uploaded_evidence(f, complaint.id, is_voice=False)
                if ev_record and f_path:
                    saved_file_paths.append(f_path)
                    db.session.add(ev_record)

        # Voice recording from browser microphone
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

    # Persistent Admin Notification for new incident report
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



# ================= EVIDENCE ACCESS ROUTE =================
@app.route("/evidence/<int:evidence_id>")
def access_evidence(evidence_id):
    """
    Secure evidence delivery route.
    Enforces authorization: authenticated admin OR verified student owner of the complaint.
    Guards against path traversal, missing files, and unauthorized data leakage.
    """
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
    file_path = os.path.join(EVIDENCE_UPLOAD_FOLDER, safe_storage_name)

    if not os.path.exists(file_path):
        logger.warning("Evidence file missing from disk: %s", file_path)
        return render_template(
            "error.html",
            error_title="File Unavailable",
            error_heading="Evidence File Not Found",
            error_message="The evidence file could not be located on the server storage."
        ), 404

    as_attachment = request.args.get("download") in ("1", "true")
    return send_from_directory(
        EVIDENCE_UPLOAD_FOLDER,
        safe_storage_name,
        mimetype=evidence.mime_type,
        as_attachment=as_attachment,
        download_name=evidence.original_filename
    )


# ================= ADMIN LOGIN =================
@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        client_ip = get_client_ip()

        # Mandatory Requirement C: Rate limiting on administrator login
        allowed, lockout_msg = check_admin_rate_limit(client_ip)
        if not allowed:
            flash(lockout_msg, "error")
            return render_template("admin_login.html")

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        # Mandatory Requirement C: Constant-time comparison
        valid_email = ADMIN_EMAIL and secrets.compare_digest(email.lower(), ADMIN_EMAIL.lower())
        valid_password = ADMIN_PASSWORD and secrets.compare_digest(password, ADMIN_PASSWORD)

        if valid_email and valid_password:
            reset_admin_login_failures(client_ip)
            # Invalidate any existing student identity to isolate administrator session
            session.pop("verified", None)
            session.pop("email", None)
            session["admin_logged_in"] = True
            logger.info("Administrator successfully authenticated from IP %s", client_ip)
            return redirect(url_for("admin"))

        record_admin_login_failure(client_ip)
        logger.warning("Failed administrator login attempt from IP %s", client_ip)
        flash("Invalid admin credentials", "error")

    return render_template("admin_login.html")


# ================= ANTI-RAGGING COMMITTEE (ARC) DASHBOARD =================
@app.route("/admin", methods=["GET", "POST"])
def admin():
    # Mandatory Requirement C: Check administrator authorization
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        complaint_id = request.form.get("complaint_id")
        new_status = request.form.get("status", "").strip()
        remarks = request.form.get("admin_remarks", "").strip()

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
            if target_status in valid_statuses:
                complaint.status = target_status

            complaint.admin_remarks = remarks
            complaint.timestamp = datetime.utcnow()
            db.session.commit()

            # Persistent Student Notification upon successful status update
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
                send_status_update_email(complaint.student_email, complaint.id, complaint.status)
            except Exception as notify_err:
                logger.error("Failed to dispatch status update notification: %s", str(notify_err))
            flash(f"Incident #{complaint.id} updated: Status is now '{complaint.status}'.", "success")

        return redirect(url_for("admin"))

    # Summary counts using actual database records
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

    # Incident category distribution for analytics
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

    # Admin notifications and pending action queue
    admin_notifs = Notification.query.filter_by(
        recipient_role="admin"
    ).order_by(Notification.created_at.desc()).limit(20).all()

    admin_unread_count = Notification.query.filter_by(
        recipient_role="admin",
        is_read=False
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


# ================= AUTHORIZED COMMITTEE COMPLAINT DETAILS =================
@app.route("/admin/complaint/<int:complaint_id>", methods=["GET", "POST"])
def admin_complaint_details(complaint_id):
    # Mandatory Requirement C: Check administrator authorization
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
        if target_status in valid_statuses:
            complaint.status = target_status
        complaint.admin_remarks = remarks
        complaint.timestamp = datetime.utcnow()
        db.session.commit()

        # Persistent Student Notification upon successful status update
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
            send_status_update_email(complaint.student_email, complaint.id, complaint.status)
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


# ================= ADMIN LOGS =================
@app.route("/admin/logs")
def admin_logs():
    # Mandatory Requirement C: Check administrator authorization
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    status_filter = request.args.get("status")

    cleaning_query = Complaint.query.filter(
        Complaint.department.like("%Hostel%") |
        Complaint.department.like("%Campus%")
    )

    if status_filter:
        cleaning_query = cleaning_query.filter(Complaint.status == status_filter)

    cleaning_complaints = cleaning_query.all()

    for complaint in cleaning_complaints:
        failed_calls = CallLog.query.filter(
            CallLog.complaint_id == complaint.id,
            CallLog.call_status != "Connected"
        ).count()

        if failed_calls >= 3:
            complaint.escalation_level = 3
        elif failed_calls == 2:
            complaint.escalation_level = 2
        elif failed_calls == 1:
            complaint.escalation_level = 1
        else:
            complaint.escalation_level = 0

    db.session.commit()

    cleaning_ids = [c.id for c in cleaning_complaints]

    sms_logs = SMSLog.query.filter(
        SMSLog.complaint_id.in_(cleaning_ids)
    ).order_by(SMSLog.timestamp.desc()).all()

    call_logs = CallLog.query.filter(
        CallLog.complaint_id.in_(cleaning_ids)
    ).order_by(CallLog.timestamp.desc()).all()

    return render_template(
        "admin_logs.html",
        sms_logs=sms_logs,
        call_logs=call_logs
    )


@app.route("/update_call_status", methods=["POST"])
def update_call_status():
    # Mandatory Requirement C: Check administrator authorization
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    call_id = request.form.get("call_id")
    new_status = request.form.get("call_status")

    call = CallLog.query.get(call_id)
    if call:
        call.call_status = new_status
        db.session.commit()

    return redirect(url_for("admin_logs"))


@app.route("/admin_logout")
def admin_logout():
    # Mandatory Requirement A: Invalidate administrator session
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))


# ================= SECURE TRACK COMPLAINT =================
@app.route("/track", methods=["GET", "POST"])
def track():
    complaint = None
    error_message = None
    lookup_attempted = False

    client_ip = get_client_ip()

    # 1. Rate Limiting / Attempt Throttling check
    allowed, lockout_msg = check_tracking_rate_limit(client_ip)
    if not allowed:
        return render_template(
            "track.html",
            complaint=None,
            error_message=lockout_msg,
            lookup_attempted=True
        )

    # 2. Extract input parameters
    complaint_id_str = None
    provided_token = None

    if request.method == "POST":
        complaint_id_str = request.form.get("complaint_id", "").strip()
        provided_token = request.form.get("tracking_token", "").strip()
    else:
        complaint_id_str = request.args.get("id", "").strip()
        provided_token = request.args.get("token", "").strip()

    # If neither parameter provided, render empty verification lookup form
    if not complaint_id_str and not provided_token:
        return render_template(
            "track.html",
            complaint=None,
            error_message=None,
            lookup_attempted=False
        )

    lookup_attempted = True

    # 3. Validate ticket ID format
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

    # 4. Query record from isolated database
    complaint = Complaint.query.get(complaint_id)

    # 5. Dual Authorization Evaluation:
    # A) Authenticated student session owns this complaint
    is_authenticated_owner = (
        session.get("verified") and
        session.get("email") and
        complaint is not None and
        complaint.student_email == session.get("email")
    )

    # B) Cryptographically verified tracking token
    is_valid_token = (
        complaint is not None and
        provided_token and
        verify_tracking_token(complaint.tracking_token, provided_token)
    )

    if not is_authenticated_owner and not is_valid_token:
        # Uniform generic error prevents ticket existence enumeration
        record_tracking_failure(client_ip)
        return render_template(
            "track.html",
            complaint=None,
            error_message="Unable to verify incident ticket. Please verify your Reference ID and Tracking Token.",
            lookup_attempted=True
        )

    # Success: reset failure throttle counter
    reset_tracking_attempts(client_ip)

    return render_template(
        "track.html",
        complaint=complaint,
        error_message=None,
        lookup_attempted=True
    )



# ================= NOTIFICATION ROUTES: STUDENT =================
@app.route("/student/notifications", methods=["GET"])
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


@app.route("/student/notifications/<int:notification_id>/read", methods=["POST"])
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


@app.route("/student/notifications/mark_all_read", methods=["POST"])
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


# ================= NOTIFICATION ROUTES: ADMIN =================
@app.route("/admin/notifications", methods=["GET"])
def admin_notifications():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401

    notifs = Notification.query.filter_by(
        recipient_role="admin"
    ).order_by(Notification.created_at.desc()).limit(50).all()

    unread_count = Notification.query.filter_by(
        recipient_role="admin",
        is_read=False
    ).count()

    return jsonify({
        "notifications": [n.to_dict() for n in notifs],
        "unread_count": unread_count
    })


@app.route("/admin/notifications/<int:notification_id>/read", methods=["POST"])
def admin_mark_notification_read(notification_id):
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401

    notif = Notification.query.filter_by(
        id=notification_id,
        recipient_role="admin"
    ).first()

    if not notif:
        return jsonify({"error": "Notification not found"}), 404

    notif.is_read = True
    notif.read_at = datetime.utcnow()
    db.session.commit()

    unread_count = Notification.query.filter_by(
        recipient_role="admin",
        is_read=False
    ).count()

    return jsonify({"success": True, "id": notif.id, "unread_count": unread_count})


@app.route("/admin/notifications/mark_all_read", methods=["POST"])
def admin_mark_all_notifications_read():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401

    unread_notifs = Notification.query.filter_by(
        recipient_role="admin",
        is_read=False
    ).all()

    now = datetime.utcnow()
    for n in unread_notifs:
        n.is_read = True
        n.read_at = now

    db.session.commit()
    return jsonify({"success": True, "marked_count": len(unread_notifs), "unread_count": 0})


@app.route("/admin/pending_queue", methods=["GET"])
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


# ================= INIT / DB MIGRATION =================
with app.app_context():
    db.create_all()

    try:
        from sqlalchemy import inspect as sa_inspect, text as sa_text
        inspector = sa_inspect(db.engine)
        if "complaint" in inspector.get_table_names():
            columns = [col["name"] for col in inspector.get_columns("complaint")]
            column_defs = {
                "complaint_title": "VARCHAR(150)",
                "admin_remarks": "TEXT",
                "incident_type": "VARCHAR(100)",
                "severity_level": "VARCHAR(50) DEFAULT 'Medium'",
                "incident_date_time": "VARCHAR(100)",
                "incident_location": "VARCHAR(150)",
                "accused_details": "TEXT",
                "witness_details": "TEXT",
                "is_confidential": "BOOLEAN DEFAULT 0",
                "tracking_token": "VARCHAR(64)",
                "committee_assigned": "VARCHAR(100)"
            }
            with db.engine.connect() as connection:
                for col_name, col_type in column_defs.items():
                    if col_name not in columns:
                        connection.execute(sa_text(f"ALTER TABLE complaint ADD COLUMN {col_name} {col_type}"))
                connection.commit()
        logger.info("Database schema initialized/verified.")
    except Exception as e:
        logger.error("Database schema migration error: %s", str(e))

    # Start pending-action reminder daemon
    start_reminder_scheduler()


if __name__ == "__main__":
    app.run(debug=True)
