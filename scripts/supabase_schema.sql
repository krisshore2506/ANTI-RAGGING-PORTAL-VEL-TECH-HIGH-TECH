-- ==============================================================================
-- ANTI-RAGGING PORTAL - RECONSTRUCTED SUPABASE POSTGRESQL PRODUCTION DDL
-- Project: anti-ragging-portal-db (Region: ap-south-1 Mumbai)
-- Target: Supabase Managed PostgreSQL
-- ==============================================================================

-- 1. Anti-Ragging Committee Members & Authorized Personnel
CREATE TABLE IF NOT EXISTS committee_member (
    id SERIAL PRIMARY KEY,
    email VARCHAR(120) NOT NULL UNIQUE,
    full_name VARCHAR(100) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'squad_member',
    department VARCHAR(100),
    phone VARCHAR(20),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc') NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc') NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_committee_member_email ON committee_member (email);

-- 2. Anti-Ragging Complaints Table
CREATE TABLE IF NOT EXISTS complaint (
    id SERIAL PRIMARY KEY,
    complaint_reference VARCHAR(50) UNIQUE,
    student_email VARCHAR(120) NOT NULL,
    complainant_name VARCHAR(100),
    complainant_phone VARCHAR(20),
    is_anonymous BOOLEAN NOT NULL DEFAULT FALSE,
    complaint_title VARCHAR(150),
    complaint_text TEXT NOT NULL,
    department VARCHAR(100) NOT NULL,
    assigned_cleaner VARCHAR(100),
    status VARCHAR(50) NOT NULL DEFAULT 'Open',
    priority VARCHAR(20) DEFAULT 'Medium',
    escalation_level INTEGER DEFAULT 0,
    admin_remarks TEXT,
    resolution_details TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc') NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc'),
    closed_at TIMESTAMP WITHOUT TIME ZONE,
    timestamp TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc'),
    incident_type VARCHAR(100),
    severity_level VARCHAR(50) DEFAULT 'Medium',
    incident_date_time VARCHAR(100),
    incident_location VARCHAR(150),
    accused_details TEXT,
    witness_details TEXT,
    is_confidential BOOLEAN DEFAULT FALSE,
    tracking_token VARCHAR(64),
    committee_assigned VARCHAR(100) DEFAULT 'Anti-Ragging Committee',
    assigned_investigator_id INTEGER REFERENCES committee_member(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_complaint_complaint_reference ON complaint (complaint_reference);
CREATE INDEX IF NOT EXISTS ix_complaint_tracking_token ON complaint (tracking_token);
CREATE INDEX IF NOT EXISTS ix_complaint_student_email ON complaint (student_email);
CREATE INDEX IF NOT EXISTS ix_complaint_status ON complaint (status);
CREATE INDEX IF NOT EXISTS ix_complaint_created_at ON complaint (created_at);
CREATE INDEX IF NOT EXISTS ix_complaint_timestamp ON complaint (timestamp);
CREATE INDEX IF NOT EXISTS ix_complaint_incident_type ON complaint (incident_type);

-- 3. Complaint Evidence Files (Private Cloud Attachments)
CREATE TABLE IF NOT EXISTS complaint_evidence (
    id SERIAL PRIMARY KEY,
    complaint_id INTEGER NOT NULL REFERENCES complaint(id) ON DELETE CASCADE,
    file_type VARCHAR(50) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    storage_filename VARCHAR(255) NOT NULL UNIQUE,
    mime_type VARCHAR(100) NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    uploader_role VARCHAR(20) NOT NULL DEFAULT 'student',
    access_classification VARCHAR(50) NOT NULL DEFAULT 'confidential',
    uploaded_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc') NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_complaint_evidence_complaint_id ON complaint_evidence (complaint_id);

-- 4. Complaint Status History (Immutable Lifecycle Audit Trail)
CREATE TABLE IF NOT EXISTS complaint_status_history (
    id SERIAL PRIMARY KEY,
    complaint_id INTEGER NOT NULL REFERENCES complaint(id) ON DELETE CASCADE,
    previous_status VARCHAR(50),
    new_status VARCHAR(50) NOT NULL,
    action_type VARCHAR(50) NOT NULL,
    actor_role VARCHAR(50) NOT NULL,
    actor_email VARCHAR(120),
    remarks TEXT,
    timestamp TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc') NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_complaint_status_history_complaint_id ON complaint_status_history (complaint_id);
CREATE INDEX IF NOT EXISTS ix_complaint_status_history_timestamp ON complaint_status_history (timestamp);

-- 5. Committee Investigation Actions & Hearings
CREATE TABLE IF NOT EXISTS investigation_action (
    id SERIAL PRIMARY KEY,
    complaint_id INTEGER NOT NULL REFERENCES complaint(id) ON DELETE CASCADE,
    assigned_to_id INTEGER REFERENCES committee_member(id) ON DELETE SET NULL,
    assigned_to_name VARCHAR(100),
    action_type VARCHAR(50) NOT NULL,
    action_summary TEXT NOT NULL,
    confidential_notes TEXT,
    performed_by VARCHAR(120) NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc') NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_investigation_action_complaint_id ON investigation_action (complaint_id);
CREATE INDEX IF NOT EXISTS ix_investigation_action_created_at ON investigation_action (created_at);

-- 6. In-App Notifications & Action Reminders
CREATE TABLE IF NOT EXISTS notification (
    id SERIAL PRIMARY KEY,
    recipient_role VARCHAR(20) NOT NULL,
    recipient_email VARCHAR(120),
    complaint_id INTEGER,
    notification_type VARCHAR(50) NOT NULL,
    title VARCHAR(150) NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc') NOT NULL,
    is_read BOOLEAN DEFAULT FALSE NOT NULL,
    read_at TIMESTAMP WITHOUT TIME ZONE,
    is_resolved BOOLEAN DEFAULT FALSE NOT NULL,
    resolved_at TIMESTAMP WITHOUT TIME ZONE,
    dedup_key VARCHAR(120)
);

CREATE INDEX IF NOT EXISTS ix_notification_recipient_role ON notification (recipient_role);
CREATE INDEX IF NOT EXISTS ix_notification_recipient_email ON notification (recipient_email);
CREATE INDEX IF NOT EXISTS ix_notification_complaint_id ON notification (complaint_id);
CREATE INDEX IF NOT EXISTS ix_notification_created_at ON notification (created_at);
CREATE INDEX IF NOT EXISTS ix_notification_is_read ON notification (is_read);
CREATE INDEX IF NOT EXISTS ix_notification_is_resolved ON notification (is_resolved);
CREATE INDEX IF NOT EXISTS ix_notification_resolved_at ON notification (resolved_at);
CREATE INDEX IF NOT EXISTS ix_notification_dedup_key ON notification (dedup_key);

-- 7. Legacy Compatibility Tables
CREATE TABLE IF NOT EXISTS cleaner (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    department VARCHAR(50) NOT NULL,
    area VARCHAR(100) NOT NULL,
    phone VARCHAR(20) NOT NULL
);

CREATE TABLE IF NOT EXISTS call_log (
    id SERIAL PRIMARY KEY,
    complaint_id INTEGER NOT NULL,
    cleaner_name VARCHAR(100) NOT NULL,
    phone_number VARCHAR(20) NOT NULL,
    call_status VARCHAR(50) DEFAULT 'Triggered',
    timestamp TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS ix_call_log_complaint_id ON call_log (complaint_id);

CREATE TABLE IF NOT EXISTS sms_logs (
    id SERIAL PRIMARY KEY,
    complaint_id INTEGER NOT NULL,
    to_number VARCHAR(20) NOT NULL,
    message TEXT NOT NULL,
    status VARCHAR(50),
    timestamp TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS ix_sms_logs_complaint_id ON sms_logs (complaint_id);

-- 8. Production Server Sessions (Flask-Session SQLAlchemy Backend)
CREATE TABLE IF NOT EXISTS sessions (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) UNIQUE,
    data BYTEA,
    expiry TIMESTAMP WITHOUT TIME ZONE
);

CREATE INDEX IF NOT EXISTS ix_sessions_session_id ON sessions (session_id);
CREATE INDEX IF NOT EXISTS ix_sessions_expiry ON sessions (expiry);

-- ==============================================================================
-- ROW LEVEL SECURITY (RLS) POLICIES
-- Protect student identity and grievance records from unauthorized public API inspection.
-- Backend processes connect via authenticated service role / direct pooler connection.
-- ==============================================================================

ALTER TABLE committee_member ENABLE ROW LEVEL SECURITY;
ALTER TABLE complaint ENABLE ROW LEVEL SECURITY;
ALTER TABLE complaint_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE complaint_status_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE investigation_action ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification ENABLE ROW LEVEL SECURITY;
ALTER TABLE cleaner ENABLE ROW LEVEL SECURITY;
ALTER TABLE call_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE sms_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_full_access_committee_member') THEN
        CREATE POLICY service_role_full_access_committee_member ON committee_member FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_full_access_complaint') THEN
        CREATE POLICY service_role_full_access_complaint ON complaint FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_full_access_evidence') THEN
        CREATE POLICY service_role_full_access_evidence ON complaint_evidence FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_full_access_status_history') THEN
        CREATE POLICY service_role_full_access_status_history ON complaint_status_history FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_full_access_investigation') THEN
        CREATE POLICY service_role_full_access_investigation ON investigation_action FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_full_access_notification') THEN
        CREATE POLICY service_role_full_access_notification ON notification FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_full_access_cleaner') THEN
        CREATE POLICY service_role_full_access_cleaner ON cleaner FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_full_access_call_log') THEN
        CREATE POLICY service_role_full_access_call_log ON call_log FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_full_access_sms_logs') THEN
        CREATE POLICY service_role_full_access_sms_logs ON sms_logs FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_full_access_sessions') THEN
        CREATE POLICY service_role_full_access_sessions ON sessions FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;

-- Supabase Storage Private Bucket Setup (for complaint-evidence)
INSERT INTO storage.buckets (id, name, public)
VALUES ('complaint-evidence', 'complaint-evidence', false)
ON CONFLICT (id) DO NOTHING;
