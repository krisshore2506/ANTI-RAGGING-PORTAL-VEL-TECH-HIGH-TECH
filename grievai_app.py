from flask import Flask, request, render_template, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_session import Session
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
import pickle
import smtplib
import random
import time
import os
from twilio.rest import Client
import os
from dotenv import load_dotenv
import sqlite3

load_dotenv()

account_sid = os.getenv("TWILIO_ACCOUNT_SID")
auth_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_number = os.getenv("TWILIO_PHONE_NUMBER")

client = None
if account_sid and auth_token:
    client = Client(account_sid, auth_token)
    print("Twilio configured successfully.")
else:
    print("Twilio not configured. SMS/call features will run in simulated mode.")
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()
def send_sms_simulated(complaint_id, to_number, message):
    conn = sqlite3.connect("complaints.db")
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


# ================= LOAD NEW ML MODELS =================

# Campus Model
with open("campus_model.pkl", "rb") as f:
    campus_model = pickle.load(f)

with open("campus_vectorizer.pkl", "rb") as f:
    campus_vectorizer = pickle.load(f)

# Hostel Model
with open("hostel_model.pkl", "rb") as f:
    hostel_model = pickle.load(f)

with open("hostel_vectorizer.pkl", "rb") as f:
    hostel_vectorizer = pickle.load(f)


# Keywords to detect Hostel Complaints
HOSTEL_KEYWORDS = [
    "hostel", "room", "bed", "warden", "mess",
    "block", "floor", "corridor", "common room"
]
with open("complaints_model.pkl", "rb") as f:
    model = pickle.load(f)

with open("vectorizer.pkl", "rb") as f:
    vectorizer = pickle.load(f)

with open("label_encoder.pkl", "rb") as f:
    label_encoder = pickle.load(f)


# ================= APP CONFIG =================
app = Flask(__name__)
app.secret_key = os.urandom(24)

app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_PERMANENT"] = False
Session(app)

# ================= DATABASE CONFIG =================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(BASE_DIR, "complaints.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# ================= DATABASE MODEL =================
# ================= DATABASE MODEL =================

class Complaint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_email = db.Column(db.String(120), nullable=False)
    complaint_title = db.Column(db.String(150), nullable=True)  # 🔥 NEW FIELD
    complaint_text = db.Column(db.Text, nullable=False)
    department = db.Column(db.String(100), nullable=False)
    assigned_cleaner = db.Column(db.String(100))
    status = db.Column(db.String(20), default="Open")
    priority = db.Column(db.String(20), default="Medium")
    escalation_level = db.Column(db.Integer, default=0)
    admin_remarks = db.Column(db.Text, nullable=True)  # 🔥 NEW FIELD
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Cleaner(db.Model):

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(100), nullable=False)

    department = db.Column(db.String(50), nullable=False)
    # Hostel / Campus

    area = db.Column(db.String(100), nullable=False)
    # Hostel_Room / Campus_Lab / etc.

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


# ================= EMAIL CONFIG =================
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

# ================= ADMIN CREDENTIALS =================
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
DEMO_OTP_MODE = True

# ================= HELPER: SEND OTP EMAIL =================
def send_otp_email(email, otp):
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        msg = MIMEText(f"Your OTP is {otp}. It expires in 1 minute.", "plain")
        msg["Subject"] = "EduSort OTP Verification"
        msg["From"] = SMTP_EMAIL
        msg["To"] = email
        server.send_message(msg)
# ================= FIND CLEANER =================

def find_cleaner(main_dept, sub_dept):

    return Cleaner.query.filter_by(
        department=main_dept,
        area=sub_dept
    ).first()

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

    # Demo mode for Render / online deployment
    if DEMO_OTP_MODE:
        flash(f"Demo OTP generated successfully.", "success")
        return redirect(url_for("verify_otp_page"))

    # Normal email OTP mode
    try:
        send_otp_email(email, otp)
        return redirect(url_for("verify_otp_page"))
    except Exception as e:
        return f"OTP Error: {e}"

#========🧠 FIND CLEANER FUNCTION=========
def find_cleaner(main_dept, sub_dept):

    return Cleaner.query.filter_by(
        department=main_dept,
        area=sub_dept
    ).first()

def send_sms(to_number, message):
    try:
        message = client.messages.create(
            body=message,
            from_=twilio_number,
            to=to_number
        )
        return True
    except Exception as e:
        print("SMS Error:", e)
        return False


def trigger_auto_call(complaint_id, cleaner_name, phone_number):
    print(f"📞 Auto Call Triggered → Complaint #{complaint_id} → {phone_number}")

    call_log = CallLog(
        complaint_id=complaint_id,
        cleaner_name=cleaner_name,
        phone_number=phone_number,
        call_status="Triggered"
    )

    db.session.add(call_log)
    db.session.commit()

    return True



# ================= OTP PAGE (GET) =================
@app.route("/verify_otp", methods=["GET"])
def verify_otp_page():
    demo_otp = session.get("otp") if DEMO_OTP_MODE else None
    return render_template(
        "verify_otp.html",
        email=session.get("email"),
        demo_otp=demo_otp
    )


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
demo_otp = session.get("otp") if DEMO_OTP_MODE else None
return render_template("verify_otp.html", email=email, demo_otp=demo_otp)




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

    send_otp_email(email, new_otp)

    flash("A new OTP has been sent to your email.", "success")
    return redirect(url_for("verify_otp_page"))


@app.route("/student_dashboard")
def student_dashboard():
    if not session.get("verified"):
        return redirect(url_for("home"))
    
    email = session.get("email")
    complaints = Complaint.query.filter_by(student_email=email).order_by(Complaint.timestamp.desc()).all()
    
    # Calculate stats
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
    session.clear()   # clears verified, email, otp, everything
    return redirect(url_for("home"))

def send_status_update_email(student_email, complaint_id, new_status):
    subject = f"EduSort Complaint Status Update (ID #{complaint_id})"

    status = new_status.strip().lower()

    if "progress" in status:
        title = "Complaint In Progress"
        message = (
            "Your complaint is currently being reviewed and processed by our team."
        )
        status_color = "#f59e0b"   # amber

    elif status in ["resolved", "closed"]:
        title = "Complaint Resolved"
        message = (
            "We are happy to inform you that your complaint has been successfully resolved."
        )
        status_color = "#16a34a"   # green

    else:
        return  # no email for other statuses

    html_body = f"""
    <html>
    <body style="margin:0; padding:0; background-color:#f4f6f9; font-family:Arial, sans-serif;">
        <div style="max-width:600px; margin:40px auto; background:#ffffff;
                    border-radius:12px; box-shadow:0 4px 15px rgba(0,0,0,0.1);
                    overflow:hidden;">
            <div style="background:#1e88c9; padding:20px; text-align:center;">
                <h2 style="color:#ffffff; margin:0;">EduSort</h2>
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
                    Regards,<br><strong>EduSort Team</strong>
                </p>
            </div>

            <div style="background:#f1f5f9; padding:14px; text-align:center;
                        font-size:12px; color:#555;">
                © 2026 EduSort | Vel Tech High Tech Engineering College
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

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.send_message(msg)

    except Exception as e:
        print("Status email error:", e)




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

    complaint_text = request.form.get("complaint", "").strip().lower()
    complaint_title = request.form.get("title", "").strip()
    if not complaint_title:
        # Fallback if title is empty
        complaint_title = "Complaint: " + (complaint_text[:30] + "..." if len(complaint_text) > 30 else complaint_text)
        
    priority = request.form.get("priority", "Medium").strip()
    email = session.get("email")

    # -------- CLEANING KEYWORDS --------
    CLEANING_KEYWORDS = [
        "dirty", "dust", "garbage", "smell", "toilet",
        "washroom", "bathroom", "clean", "waste",
        "sewage", "mop", "sweep", "ground", "lab", "room"
    ]

    HOSTEL_KEYWORDS = [
        "hostel", "room", "bed", "warden",
        "mess", "hostel block", "hostel corridor",
        "common room"
    ]

    # -------- CHECK TYPE --------
    is_cleaning = any(word in complaint_text for word in CLEANING_KEYWORDS)
    is_hostel = any(word in complaint_text for word in HOSTEL_KEYWORDS)

    assigned = None
    cleaner_phone = None
    department = None

    # =====================================
    # CASE 1: CLEANING COMPLAINT
    # =====================================
    if is_cleaning:

        # Hostel
        if is_hostel:

            X = hostel_vectorizer.transform([complaint_text])
            sub_category = hostel_model.predict(X)[0]

            cleaner = find_cleaner("Hostel", sub_category)

            department = f"Hostel - {sub_category}"

        # Campus
        else:

            X = campus_vectorizer.transform([complaint_text])
            sub_category = campus_model.predict(X)[0]

            cleaner = find_cleaner("Campus", sub_category)

            department = f"Campus - {sub_category}"


        if cleaner:
            assigned = cleaner.name
            cleaner_phone = cleaner.phone
        else:
            assigned = "Not Assigned"
            cleaner_phone = "N/A"


       # =====================================
    # CASE 2: NORMAL COMPLAINT (OLD SYSTEM)
    # =====================================
    else:

        X = vectorizer.transform([complaint_text])

        old_category = label_encoder.inverse_transform(
            model.predict(X)
        )[0]

        department = old_category

    # Set initial status (if assigned cleaner, set status to Assigned)
    initial_status = "Assigned" if (is_cleaning and assigned and assigned != "Not Assigned") else "Open"

    # -------- SAVE TO DATABASE --------
    complaint = Complaint(
        student_email=email,
        complaint_title=complaint_title,
        complaint_text=complaint_text,
        department=department,
        assigned_cleaner=assigned,
        status=initial_status,
        priority=priority
    )

    db.session.add(complaint)
    db.session.commit()

    # -------- SEND SMS TO CLEANER --------
    # -------- SEND SMS TO CLEANER --------
    if is_cleaning and cleaner_phone and cleaner_phone != "N/A":

        sms_message = f"""
EduSort Cleaning Alert 🚨
Hello {assigned},

You have been assigned a cleaning complaint.
Department: {department}
Complaint ID: {complaint.id}

Please respond immediately.
"""

        send_sms_simulated(
            complaint.id,
            cleaner_phone,
            sms_message
        )

        trigger_auto_call(
            complaint.id,
            assigned,
            cleaner_phone
        )

    # We do NOT clear student session here so student remains logged in!

    return render_template(
        "success.html",
        category=department,
        complaint_id=complaint.id,
        cleaner_name=assigned,
        cleaner_phone=cleaner_phone,
        is_cleaning=is_cleaning
    )


# ================= ADMIN LOGIN =================
@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if (
            request.form["email"] == ADMIN_EMAIL
            and request.form["password"] == ADMIN_PASSWORD
        ):
            session["admin_logged_in"] = True
            return redirect(url_for("admin"))

        flash("Invalid admin credentials", "error")

    return render_template("admin_login.html")

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        complaint_id = request.form.get("complaint_id")
        new_status = request.form.get("status")
        remarks = request.form.get("admin_remarks", "").strip()

        complaint = Complaint.query.get(complaint_id)

        if complaint:
            old_status = complaint.status
            complaint.admin_remarks = remarks
            
            status_changed = (old_status != new_status)
            if status_changed:
                complaint.status = new_status
            
            db.session.commit()

            if status_changed:
                # 🔔 SEND STATUS UPDATE EMAIL
                send_status_update_email(
                    complaint.student_email,
                    complaint.id,
                    new_status
                )

    complaints = Complaint.query.order_by(Complaint.timestamp.desc()).all()
    return render_template("admin.html", complaints=complaints)
@app.route("/admin/logs")
@app.route("/admin/logs")
def admin_logs():

    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    # Get selected status from dropdown
    status_filter = request.args.get("status")

    # Get only Cleaning complaints
    cleaning_query = Complaint.query.filter(
        Complaint.department.like("%Hostel%") |
        Complaint.department.like("%Campus%")
    )

    # If a status is selected, filter it
    if status_filter:
        cleaning_query = cleaning_query.filter(
            Complaint.status == status_filter
        )

    cleaning_complaints = cleaning_query.all()
    # 🔥 ESCALATION LOGIC (Based on Failed Calls)

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
# ================= TRACK COMPLAINT =================
@app.route("/track", methods=["GET", "POST"])
def track():
    complaint = None
    not_found = False

    complaint_id = None
    if request.method == "POST":
        complaint_id = request.form.get("complaint_id")
    else:
        complaint_id = request.args.get("id")

    if complaint_id:
        complaint = Complaint.query.get(complaint_id)
        if complaint is None:
            not_found = True

    return render_template(
        "track.html",
        complaint=complaint,
        not_found=not_found
    )



# ================= INIT =================
with app.app_context():
    db.create_all()
    # Safely alter table to add complaint_title and admin_remarks if they don't exist
    try:
        db_path = os.path.join(BASE_DIR, "complaints.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(complaint)")
        columns = [row[1] for row in cursor.fetchall()]
        if "complaint_title" not in columns:
            cursor.execute("ALTER TABLE complaint ADD COLUMN complaint_title VARCHAR(150)")
        if "admin_remarks" not in columns:
            cursor.execute("ALTER TABLE complaint ADD COLUMN admin_remarks TEXT")
        conn.commit()
        conn.close()
        print("Database schema migration completed/verified.")
    except Exception as e:
        print("Database schema migration error:", e)

if __name__ == "__main__":
    app.run(debug=True)
