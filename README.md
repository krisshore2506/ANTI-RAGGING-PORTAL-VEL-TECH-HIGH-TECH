# Anti-Ragging Portal — Vel Tech High Tech

A secure, student-focused web application designed to support ragging incident reporting, confidential complaint tracking, and administrative case management in a college environment.

## Overview

The Anti-Ragging Portal provides a centralized platform where students can report incidents and track their complaints, while authorized administrators can review reports, update case statuses, and manage follow-up actions.

The project emphasizes confidentiality, authentication, secure request handling, and a professional user experience.

## Features

### Student Portal
- Student email verification using OTP.
- Complaint submission with incident details and classification.
- Complaint reference and confidential tracking token.
- Complaint status tracking.
- Responsive student dashboard.
- Complaint confirmation and printable receipt.

### Administrator Portal
- Dedicated administrator authentication.
- Dashboard with complaint metrics and charts.
- Complaint review and status management.
- Detailed incident review interface.
- Communication and simulated SMS log views.
- Status-update notification support.

### Security and Reliability
- CSRF protection for state-changing requests.
- Secure OTP generation and expiry handling.
- OTP resend cooldown and request rate limiting.
- Input validation and safe HTML escaping.
- Session and authentication protections.
- Automated security and end-to-end workflow tests.

## Technology Stack

- **Backend:** Python, Flask
- **Frontend:** HTML, CSS, JavaScript, Jinja2
- **Database:** SQLite with Flask-SQLAlchemy
- **Authentication:** Email OTP verification and session management
- **Testing:** Python unittest and Flask test client
- **Visualizations:** Chart.js

## Project Structure

`	ext
ANTI_RAGGING_PORTAL/
├── grievai_app.py
├── requirements.txt
├── tests/
│   ├── test_security_hardening.py
│   └── test_e2e_workflows.py
├── templates/
│   ├── index.html
│   ├── student_login.html
│   ├── verify_otp.html
│   ├── student_dashboard.html
│   ├── complaint_form.html
│   ├── success.html
│   ├── track.html
│   ├── admin_login.html
│   ├── admin.html
│   └── admin_logs.html
├── static/
│   ├── css/
│   ├── js/
│   └── images/
└── .gitignore
`

The repository also contains model artifacts and datasets used by the application. Refer to the actual project files for their specific roles.

## Installation

### Prerequisites

- Python 3.11 or a compatible version supported by the project dependencies.
- Git.
- A terminal such as PowerShell.

### 1. Clone the repository

`ash
git clone https://github.com/krisshore2506/ANTI-RAGGING-PORTAL-VEL-TECH-HIGH-TECH.git
cd ANTI-RAGGING-PORTAL-VEL-TECH-HIGH-TECH
`

### 2. Create a virtual environment

On Windows PowerShell:

`powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
`

### 3. Install dependencies

`ash
python -m pip install --upgrade pip
pip install -r requirements.txt
`

### 4. Configure environment variables

Configure the environment variables required by the application before running it. Depending on the enabled features, these may include:

- SECRET_KEY
- SMTP_EMAIL
- SMTP_PASSWORD
- ADMIN_EMAIL
- ADMIN_PASSWORD

Configure any additional messaging-provider credentials required by your installation.

Use strong, unique production credentials. Store secrets in environment variables or a secret manager, never directly in source code or Git. Do not use development credentials in production.

### 5. Run the application

Follow the application entry-point and configuration requirements in grievai_app.py. If the script's main entry point starts the Flask server, run:

`ash
python grievai_app.py
`

Open the local address printed by the application in your browser.

## Running Tests

Run the automated test suite from the project root:

`ash
python -m unittest discover -s tests -v
`

The Phase 2.5 validation report recorded 39 passing automated tests and browser checks for major student and administrator workflows. Results may vary after subsequent code or environment changes.

Use an isolated test database for testing. Never run test workflows against a database containing real student complaints.

## Database and Privacy

The application uses SQLite in its development configuration. Configure an appropriate persistent database and backup strategy for deployment.

Complaint narratives, student identifiers, email addresses, tracking tokens, authentication secrets, and database files may contain sensitive information. Do not publish real complaint records or confidential datasets.

Before making this repository public, verify that all committed datasets are anonymized and that no credentials or private records exist in the current files or Git history.

## Production Deployment

Before deploying:

1. Disable Flask debug mode.
2. Configure a production-grade WSGI server and HTTPS.
3. Set strong secret keys and administrator credentials.
4. Configure production email and messaging credentials securely.
5. Use a persistent database with access controls and backups.
6. Verify authorization, CSRF protection, rate limiting, and session security.
7. Restrict access to complaint records and administrative functions.
8. Test recovery, logging, and incident-response procedures.

## Responsible Use

This portal is intended to support institutional anti-ragging reporting and complaint management. It should complement, not replace, applicable institutional procedures and official reporting channels.

Protect complainant confidentiality and ensure that only authorized personnel can access sensitive case information.

## License

No license has been specified yet. All rights remain with the respective copyright holders unless a license is added.
