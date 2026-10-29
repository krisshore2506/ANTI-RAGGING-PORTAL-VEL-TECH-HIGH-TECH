"""
Anti-Ragging Committee & Investigation Models
Vel Tech High Tech Dr. Rangarajan Dr. Sakunthala Engineering College
"""
from datetime import datetime
from backend.models.base import db


class CommitteeMember(db.Model):
    """
    Authorized Anti-Ragging Committee (ARC) and Anti-Ragging Squad (ARS) members.
    Supports role-based access, investigation assignments, and official tracking.
    """
    __tablename__ = "committee_member"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(50), nullable=False, default="squad_member")  # chairperson, squad_member, investigator, counselor, nodal_officer
    department = db.Column(db.String(100), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
            "department": self.department,
            "phone": self.phone,
            "is_active": self.is_active,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else None
        }


class InvestigationAction(db.Model):
    """
    Official investigation actions, hearings, evidence reviews, and squad field dispatches
    undertaken by authorized committee members for a specific complaint.
    """
    __tablename__ = "investigation_action"

    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(db.Integer, db.ForeignKey("complaint.id", ondelete="CASCADE"), nullable=False, index=True)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("committee_member.id", ondelete="SET NULL"), nullable=True)
    assigned_to_name = db.Column(db.String(100), nullable=True)
    action_type = db.Column(db.String(50), nullable=False)  # hearing_scheduled, statement_recorded, evidence_reviewed, squad_dispatched, interim_report, final_verdict
    action_summary = db.Column(db.Text, nullable=False)
    confidential_notes = db.Column(db.Text, nullable=True)  # Sensitive committee-only notes
    performed_by = db.Column(db.String(120), nullable=False)  # Officer or system identifier
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "complaint_id": self.complaint_id,
            "assigned_to_id": self.assigned_to_id,
            "assigned_to_name": self.assigned_to_name,
            "action_type": self.action_type,
            "action_summary": self.action_summary,
            "performed_by": self.performed_by,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M UTC") if self.created_at else None
        }

