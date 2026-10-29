"""reconcile_anti_ragging_tables

Revision ID: 7f2c8a91b4de
Revises: 68cdcf0577df
Create Date: 2026-10-29 20:47:00.000000

Safely reconciles and creates all missing core Anti-Ragging Portal tables,
indexes, and relationships when baseline revision 68cdcf0577df was marked
applied in alembic_version but physical tables are absent in the database.
Handles pre-existing tables idempotently to prevent DuplicateTable errors.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7f2c8a91b4de'
down_revision = '68cdcf0577df'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # 1. Anti-Ragging Committee Members
    if 'committee_member' not in existing_tables:
        op.create_table('committee_member',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('email', sa.String(length=120), nullable=False),
            sa.Column('full_name', sa.String(length=100), nullable=False),
            sa.Column('role', sa.String(length=50), nullable=False, server_default='squad_member'),
            sa.Column('department', sa.String(length=100), nullable=True),
            sa.Column('phone', sa.String(length=20), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('email')
        )
        with op.batch_alter_table('committee_member', schema=None) as batch_op:
            batch_op.create_index('ix_committee_member_email', ['email'], unique=True)

    # 2. Complaints Table
    if 'complaint' not in existing_tables:
        op.create_table('complaint',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('complaint_reference', sa.String(length=50), nullable=True),
            sa.Column('student_email', sa.String(length=120), nullable=False),
            sa.Column('complainant_name', sa.String(length=100), nullable=True),
            sa.Column('complainant_phone', sa.String(length=20), nullable=True),
            sa.Column('is_anonymous', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('complaint_title', sa.String(length=150), nullable=True),
            sa.Column('complaint_text', sa.Text(), nullable=False),
            sa.Column('department', sa.String(length=100), nullable=False),
            sa.Column('assigned_cleaner', sa.String(length=100), nullable=True),
            sa.Column('status', sa.String(length=50), nullable=False, server_default='Open'),
            sa.Column('priority', sa.String(length=20), nullable=True, server_default='Medium'),
            sa.Column('escalation_level', sa.Integer(), nullable=True, server_default='0'),
            sa.Column('admin_remarks', sa.Text(), nullable=True),
            sa.Column('resolution_details', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.Column('closed_at', sa.DateTime(), nullable=True),
            sa.Column('timestamp', sa.DateTime(), nullable=True),
            sa.Column('incident_type', sa.String(length=100), nullable=True),
            sa.Column('severity_level', sa.String(length=50), nullable=True, server_default='Medium'),
            sa.Column('incident_date_time', sa.String(length=100), nullable=True),
            sa.Column('incident_location', sa.String(length=150), nullable=True),
            sa.Column('accused_details', sa.Text(), nullable=True),
            sa.Column('witness_details', sa.Text(), nullable=True),
            sa.Column('is_confidential', sa.Boolean(), nullable=True, server_default=sa.text('false')),
            sa.Column('tracking_token', sa.String(length=64), nullable=True),
            sa.Column('committee_assigned', sa.String(length=100), nullable=True, server_default='Anti-Ragging Committee'),
            sa.Column('assigned_investigator_id', sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(['assigned_investigator_id'], ['committee_member.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('complaint_reference')
        )
        with op.batch_alter_table('complaint', schema=None) as batch_op:
            batch_op.create_index('ix_complaint_complaint_reference', ['complaint_reference'], unique=True)
            batch_op.create_index('ix_complaint_created_at', ['created_at'], unique=False)
            batch_op.create_index('ix_complaint_incident_type', ['incident_type'], unique=False)
            batch_op.create_index('ix_complaint_status', ['status'], unique=False)
            batch_op.create_index('ix_complaint_timestamp', ['timestamp'], unique=False)
            batch_op.create_index('ix_complaint_tracking_token', ['tracking_token'], unique=False)

    # 3. Complaint Evidence
    if 'complaint_evidence' not in existing_tables:
        op.create_table('complaint_evidence',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('complaint_id', sa.Integer(), nullable=False),
            sa.Column('file_type', sa.String(length=50), nullable=False),
            sa.Column('original_filename', sa.String(length=255), nullable=False),
            sa.Column('storage_filename', sa.String(length=255), nullable=False),
            sa.Column('mime_type', sa.String(length=100), nullable=False),
            sa.Column('file_size_bytes', sa.Integer(), nullable=False),
            sa.Column('uploader_role', sa.String(length=20), nullable=False, server_default='student'),
            sa.Column('access_classification', sa.String(length=50), nullable=False, server_default='confidential'),
            sa.Column('uploaded_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['complaint_id'], ['complaint.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('storage_filename')
        )
        with op.batch_alter_table('complaint_evidence', schema=None) as batch_op:
            batch_op.create_index('ix_complaint_evidence_complaint_id', ['complaint_id'], unique=False)

    # 4. Complaint Status History (Audit Trail)
    if 'complaint_status_history' not in existing_tables:
        op.create_table('complaint_status_history',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('complaint_id', sa.Integer(), nullable=False),
            sa.Column('previous_status', sa.String(length=50), nullable=True),
            sa.Column('new_status', sa.String(length=50), nullable=False),
            sa.Column('action_type', sa.String(length=50), nullable=False),
            sa.Column('actor_role', sa.String(length=50), nullable=False),
            sa.Column('actor_email', sa.String(length=120), nullable=True),
            sa.Column('remarks', sa.Text(), nullable=True),
            sa.Column('timestamp', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['complaint_id'], ['complaint.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('complaint_status_history', schema=None) as batch_op:
            batch_op.create_index('ix_complaint_status_history_complaint_id', ['complaint_id'], unique=False)
            batch_op.create_index('ix_complaint_status_history_timestamp', ['timestamp'], unique=False)

    # 5. Investigation Actions
    if 'investigation_action' not in existing_tables:
        op.create_table('investigation_action',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('complaint_id', sa.Integer(), nullable=False),
            sa.Column('assigned_to_id', sa.Integer(), nullable=True),
            sa.Column('assigned_to_name', sa.String(length=100), nullable=True),
            sa.Column('action_type', sa.String(length=50), nullable=False),
            sa.Column('action_summary', sa.Text(), nullable=False),
            sa.Column('confidential_notes', sa.Text(), nullable=True),
            sa.Column('performed_by', sa.String(length=120), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['assigned_to_id'], ['committee_member.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['complaint_id'], ['complaint.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('investigation_action', schema=None) as batch_op:
            batch_op.create_index('ix_investigation_action_complaint_id', ['complaint_id'], unique=False)
            batch_op.create_index('ix_investigation_action_created_at', ['created_at'], unique=False)

    # 6. Notifications
    if 'notification' not in existing_tables:
        op.create_table('notification',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('recipient_role', sa.String(length=20), nullable=False),
            sa.Column('recipient_email', sa.String(length=120), nullable=True),
            sa.Column('complaint_id', sa.Integer(), nullable=True),
            sa.Column('notification_type', sa.String(length=50), nullable=False),
            sa.Column('title', sa.String(length=150), nullable=False),
            sa.Column('message', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('is_read', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('read_at', sa.DateTime(), nullable=True),
            sa.Column('is_resolved', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('resolved_at', sa.DateTime(), nullable=True),
            sa.Column('dedup_key', sa.String(length=120), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('notification', schema=None) as batch_op:
            batch_op.create_index('ix_notification_complaint_id', ['complaint_id'], unique=False)
            batch_op.create_index('ix_notification_created_at', ['created_at'], unique=False)
            batch_op.create_index('ix_notification_dedup_key', ['dedup_key'], unique=False)
            batch_op.create_index('ix_notification_is_read', ['is_read'], unique=False)
            batch_op.create_index('ix_notification_is_resolved', ['is_resolved'], unique=False)
            batch_op.create_index('ix_notification_recipient_email', ['recipient_email'], unique=False)
            batch_op.create_index('ix_notification_recipient_role', ['recipient_role'], unique=False)
            batch_op.create_index('ix_notification_resolved_at', ['resolved_at'], unique=False)

    # 7. Cleaner (Legacy compatibility)
    if 'cleaner' not in existing_tables:
        op.create_table('cleaner',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('department', sa.String(length=50), nullable=False),
            sa.Column('area', sa.String(length=100), nullable=False),
            sa.Column('phone', sa.String(length=20), nullable=False),
            sa.PrimaryKeyConstraint('id')
        )

    # 8. Call Log
    if 'call_log' not in existing_tables:
        op.create_table('call_log',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('complaint_id', sa.Integer(), nullable=False),
            sa.Column('cleaner_name', sa.String(length=100), nullable=False),
            sa.Column('phone_number', sa.String(length=20), nullable=False),
            sa.Column('call_status', sa.String(length=50), nullable=True),
            sa.Column('timestamp', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )

    # 9. SMS Logs
    if 'sms_logs' not in existing_tables:
        op.create_table('sms_logs',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('complaint_id', sa.Integer(), nullable=False),
            sa.Column('to_number', sa.String(length=20), nullable=False),
            sa.Column('message', sa.Text(), nullable=False),
            sa.Column('status', sa.String(length=50), nullable=True),
            sa.Column('timestamp', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )

    # 10. Sessions (Flask-Session SQLAlchemy)
    if 'sessions' not in existing_tables:
        op.create_table('sessions',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('session_id', sa.String(length=255), nullable=True),
            sa.Column('data', sa.LargeBinary(), nullable=True),
            sa.Column('expiry', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('session_id')
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    for table_name in [
        'sms_logs', 'call_log', 'cleaner',
        'notification', 'investigation_action',
        'complaint_status_history', 'complaint_evidence',
        'complaint', 'committee_member'
    ]:
        if table_name in existing_tables:
            op.drop_table(table_name)

