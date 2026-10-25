from datetime import datetime
from backend.models.base import db

class Complaint(db.Model):
    __tablename__ = "complaint"

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

    # Anti-Ragging Specific Attributes
    incident_type = db.Column(db.String(100), nullable=True)
    severity_level = db.Column(db.String(50), nullable=True, default="Medium")
    incident_date_time = db.Column(db.String(100), nullable=True)
    incident_location = db.Column(db.String(150), nullable=True)
    accused_details = db.Column(db.Text, nullable=True)
    witness_details = db.Column(db.Text, nullable=True)
    is_confidential = db.Column(db.Boolean, default=False)
    tracking_token = db.Column(db.String(64), nullable=True, index=True)
    committee_assigned = db.Column(db.String(100), nullable=True)
    evidence_files = db.relationship('ComplaintEvidence', backref='complaint', lazy=True, cascade='all, delete-orphan')


class ComplaintEvidence(db.Model):
    __tablename__ = "complaint_evidence"

    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(db.Integer, db.ForeignKey("complaint.id"), nullable=False, index=True)
    file_type = db.Column(db.String(50), nullable=False)  # 'photo', 'audio', 'video', 'document'
    original_filename = db.Column(db.String(255), nullable=False)
    storage_filename = db.Column(db.String(255), nullable=False, unique=True)
    mime_type = db.Column(db.String(100), nullable=False)
    file_size_bytes = db.Column(db.Integer, nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        from backend.services.evidence_service import format_file_size
        return {
            "id": self.id,
            "complaint_id": self.complaint_id,
            "file_type": self.file_type,
            "original_filename": self.original_filename,
            "mime_type": self.mime_type,
            "file_size_bytes": self.file_size_bytes,
            "file_size_human": format_file_size(self.file_size_bytes),
            "uploaded_at": self.uploaded_at.strftime("%Y-%m-%d %H:%M UTC") if self.uploaded_at else ""
        }
