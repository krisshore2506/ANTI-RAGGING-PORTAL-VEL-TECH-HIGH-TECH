import os
import re
import logging
from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode
from dotenv import load_dotenv

load_dotenv()

# Logger setup
logger = logging.getLogger("anti_ragging_portal")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

# Base directories
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PORTAL_ROOT = BASE_DIR

# Security and Session Config
SECRET_KEY = os.getenv("SECRET_KEY")
FLASK_ENV = os.getenv("FLASK_ENV", "development").lower()
IS_PRODUCTION = FLASK_ENV == "production" or os.getenv("ENV", "").lower() == "production"
IS_VERCEL = os.getenv("VERCEL") == "1" or bool(os.getenv("VERCEL_ENV"))

if not SECRET_KEY:
    raise RuntimeError(
        "CRITICAL SECURITY CONFIGURATION ERROR: SECRET_KEY environment variable is required but missing. "
        "Define a persistent, secure SECRET_KEY in your environment or .env file before running."
    )

SESSION_TYPE = os.getenv("SESSION_TYPE", "filesystem").lower()
SESSION_PERMANENT = False
SESSION_USE_SIGNER = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "False").lower() in ("true", "1")


def normalize_database_url(raw_url: str) -> str:
    """
    Normalizes a database URL to ensure seamless compatibility with SQLAlchemy and PostgreSQL (Psycopg 3).
    - Preserves SQLite, MySQL, and other valid dialect URLs.
    - Converts legacy 'postgres://' or driverless 'postgresql://' to 'postgresql+psycopg://'.
    - Preserves explicit drivers (e.g., 'postgresql+psycopg2://', 'postgresql+asyncpg://').
    - Enforces TLS encryption (sslmode=require) for remote PostgreSQL hosts (e.g., Supabase) when unspecified.
    """
    if not raw_url or not isinstance(raw_url, str):
        return ""

    url = raw_url.strip()
    if not url or url.lower() in ("your_db_url", "none", '""', "''"):
        return ""

    # Normalize scheme to Psycopg 3 dialect for SQLAlchemy 2.0+
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]

    # Check TLS for PostgreSQL connections
    if url.startswith(("postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://")):
        try:
            parts = urlsplit(url)
            hostname = (parts.hostname or "").lower()
            query_params = parse_qs(parts.query, keep_blank_values=True)

            # Remote PostgreSQL hosts (including Supabase) require TLS encryption
            is_local = hostname in ("localhost", "127.0.0.1", "0.0.0.0", "") or hostname.startswith("127.")
            if not is_local and "sslmode" not in query_params:
                query_params["sslmode"] = ["require"]
                new_query = urlencode(query_params, doseq=True)
                url = urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))
        except Exception:
            pass

    return url


def mask_database_url(url: str) -> str:
    """
    Masks credentials in a database URL so it can be safely logged without leaking passwords.
    """
    if not url or not isinstance(url, str):
        return ""
    try:
        from sqlalchemy.engine import make_url
        norm = url
        if norm.startswith("postgres://"):
            norm = "postgresql://" + norm[len("postgres://"):]
        return make_url(norm).render_as_string(hide_password=True)
    except Exception:
        masked = re.sub(r':([^/@:\s]+)@', r':***@', url)
        masked = re.sub(r'(password=)[^&;\s]+', r'\1***', masked, flags=re.IGNORECASE)
        return masked


def sanitize_db_error(e: Exception) -> str:
    """
    Sanitizes database exception strings to prevent credentials or sensitive DSNs from leaking in logs or errors.
    """
    if not e:
        return ""
    msg = str(e)
    msg = re.sub(r':([^/@:\s]+)@', r':***@', msg)
    msg = re.sub(r'(password=)[^&;\s]+', r'\1***', msg, flags=re.IGNORECASE)
    msg = re.sub(r'(Bearer\s+)[A-Za-z0-9_\-\.]+', r'\1***', msg, flags=re.IGNORECASE)
    return msg


# Database Configuration
DB_NAME = "anti_ragging_dev.db"
DB_PATH = os.path.join(BASE_DIR, DB_NAME)

env_db_url = os.getenv("DATABASE_URL", "").strip()
normalized_db_url = normalize_database_url(env_db_url)

if normalized_db_url:
    SQLALCHEMY_DATABASE_URI = normalized_db_url
elif IS_PRODUCTION:
    raise RuntimeError(
        "CRITICAL DATABASE CONFIGURATION ERROR: In production mode (FLASK_ENV=production), "
        "a valid PostgreSQL DATABASE_URL is strictly required. Silent fallback to SQLite is disabled."
    )
else:
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + DB_PATH

SQLALCHEMY_TRACK_MODIFICATIONS = False

# Connection Engine Pool Options
if SQLALCHEMY_DATABASE_URI.startswith("sqlite"):
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
    }
else:
    _default_pool_size = "3" if IS_VERCEL else "10"
    _default_max_overflow = "2" if IS_VERCEL else "5"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": int(os.getenv("DB_POOL_RECYCLE_SECONDS", "300")),
        "pool_size": int(os.getenv("DB_POOL_SIZE", _default_pool_size)),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", _default_max_overflow)),
    }

# Evidence Upload Configuration
MAX_CONTENT_LENGTH = int(os.getenv("MAX_UPLOAD_SIZE_BYTES", 25 * 1024 * 1024))  # 25 MB
if IS_VERCEL:
    EVIDENCE_UPLOAD_FOLDER = os.path.join("/tmp", "uploads", "evidence")
else:
    EVIDENCE_UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "evidence")

try:
    os.makedirs(EVIDENCE_UPLOAD_FOLDER, exist_ok=True)
except OSError:
    pass

# Evidence Storage Backend Configuration ('local' or 'supabase')
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").lower()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "complaint-evidence").strip()

# Strict Student Email Pattern: ^vh[0-9]+@velhightech\.com$
STUDENT_EMAIL_REGEX = re.compile(r"^vh[0-9]+@velhightech\.com$")

# Email & SMTP Configuration
_raw_smtp_email = (os.getenv("SMTP_EMAIL") or os.getenv("EMAIL_USER") or "").strip()
_raw_smtp_password = (os.getenv("SMTP_PASSWORD") or os.getenv("EMAIL_PASS") or "").strip()
SMTP_EMAIL = _raw_smtp_email if _raw_smtp_email else None
SMTP_PASSWORD = _raw_smtp_password if _raw_smtp_password else None
EMAIL_USER = SMTP_EMAIL
EMAIL_PASS = SMTP_PASSWORD

SMTP_HOST = (os.getenv("SMTP_HOST") or os.getenv("MAIL_SERVER") or "smtp.gmail.com").strip()

_raw_port = (os.getenv("SMTP_PORT") or os.getenv("MAIL_PORT") or "").strip()
_raw_ssl = os.getenv("SMTP_USE_SSL")
if _raw_ssl is not None:
    SMTP_USE_SSL = _raw_ssl.strip().lower() in ("true", "1", "yes")
else:
    SMTP_USE_SSL = (_raw_port == "465")

if _raw_port.isdigit():
    SMTP_PORT = int(_raw_port)
else:
    SMTP_PORT = 465 if SMTP_USE_SSL else 587

_raw_tls = os.getenv("SMTP_USE_TLS")
if _raw_tls is not None:
    SMTP_USE_TLS = _raw_tls.strip().lower() in ("true", "1", "yes")
else:
    SMTP_USE_TLS = not SMTP_USE_SSL

_raw_timeout = (os.getenv("SMTP_TIMEOUT") or "15").strip()
SMTP_TIMEOUT = int(_raw_timeout) if _raw_timeout.isdigit() else 15

DEV_SIMULATE_EMAIL = os.getenv("DEV_SIMULATE_EMAIL", "False").lower() in ("true", "1", "yes")

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

# Twilio Configuration
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

# Reminder Thresholds
REMINDER_OPEN_HOURS = int(os.getenv("REMINDER_OPEN_HOURS", "24"))
REMINDER_IN_PROGRESS_HOURS = int(os.getenv("REMINDER_IN_PROGRESS_HOURS", "72"))
REMINDER_CHECK_INTERVAL_SECONDS = int(os.getenv("REMINDER_CHECK_INTERVAL_SECONDS", "3600"))
DISABLE_REMINDER_SCHEDULER = os.getenv("DISABLE_REMINDER_SCHEDULER", "False").lower() in ("true", "1", "yes")
CRON_SECRET = os.getenv("CRON_SECRET", "").strip()

# Rate Limiting Constants
MAX_TRACKING_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 600  # 10 minutes
MAX_ADMIN_ATTEMPTS = 5
ADMIN_LOCKOUT_SECONDS = 900  # 15 minutes
