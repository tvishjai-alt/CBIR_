-- ============================================================================
-- Tele-Radiology Workflow Management System: SQLite Schema (Member 3)
-- Compliant with SQLite3 standard constraints and foreign key relationships.
-- ============================================================================

PRAGMA foreign_keys = ON;

-- 1. Clinics Table: Partnering clinical sites uploading imaging cases
CREATE TABLE IF NOT EXISTS clinics (
    clinic_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL
);

-- 2. Radiologists Table: Active and inactive specialists reading cases
CREATE TABLE IF NOT EXISTS radiologists (
    rad_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    subspecialty TEXT NOT NULL CHECK(subspecialty IN ('Neuro', 'Chest', 'MSK', 'General')),
    max_capacity INTEGER NOT NULL DEFAULT 6,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    avg_report_minutes REAL NOT NULL DEFAULT 15.0
);

-- 3. Cases Table: Central imaging studies being processed
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    clinic_id TEXT NOT NULL,
    patient_ref TEXT NOT NULL,
    modality TEXT NOT NULL CHECK(modality IN ('X-ray', 'CT', 'MRI')),
    body_part TEXT NOT NULL,
    urgency_flag INTEGER NOT NULL DEFAULT 0 CHECK(urgency_flag IN (0, 1)),
    image_path TEXT NOT NULL,
    upload_time TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'Uploaded',
        'Stored',
        'Retrieved',
        'Assigned',
        'Reassigned',
        'In_Review',
        'Reported',
        'Returned_To_Clinic'
    )),
    priority_label TEXT NOT NULL CHECK(priority_label IN ('Critical', 'High', 'Routine')),
    priority_score REAL NOT NULL DEFAULT 0.0,
    assigned_rad_id TEXT,
    FOREIGN KEY (clinic_id) REFERENCES clinics(clinic_id) ON UPDATE CASCADE,
    FOREIGN KEY (assigned_rad_id) REFERENCES radiologists(rad_id) ON UPDATE CASCADE
);

-- 4. Assignments Table: Audit history of case-to-radiologist allocations
CREATE TABLE IF NOT EXISTS assignments (
    assignment_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    rad_id TEXT NOT NULL,
    assigned_time TEXT NOT NULL,
    assignment_reason TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE,
    FOREIGN KEY (rad_id) REFERENCES radiologists(rad_id) ON UPDATE CASCADE
);

-- 5. Reports Table: Diagnostic findings, timings, and CBIR usage
CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL UNIQUE,
    rad_id TEXT NOT NULL,
    review_start_time TEXT NOT NULL,
    report_submit_time TEXT NOT NULL,
    report_text TEXT NOT NULL,
    used_similar_cases INTEGER NOT NULL DEFAULT 0 CHECK(used_similar_cases IN (0, 1)),
    similar_cases_viewed INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE,
    FOREIGN KEY (rad_id) REFERENCES radiologists(rad_id) ON UPDATE CASCADE
);

-- 6. Events Table: Immutable event audit trail for state machine and turnaround analytics
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    status TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);

-- 7. Retrieval Log: Record of Top-K CBIR visual search calls and latency
CREATE TABLE IF NOT EXISTS retrieval_log (
    log_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    top_k_json TEXT NOT NULL,
    retrieval_ms REAL NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('baseline', 'cbir', 'cbir_rerank')),
    created_at TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);

-- 8. SLA Configuration: Maximum acceptable turnaround in minutes
CREATE TABLE IF NOT EXISTS sla_config (
    priority_label TEXT PRIMARY KEY CHECK(priority_label IN ('Critical', 'High', 'Routine')),
    max_minutes INTEGER NOT NULL
);

-- Performance Indexes
CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
CREATE INDEX IF NOT EXISTS idx_cases_assigned_rad ON cases(assigned_rad_id);
CREATE INDEX IF NOT EXISTS idx_cases_priority ON cases(priority_label, priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_events_case_id ON events(case_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_assignments_case_id ON assignments(case_id);
CREATE INDEX IF NOT EXISTS idx_reports_rad_id ON reports(rad_id);
CREATE INDEX IF NOT EXISTS idx_retrieval_log_case_id ON retrieval_log(case_id);
