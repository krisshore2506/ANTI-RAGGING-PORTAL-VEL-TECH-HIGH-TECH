import os
import html
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from twilio.rest import Client

from backend.config.settings import (
    SMTP_EMAIL,
    SMTP_PASSWORD,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_PHONE_NUMBER,
    logger
)
from backend.models.base import db
from backend.models.audit import SMSLog, CallLog, Cleaner

# Twilio Client Initialization
twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    try:
        twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        logger.info("Twilio configured successfully.")
    except Exception as twilio_err:
        logger.warning("Twilio initialization error: %s. Continuing in simulated mode.", twilio_err)
        twilio_client = None
else:
    logger.info("Twilio not configured. SMS/call features will run in simulated mode.")


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
                    <tr>
                        <td style="padding: 34px 28px 28px 28px;">
                            <h2 style="margin: 0 0 12px 0; color: #0f172a; font-size: 20px; font-weight: 700; line-height: 1.3;">
                                Verify Your Email Address
                            </h2>
                            <p style="margin: 0 0 22px 0; color: #334155; font-size: 14.5px; line-height: 1.6;">
                                Use the verification code below to verify your official college email address and securely access the Anti-Ragging Portal.
                            </p>
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


def send_sms(to_number, message):
    if not twilio_client:
        logger.info("Twilio not configured. Skipping real SMS.")
        return False
    try:
        twilio_client.messages.create(
            body=message,
            from_=TWILIO_PHONE_NUMBER,
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
                <p><strong>Status:</strong> <span style="background:{status_color}; color:white; padding:4px 8px; border-radius:4px;">{safe_status}</span></p>
                <p style="margin-top:20px;">Regards,<br><strong>Anti-Ragging Committee</strong></p>
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
    except Exception as e:
        logger.error("Failed to send status update email: %s", str(e))
