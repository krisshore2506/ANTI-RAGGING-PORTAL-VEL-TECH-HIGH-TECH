from datetime import datetime
from backend.models.base import db

class Cleaner(db.Model):
    __tablename__ = "cleaner"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    department = db.Column(db.String(50), nullable=False)   # Hostel / Campus
    area = db.Column(db.String(100), nullable=False)        # Hostel_Room / Campus_Lab / etc.
    phone = db.Column(db.String(20), nullable=False)


class CallLog(db.Model):
    __tablename__ = "call_log"

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
