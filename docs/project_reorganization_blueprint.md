# Anti-Ragging Portal — Project Reorganization & Modularization Blueprint

## Executive Summary
This document outlines the architectural restructuring plan for the **Vel Tech High Tech Anti-Ragging Portal** (`C:\PROJECTS\ANTI_RAGGING_PORTAL`).
The current codebase operates as a single monolithic Python application (`grievai_app.py`, 2,168 lines) alongside root-level datasets, training scripts, SQLite databases, and numerous backup files.

The goal is to cleanly decouple the project into:
1. `backend/` — Modularized Python server logic (Configuration, Models, Security Middleware, Services, Routes/Controllers).
2. `frontend/` — Template pages and reusable UI components.
3. `static/` — Public assets (Images, Logos, CSS Stylesheets, JavaScript scripts).
4. `scripts/` — Model training, dataset generation, and maintenance utilities.
5. `docs/` — Architecture documentation, SAR criteria references, and technical specifications.
6. `archive/` — Historical backup files (`*_backup*`) preserved safely out of the main source tree.

---

## Current vs. Proposed Project Structure

### Current Structure (Monolithic & Cluttered)
```text
ANTI_RAGGING_PORTAL/
├── .env
├── .gitignore
├── README.md
├── requirements.txt
├── grievai_app.py                          # 2,168 lines monolith (config, models, routes, logic)
├── add_cleaners.py                         # Standalone script
├── train_model.py                          # Training script
├── train_campus_model.py                   # Training script
├── train_hostel_model.py                   # Training script
├── college_campus_complaints.csv           # ML dataset
├── hostel_complaints.csv                   # ML dataset
├── student_complaints.csv                  # ML dataset
├── Student_complaints .csv                 # ML dataset duplicate
├── complaints_model.pkl, vectorizer.pkl... # ML artifacts
├── anti_ragging_dev.db                     # Development SQLite database
├── complaints.db                           # Secondary SQLite database
├── templates/                              # 12 active templates + 47 backup templates
│   ├── components/footer.html
│   └── *.html
├── static/                                 # CSS (15 active + 18 backups), JS, Images
│   ├── css/
│   ├── js/
│   └── images/
├── tests/                                  # 4 test suites (65 tests)
├── uploads/                                # User evidence files (private)
└── [70+ *_backup* files in root, templates, and static]
```

---

### Target Reorganized Structure (Clean & Modular)
```text
ANTI_RAGGING_PORTAL/
│
├── grievai_app.py                          # Root backwards-compatible entry point
├── run.py                                  # Alternative clean launcher
├── requirements.txt
├── README.md
├── .env.example                            # Sanitized environment template
├── .gitignore                              # Hardened git ignore
│
├── backend/                                # Core Server-Side Logic
│   ├── __init__.py                         # Application factory & package init
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py                     # Secret key, session, DB, Twilio, SMTP config
│   ├── models/
│   │   ├── __init__.py
│   │   ├── base.py                         # db = SQLAlchemy()
│   │   ├── complaint.py                    # Complaint & ComplaintEvidence models
│   │   ├── notification.py                 # Notification model
│   │   └── audit.py                        # AuditLog, SMSLog, OTPLog, AdminUser
│   ├── middleware/
│   │   ├── __init__.py
│   │   ├── csrf.py                         # Native CSRF token generation & validation
│   │   └── rate_limit.py                   # Admin brute-force IP rate limiting & lockout
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── security.py                     # Tracking token hashing, OTP crypto generator
│   │   ├── triage.py                       # NLP rule-based triage & severity classifier
│   │   └── email_sms.py                    # SMTP mailers & Twilio simulation helpers
│   ├── services/
│   │   ├── __init__.py
│   │   ├── notification_service.py         # In-app notifications & reminder daemon
│   │   └── evidence_service.py             # File validation, upload, & authorized access
│   └── routes/
│       ├── __init__.py
│       ├── auth_routes.py                  # Student login/OTP & Admin login/logout
│       ├── student_routes.py               # Home, Complaint filing, Dashboard, Tracking
│       └── admin_routes.py                 # Admin dashboard, Complaint triage, Audit logs
│
├── frontend/                               # Presentation Layer
│   ├── pages/                              # Jinja2 Page Templates
│   │   ├── index.html
│   │   ├── student_login.html
│   │   ├── verify_otp.html
│   │   ├── student_dashboard.html
│   │   ├── complaint_form.html
│   │   ├── success.html
│   │   ├── track.html
│   │   ├── admin_login.html
│   │   ├── admin.html
│   │   ├── admin_complaint_detail.html
│   │   ├── admin_logs.html
│   │   └── error.html
│   └── components/                         # Reusable Jinja2 UI Components
│       └── footer.html                     # Standardized NBA SAR Criterion 10.1.4 footer
│
├── static/                                 # Web Assets (Served by Flask /static)
│   ├── images/
│   │   ├── veltech_college_logo.png        # Official high-res transparent logo
│   │   └── logo.png
│   ├── css/
│   │   ├── style.css                       # Core design system & theme tokens
│   │   ├── auth.css                        # Student/Admin login, OTP, logo badge
│   │   ├── complaint_form.css              # Complaint submission & evidence dropzone
│   │   ├── dashboard.css                   # Student & Admin dashboards
│   │   ├── admin.css                       # Admin control room
│   │   ├── landing.css                     # Homepage styles
│   │   ├── track.css                       # Incident tracking timeline
│   │   └── error.css                       # Error page styles
│   └── js/
│       ├── admin.js                        # Admin panel interactions & filter logic
│       └── dashboard.js                    # Student dashboard actions
│
├── scripts/                                # Standalone Scripts & ML Tools
│   ├── train_model.py                      # Main complaints category classifier
│   ├── train_campus_model.py               # Campus incident classifier
│   ├── train_hostel_model.py               # Hostel incident classifier
│   ├── add_cleaners.py                     # Secondary maintenance tool
│   └── data/                               # Reference datasets
│       ├── student_complaints.csv
│       ├── college_campus_complaints.csv
│       ├── hostel_complaints.csv
│       └── *.pkl                           # Trained vectorizer & model artifacts
│
├── docs/                                   # Institutional & Technical Documentation
│   └── ANTI_RAGGING_PROJECT_ANALYSIS.md    # Architecture & security specification
│
├── archive/                                # Preserved Backup Files (Never deleted)
│   ├── templates_backup/                   # 47 legacy backup templates
│   ├── css_backup/                         # 18 legacy backup stylesheets
│   └── python_backup/                      # Legacy application iterations
│
├── tests/                                  # Automated Test Suite (65 tests)
│   ├── test_e2e_workflows.py
│   ├── test_evidence_and_validation.py
│   ├── test_notifications.py
│   └── test_security_hardening.py
│
└── uploads/                                # Private evidence storage (gitignored)
    └── evidence/
```

---

## Detailed Migration Plan

### Step 1: Backend Modularization (`backend/`)
1. Create `backend/` and its subdirectories: `config/`, `models/`, `middleware/`, `utils/`, `services/`, `routes/`.
2. Extract settings and security constants into `backend/config/settings.py`.
3. Extract SQLAlchemy database models into `backend/models/`.
4. Extract CSRF token management and brute-force lockout into `backend/middleware/`.
5. Extract cryptographic helpers, tracking hashes, and ML triage classification into `backend/utils/`.
6. Extract email/SMS notification dispatch and the background reminder daemon into `backend/services/`.
7. Divide the route handlers into 3 Flask Blueprints in `backend/routes/`:
   - `auth_bp` (`auth_routes.py`)
   - `student_bp` (`student_routes.py`)
   - `admin_bp` (`admin_routes.py`)
8. Create an application factory in `backend/__init__.py` that configures Jinja2 template loaders (pointing to `frontend/pages` and `frontend/components`) and registers all blueprints.

### Step 2: Backwards-Compatibility Layer (`grievai_app.py`)
To ensure that existing startup commands (`python grievai_app.py`) and all 65 automated tests (`import grievai_app; from grievai_app import app, db, Complaint...`) continue to function with zero friction:
- `grievai_app.py` at the root imports the initialized `app`, `db`, and models from `backend`.
- If executed directly (`if __name__ == '__main__':`), it starts the development server via `app.run(debug=True)`.

### Step 3: Frontend Template Restructuring (`frontend/`)
1. Create `frontend/pages/` and `frontend/components/`.
2. Move the 12 active templates into `frontend/pages/`.
3. Move `components/footer.html` into `frontend/components/`.
4. Configure Flask's Jinja2 template loader to include both `frontend/pages/` and `frontend/` so that existing calls like `render_template('index.html')` and `{% include 'components/footer.html' %}` resolve seamlessly.
5. Create a `templates/` junction/alias or symlink if needed for legacy tests, or update `tests/test_evidence_and_validation.py` to check the organized `frontend/pages/` path.

### Step 4: Asset Organization & Backup Archiving
1. Move ML training scripts (`train_*.py`, `add_cleaners.py`) and CSV datasets into `scripts/`.
2. Move technical documentation into `docs/`.
3. Move all 70+ `*_backup*` files into an `archive/` directory to declutter the active source tree while preserving all historical revisions intact without deleting anything.
4. Clean active CSS and JS files in `static/css/` and `static/js/`.

---

## Safety & Non-Breaking Guarantees
- **No Data Deletion**: Databases (`anti_ragging_dev.db`), `.env`, and private uploaded evidence in `uploads/` are completely untouched.
- **No Route Breakage**: All URL routes (`/`, `/predict`, `/admin`, `/track`, `/student_login`, etc.) remain 100% identical.
- **Test Integrity**: The 65 automated test cases will all execute and pass.
- **Git Safety**: Working tree changes will be staged cleanly without accidental leaks of sensitive files.
