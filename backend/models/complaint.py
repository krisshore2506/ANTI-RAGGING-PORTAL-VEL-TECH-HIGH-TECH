from datetime import datetime
from backend.models.base import db


class Complaint(db.Model):
    __tablename__ = "complaint"

    id = db.Column(db.Integer, primary_key=True)
    complaint_reference = db.Column(db.String(50), nullable=True, unique=True, index=True)
    student_email = db.Column(db.String(120), nullable=False)
    complainant_name = db.Column(db.String(100), nullable=True)
    complainant_phone = db.Column(db.String(20), nullable=True)
    is_anonymous = db.Column(db.Boolean, default=False, nullable=False)
    complaint_title = db.Column(db.String(150), nullable=True)
    complaint_text = db.Column(db.Text, nullable=False)
    department = db.Column(db.String(100), nullable=False)  # Academic unit or incident category
    assigned_cleaner = db.Column(db.String(100), nullable=True)  # Legacy compatibility column
    status = db.Column(db.String(50), default="Open", nullable=False, index=True)
    priority = db.Column(db.String(20), default="Medium")
    escalation_level = db.Column(db.Integer, default=0)
    admin_remarks = db.Column(db.Text, nullable=True)
    resolution_details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at = db.Column(db.DateTime, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Anti-Ragging Specific Attributes
    incident_type = db.Column(db.String(100), nullable=True, index=True)
    severity_level = db.Column(db.String(50), nullable=True, default="Medium")
    incident_date_time = db.Column(db.String(100), nullable=True)
    incident_location = db.Column(db.String(150), nullable=True)
    accused_details = db.Column(db.Text, nullable=True)
    witness_details = db.Column(db.Text, nullable=True)
    is_confidential = db.Column(db.Boolean, default=False)
    tracking_token = db.Column(db.String(64), nullable=True, index=True)
    committee_assigned = db.Column(db.String(100), nullable=True, default="Anti-Ragging Committee")
    assigned_investigator_id = db.Column(db.Integer, db.ForeignKey("committee_member.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    evidence_files = db.relationship('ComplaintEvidence', backref='complaint', lazy=True, cascade='all, delete-orphan')
    status_history = db.relationship('ComplaintStatusHistory', backref='complaint', lazy=True, cascade='all, delete-orphan', order_by='ComplaintStatusHistory.timestamp.asc()')
    investigation_actions = db.relationship('InvestigationAction', backref='complaint', lazy=True, cascade='all, delete-orphan', order_by='InvestigationAction.created_at.asc()')


class ComplaintEvidence(db.Model):
    __tablename__ = "complaint_evidence"

    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(db.Integer, db.ForeignKey("complaint.id", ondelete="CASCADE"), nullable=False, index=True)
    file_type = db.Column(db.String(50), nullable=False)  # 'photo', 'audio', 'video', 'document'
    original_filename = db.Column(db.String(255), nullable=False)
    storage_filename = db.Column(db.String(255), nullable=False, unique=True)
    mime_type = db.Column(db.String(100), nullable=False)
    file_size_bytes = db.Column(db.Integer, nullable=False)
    uploader_role = db.Column(db.String(20), default="student", nullable=False)
    access_classification = db.Column(db.String(50), default="confidential", nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

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


class ComplaintStatusHistory(db.Model):
    """
    Immutable, append-only lifecycle audit trail.
    Tracks all transitions, committee reviews, escalations, and official actions.
    """
    __tablename__ = "complaint_status_history"

    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(db.Integer, db.ForeignKey("complaint.id", ondelete="CASCADE"), nullable=False, index=True)
    previous_status = db.Column(db.String(50), nullable=True)
    new_status = db.Column(db.String(50), nullable=False)
    action_type = db.Column(db.String(50), nullable=False, default="status_update")  # submitted, status_update, escalation, assigned, reassigned, resolved, closed, rejected
    actor_role = db.Column(db.String(50), nullable=False, default="system")  # student, admin, committee, system
    actor_email = db.Column(db.String(120), nullable=True)
    remarks = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "complaint_id": self.complaint_id,
            "previous_status": self.previous_status,
            "new_status": self.new_status,
            "action_type": self.action_type,
            "actor_role": self.actor_role,
            "actor_email": self.actor_email,
            "remarks": self.remarks,
            "timestamp": self.timestamp.strftime("%Y-%m-%d %H:%M UTC") if self.timestamp else None
        }
