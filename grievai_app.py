from flask import Flask, request, render_template, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_session import Session
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
import pickle
import smtplib
import random
import secrets
import hashlib
import time
import os
import sqlite3

from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

# ================= TWILIO CONFIG =================
account_sid = os.getenv("TWILIO_ACCOUNT_SID")
auth_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_number = os.getenv("TWILIO_PHONE_NUMBER")

client = None
if account_sid and auth_token:
    client = Client(account_sid, auth_token)
    print("Twilio configured successfully.")
else:
    print("Twilio not configured. SMS/call features will run in simulated mode.")


# ================= TOKEN SECURITY & RATE LIMITING =================
MAX_TRACKING_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 600  # 10 minutes

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

def check_tracking_rate_limit():
    """
    Enforces attempt throttling on tracking lookups to prevent brute-force token guessing.
    """
    now = time.time()
    lockout_until = session.get("track_lockout_until", 0)
    if now < lockout_until:
        remaining_mins = max(1, int(lockout_until - now) // 60 + 1)
        return False, f"Too many failed verification attempts. Please wait {remaining_mins} minute(s) before trying again."
    return True, None

def record_tracking_failure():
    """
    Increments failed tracking attempts and triggers temporary lockout if threshold is exceeded.
    """
    now = time.time()
    attempts = session.get("track_attempts", 0) + 1
    session["track_attempts"] = attempts
    if attempts >= MAX_TRACKING_ATTEMPTS:
        session["track_lockout_until"] = now + LOCKOUT_DURATION_SECONDS
        session["track_attempts"] = 0

def reset_tracking_attempts():
    """
    Clears failure counters upon successful verification.
    """
    session["track_attempts"] = 0
    session.pop("track_lockout_until", None)


# ================= ANTI-RAGGING CLASSIFIER & TRIAGE LOGIC =================
# Legacy education and sanitation models (*.pkl) remain preserved on disk
# but are decoupled from the Anti-Ragging Portal.

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


# ================= APP CONFIG =================
app = Flask(__name__)
app.secret_key = os.urandom(24)

app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_PERMANENT"] = False
Session(app)

# ================= DATABASE CONFIG =================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_NAME = "anti_ragging_dev.db"
DB_PATH = os.path.join(BASE_DIR, DB_NAME)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + DB_PATH
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

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

    # Anti-Ragging Specific Attributes (Prototype)
    incident_type = db.Column(db.String(100), nullable=True)
    severity_level = db.Column(db.String(50), nullable=True, default="Medium")
    incident_date_time = db.Column(db.String(100), nullable=True)
    incident_location = db.Column(db.String(150), nullable=True)
    accused_details = db.Column(db.Text, nullable=True)
    witness_details = db.Column(db.Text, nullable=True)
    is_confidential = db.Column(db.Boolean, default=False)
    tracking_token = db.Column(db.String(64), nullable=True, index=True)
    committee_assigned = db.Column(db.String(100), nullable=True)


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


# ================= EMAIL / ADMIN CONFIG =================
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")


# ================= HELPER FUNCTIONS =================
def send_sms_simulated(complaint_id, to_number, message):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO sms_logs (complaint_id, to_number, message, status, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, (
        complaint_id,
        to_number,
        message,
        "Sent (Simulated)",
        datetime.now()
    ))

    conn.commit()
    conn.close()


def send_otp_email(email, otp):
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        raise ValueError("SMTP_EMAIL or SMTP_PASSWORD is missing")

    msg = MIMEText(f"Your OTP is {otp}. It expires in 1 minute.", "plain")
    msg["Subject"] = "Anti-Ragging Portal OTP Verification"
    msg["From"] = SMTP_EMAIL
    msg["To"] = email

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.send_message(msg)

        print(f"OTP email sent successfully to {email}")

    except Exception as e:
        print("OTP Email Error:", e)
        raise


def find_cleaner(main_dept, sub_dept):
    return Cleaner.query.filter_by(
        department=main_dept,
        area=sub_dept
    ).first()


def send_sms(to_number, message):
    if not client:
        print("Twilio not configured. Skipping real SMS.")
        return False

    try:
        client.messages.create(
            body=message,
            from_=twilio_number,
            to=to_number
        )
        return True
    except Exception as e:
        print("SMS Error:", e)
        return False


def trigger_auto_call(complaint_id, cleaner_name, phone_number):
    print(f"[CALL] Auto Call Triggered → Complaint #{complaint_id} → {phone_number}")

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
    subject = f"Anti-Ragging Incident Status Update (ID #{complaint_id})"

    status = new_status.strip().lower()

    if "progress" in status:
        title = "Complaint In Progress"
        message = "Your complaint is currently being reviewed and processed by our team."
        status_color = "#f59e0b"

    elif status in ["resolved", "closed"]:
        title = "Complaint Resolved"
        message = "We are happy to inform you that your complaint has been successfully resolved."
        status_color = "#16a34a"

    else:
        return

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
                <h3>{title}</h3>
                <p>{message}</p>

                <p><strong>Complaint ID:</strong> #{complaint_id}</p>

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
                        {new_status}
                    </span>
                </p>

                <p style="margin-top:20px;">
                    Regards,<br><strong>Anti-Ragging Committee</strong>
                </p>
            </div>

            <div style="background:#f1f5f9; padding:14px; text-align:center;
                        font-size:12px; color:#555;">
                © 2026 Anti-Ragging Portal | Vel Tech High Tech Engineering College
            </div>
        </div>
    </body>
    </html>
    """

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_EMAIL
        msg["To"] = student_email
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.send_message(msg)

        print(f"Status update email sent to {student_email}")

    except Exception as e:
        print("Status email error:", e)


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
    email = request.form["email"].strip()

    if not email.endswith("@velhightech.com"):
        return render_template("error.html")

    otp = str(random.randint(100000, 999999))
    session["otp"] = otp
    session["email"] = email
    session["otp_time"] = time.time()

    try:
        send_otp_email(email, otp)
        return redirect(url_for("verify_otp_page"))
    except Exception as e:
        return f"OTP Error: {e}"


# ================= OTP PAGE (GET) =================
@app.route("/verify_otp", methods=["GET"])
def verify_otp_page():
    return render_template("verify_otp.html", email=session.get("email"))


# ================= VERIFY OTP (POST) =================
@app.route("/verify_otp", methods=["POST"])
def verify_otp():
    user_otp = request.form.get("otp")
    stored_otp = session.get("otp")
    email = session.get("email")

    if user_otp == stored_otp:
        session["verified"] = True
        return redirect(url_for("student_dashboard"))

    flash("Invalid OTP. Please try again.", "error")
    return render_template("verify_otp.html", email=email)


# ================= RESEND OTP =================
@app.route("/resend_otp")
def resend_otp():
    email = session.get("email")

    if not email:
        flash("Session expired. Please login again.", "error")
        return redirect(url_for("student_login"))

    new_otp = str(random.randint(100000, 999999))
    session["otp"] = new_otp
    session["otp_time"] = time.time()

    try:
        send_otp_email(email, new_otp)
        flash("A new OTP has been sent to your email.", "success")
    except Exception as e:
        flash(f"Failed to resend OTP: {e}", "error")

    return redirect(url_for("verify_otp_page"))


@app.route("/student_dashboard")
def student_dashboard():
    if not session.get("verified"):
        return redirect(url_for("home"))

    email = session.get("email")
    complaints = Complaint.query.filter_by(student_email=email).order_by(Complaint.timestamp.desc()).all()

    total = len(complaints)
    open_count = sum(1 for c in complaints if c.status.strip().lower() == "open")
    in_progress = sum(1 for c in complaints if "progress" in c.status.strip().lower() or "assigned" in c.status.strip().lower())
    resolved = sum(1 for c in complaints if c.status.strip().lower() in ["resolved", "closed"])

    return render_template(
        "student_dashboard.html",
        email=email,
        complaints=complaints,
        total=total,
        open_count=open_count,
        in_progress=in_progress,
        resolved=resolved
    )


# ================= STUDENT LOGOUT =================
@app.route("/student_logout")
def student_logout():
    session.clear()
    return redirect(url_for("home"))


# ================= COMPLAINT FORM =================
@app.route("/complaint_form")
def complaint_form():
    if not session.get("verified"):
        return redirect(url_for("home"))
    return render_template("complaint_form.html", email=session.get("email"))


# ================= PREDICT =================
@app.route("/predict", methods=["POST"])
def predict():
    if not session.get("verified"):
        return redirect(url_for("home"))

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
    db.session.commit()

    # NOTE: Sanitation phone call and SMS dispatches have been completely removed.
    # No real calls or SMS messages are sent. Tracking tokens are not logged in plain app logs.

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


# ================= ADMIN LOGIN =================
@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form["email"] == ADMIN_EMAIL and request.form["password"] == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect(url_for("admin"))

        flash("Invalid admin credentials", "error")

    return render_template("admin_login.html")


# ================= ANTI-RAGGING COMMITTEE (ARC) DASHBOARD =================
@app.route("/admin", methods=["GET", "POST"])
def admin():
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
            db.session.commit()
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
    return render_template("admin.html", complaints=complaints, stats=stats, category_counts=category_counts)


# ================= AUTHORIZED COMMITTEE COMPLAINT DETAILS =================
@app.route("/admin/complaint/<int:complaint_id>", methods=["GET", "POST"])
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
        if target_status in valid_statuses:
            complaint.status = target_status
        complaint.admin_remarks = remarks
        db.session.commit()
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
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))


# ================= SECURE TRACK COMPLAINT =================
@app.route("/track", methods=["GET", "POST"])
def track():
    complaint = None
    error_message = None
    lookup_attempted = False

    # 1. Rate Limiting / Attempt Throttling check
    allowed, lockout_msg = check_tracking_rate_limit()
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
        record_tracking_failure()
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
        record_tracking_failure()
        return render_template(
            "track.html",
            complaint=None,
            error_message="Unable to verify incident ticket. Please verify your Reference ID and Tracking Token.",
            lookup_attempted=True
        )

    # Success: reset failure throttle counter
    reset_tracking_attempts()

    return render_template(
        "track.html",
        complaint=complaint,
        error_message=None,
        lookup_attempted=True
    )


# ================= INIT / DB MIGRATION =================
with app.app_context():
    db.create_all()

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("PRAGMA table_info(complaint)")
        columns = [row[1] for row in cursor.fetchall()]

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

        for col_name, col_type in column_defs.items():
            if col_name not in columns:
                cursor.execute(f"ALTER TABLE complaint ADD COLUMN {col_name} {col_type}")

        conn.commit()
        conn.close()
        print(f"Database schema initialized/verified for {DB_NAME}.")
    except Exception as e:
        print("Database schema migration error:", e)


if __name__ == "__main__":
    app.run(debug=True)