# Anti-Ragging Portal — Vel Tech High Tech Dr. Rangarajan Dr. Sakunthala Engineering College

NBA – Tier 1 SAR Guideline: Criterion No. 10.1.4

A secure, student-focused web application designed to support confidential ragging incident reporting, complaint tracking, evidence uploads, and administrative case management for the Anti-Ragging Committee (ARC).

---

## Technology Stack

- **Backend:** Python 3, Flask, Flask-SQLAlchemy, Flask-Session
- **Architecture:** Modular Layered Architecture (Blueprints, Models, Services, Middleware, Utilities)
- **Frontend:** Jinja2 Templates, Vanilla CSS Design System, Responsive Vanilla JavaScript
- **Database:** SQLite (local development) / PostgreSQL ready
- **Security:** CSRF Protection, CSPRNG OTP Auth, Magic-byte File Validation, Brute-force Lockout
- **Testing:** Python `unittest` test suite (65 comprehensive tests)

---

## Reorganized Project Structure

```
ANTI_RAGGING_PORTAL/
│
├── frontend/                     # Frontend templates and reusable components
│   ├── pages/                    # 12 active application page templates
│   │   ├── index.html            # Landing portal
│   │   ├── student_login.html    # Student authentication
│   │   ├── verify_otp.html       # 6-digit OTP verification
│   │   ├── student_dashboard.html# Student complaint management
│   │   ├── complaint_form.html   # Incident reporting with evidence uploads
│   │   ├── success.html          # Confirmation & tracking token receipt
│   │   ├── track.html            # Complaint status tracking
│   │   ├── admin_login.html      # Administrative login
│   │   ├── admin.html            # Committee dashboard
│   │   ├── admin_complaint_detail.html # Case review & status updates
│   │   ├── admin_logs.html       # SMS/Call audit records
│   │   └── error.html            # Security & error display
│   └── components/               # Modular reusable UI components
│       └── footer.html           # Institutional Criterion 10.1.4 footer
│
├── backend/                      # Decoupled server-side architecture
│   ├── __init__.py               # Flask application factory (create_app)
│   ├── config/                   # Configuration & environment settings
│   │   └── settings.py           # Core settings, constants & logger
│   ├── models/                   # SQLAlchemy database schemas
│   │   ├── base.py               # Shared db instance
│   │   ├── complaint.py          # Complaint & ComplaintEvidence models
│   │   ├── notification.py       # Two-way Notification model
│   │   └── audit.py              # Cleaner, SMSLog & CallLog models
│   ├── middleware/               # Security and rate limiting guards
│   │   ├── csrf.py               # Cryptographic CSRF token validation
│   │   └── rate_limit.py         # Brute-force & lockout protection
│   ├── utils/                    # Security, triage & messaging helpers
│   │   ├── security.py           # Client IP, OTP generation & hashing
│   │   ├── triage.py             # Incident category & severity rules
│   │   └── email_sms.py          # SMTP email delivery & Twilio integration
│   ├── services/                 # Core business services
│   │   ├── evidence_service.py   # Secure file uploads & signature check
│   │   └── notification_service.py # In-app notifications & reminder daemon
│   └── routes/                   # Route handlers & controllers
│       ├── auth_routes.py        # Student OTP & Admin authentication
│       ├── student_routes.py     # Complaint submission, tracking & evidence
│       └── admin_routes.py       # Case review, status updates & audit logs
│
├── static/                       # Public static assets
│   ├── images/                   # Official college logo and graphic assets
│   │   └── veltech_college_logo.png
│   ├── css/                      # Modular CSS stylesheets
│   │   ├── style.css             # Design tokens and base layout
│   │   ├── auth.css              # Authentication pages styling
│   │   └── dashboard.css         # Dashboard and card components
│   └── js/                       # Frontend interactivity & audio recorder
│       ├── script.js
│       └── audio_recorder.js     # WebM voice statement recorder
│
├── scripts/                      # ML training scripts and utility datasets
│   ├── data/                     # Vectorizers, models (.pkl) & datasets (.csv)
│   ├── train_model.py
│   ├── train_campus_model.py
│   ├── train_hostel_model.py
│   └── add_cleaners.py
│
├── docs/                         # Technical documentation & blueprints
│   ├── ANTI_RAGGING_PROJECT_ANALYSIS.md
│   └── project_reorganization_blueprint.md
│
├── archive/                      # Historical backup copies (excluded from git)
├── tests/                        # 65 automated tests (100% passing)
│   ├── test_security_hardening.py
│   ├── test_e2e_workflows.py
│   ├── test_evidence_and_validation.py
│   └── test_notifications.py
│
├── .env.example                  # Environment configuration template
├── .gitignore                    # Git rules excluding databases, uploads, secrets
├── grievai_app.py                # Backward-compatible root entry point
├── run.py                        # Clean application factory launcher
├── requirements.txt              # Project Python dependencies
└── README.md                     # Project documentation
```

---

## Quick Start & Running Locally

### 1. Launch the Application

You can start the application using either the standard root entry point or the modern launcher:

```bash
# Option A: Standard root entry point (backward compatible)
python grievai_app.py

# Option B: Clean application factory launcher
python run.py
```

The application starts on `http://127.0.0.1:5000/`.

### 2. Run the Automated Test Suite

Execute the entire test suite across all 4 test categories (65 tests):

```bash
python -m unittest discover -s tests -v
```

---

## Key Portal Routes

- **Landing Portal:** `http://127.0.0.1:5000/`
- **Student Authentication:** `http://127.0.0.1:5000/student_login`
- **Student Dashboard:** `http://127.0.0.1:5000/student_dashboard`
- **Incident Reporting:** `http://127.0.0.1:5000/complaint_form`
- **Complaint Tracking:** `http://127.0.0.1:5000/track`
- **Administrator Login:** `http://127.0.0.1:5000/admin_login`
- **Administrator Dashboard:** `http://127.0.0.1:5000/admin`
- **Administrator Logs:** `http://127.0.0.1:5000/admin/logs`

---

## Institutional Compliance

NBA – Tier 1 SAR Guideline: Criterion No. 10.1.4
Powered by Anti-Ragging Committee | © 2026 Vel Tech High Tech Dr. Rangarajan Dr. Sakunthala Engineering College. All rights reserved.
