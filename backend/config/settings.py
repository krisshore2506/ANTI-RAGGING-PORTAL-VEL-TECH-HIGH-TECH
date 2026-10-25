import os
import re
import logging
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

if not SECRET_KEY:
    raise RuntimeError(
        "CRITICAL SECURITY CONFIGURATION ERROR: SECRET_KEY environment variable is required but missing. "
        "Define a persistent, secure SECRET_KEY in your environment or .env file before running."
    )

SESSION_TYPE = "filesystem"
SESSION_PERMANENT = False
SESSION_USE_SIGNER = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "False").lower() in ("true", "1")

# Database Configuration
DB_NAME = "anti_ragging_dev.db"
DB_PATH = os.path.join(BASE_DIR, DB_NAME)

env_db_url = os.getenv("DATABASE_URL", "").strip()
if env_db_url and env_db_url.lower() not in ("your_db_url", "none", ""):
    if env_db_url.startswith("postgres://"):
        env_db_url = env_db_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = env_db_url
else:
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + DB_PATH

SQLALCHEMY_TRACK_MODIFICATIONS = False

# Evidence Upload Configuration
MAX_CONTENT_LENGTH = int(os.getenv("MAX_UPLOAD_SIZE_BYTES", 25 * 1024 * 1024))  # 25 MB
EVIDENCE_UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "evidence")
os.makedirs(EVIDENCE_UPLOAD_FOLDER, exist_ok=True)

# Strict Student Email Pattern: ^vh[0-9]+@velhightech\.com$
STUDENT_EMAIL_REGEX = re.compile(r"^vh[0-9]+@velhightech\.com$")

# Email & Admin Credentials
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
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

# Rate Limiting Constants
MAX_TRACKING_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 600  # 10 minutes
MAX_ADMIN_ATTEMPTS = 5
ADMIN_LOCKOUT_SECONDS = 900  # 15 minutes
