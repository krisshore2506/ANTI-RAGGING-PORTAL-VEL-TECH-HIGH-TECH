from datetime import datetime
from backend.models.base import db

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
