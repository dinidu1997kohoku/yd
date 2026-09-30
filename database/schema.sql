-- YD Daily Attendance, Absence and Canteen Reporting System
-- SQLite reference schema. Enable PRAGMA foreign_keys=ON on every connection.
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS roles (
  role_key TEXT PRIMARY KEY,
  display_name TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS departments (
  department_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  department_type TEXT NOT NULL DEFAULT 'department'
    CHECK (department_type IN ('production_line','department')),
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  submission_deadline TEXT NOT NULL DEFAULT '08:00',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS shifts (
  shift_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  starts_at TEXT NOT NULL CHECK (starts_at GLOB '[0-2][0-9]:[0-5][0-9]'),
  ends_at TEXT NOT NULL CHECK (ends_at GLOB '[0-2][0-9]:[0-5][0-9]'),
  crosses_midnight INTEGER NOT NULL DEFAULT 0 CHECK (crosses_midnight IN (0,1)),
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  CHECK ((crosses_midnight = 1 AND ends_at < starts_at) OR
         (crosses_midnight = 0 AND ends_at > starts_at))
);

-- Passwords must be salted, slow hashes (e.g. PBKDF2/Argon2), never plaintext.
CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  display_name TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '',
  password_hash TEXT NOT NULL,
  password_salt TEXT NOT NULL,
  role_key TEXT NOT NULL REFERENCES roles(role_key),
  assigned_department_id INTEGER REFERENCES departments(department_id),
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  failed_login_attempts INTEGER NOT NULL DEFAULT 0 CHECK (failed_login_attempts >= 0),
  locked_until TEXT,
  must_reset_password INTEGER NOT NULL DEFAULT 1 CHECK (must_reset_password IN (0,1)),
  last_login_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (role_key NOT IN ('department_user','department_supervisor') OR assigned_department_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_users_department ON users(assigned_department_id);

CREATE TABLE IF NOT EXISTS sessions (
  session_id INTEGER PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  csrf_hash TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  expires_at INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS employees (
  employee_no TEXT PRIMARY KEY,
  employee_name TEXT NOT NULL,
  department_id INTEGER NOT NULL REFERENCES departments(department_id),
  production_line TEXT NOT NULL DEFAULT '',
  section_name TEXT NOT NULL DEFAULT '',
  current_shift_id INTEGER REFERENCES shifts(shift_id),
  employment_category TEXT NOT NULL DEFAULT 'permanent',
  joined_date TEXT CHECK (joined_date IS NULL OR (date(joined_date, '+0 days') = joined_date)),
  contact_information TEXT,
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  meal_preference TEXT NOT NULL DEFAULT 'non_vegetarian'
    CHECK (meal_preference IN ('vegetarian','non_vegetarian')),
  special_meal_requirement TEXT,
  supervisor_employee_no TEXT REFERENCES employees(employee_no),
  authorized_notes TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_employees_department ON employees(department_id, is_active);
CREATE INDEX IF NOT EXISTS idx_employees_name ON employees(employee_name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS public_holidays (
  holiday_date TEXT PRIMARY KEY CHECK (date(holiday_date, '+0 days') = holiday_date),
  holiday_name TEXT NOT NULL,
  is_working_day INTEGER NOT NULL DEFAULT 0 CHECK (is_working_day IN (0,1))
);

CREATE TABLE IF NOT EXISTS attendance_submissions (
  submission_id INTEGER PRIMARY KEY,
  report_date TEXT NOT NULL CHECK (date(report_date, '+0 days') = report_date),
  department_id INTEGER NOT NULL REFERENCES departments(department_id),
  shift_id INTEGER NOT NULL REFERENCES shifts(shift_id),
  revision_no INTEGER NOT NULL DEFAULT 1 CHECK (revision_no >= 1),
  revision_of INTEGER REFERENCES attendance_submissions(submission_id),
  is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0,1)),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN (
    'draft','submitted','supervisor_review','returned_for_correction',
    'supervisor_approved','submitted_to_hr','hr_review','finalized','included_in_report'
  )),
  total_strength INTEGER NOT NULL DEFAULT 0 CHECK (total_strength >= 0),
  present_count INTEGER NOT NULL DEFAULT 0 CHECK (present_count >= 0),
  absent_count INTEGER NOT NULL DEFAULT 0 CHECK (absent_count >= 0),
  overtime_count INTEGER NOT NULL DEFAULT 0 CHECK (overtime_count >= 0),
  approved_leave_count INTEGER NOT NULL DEFAULT 0 CHECK (approved_leave_count >= 0),
  other_attendance_count INTEGER NOT NULL DEFAULT 0 CHECK (other_attendance_count >= 0),
  lunch_overtime_count INTEGER NOT NULL DEFAULT 0 CHECK (lunch_overtime_count >= 0),
  tea_overtime_count INTEGER NOT NULL DEFAULT 0 CHECK (tea_overtime_count >= 0),
  dinner_overtime_count INTEGER NOT NULL DEFAULT 0 CHECK (dinner_overtime_count >= 0),
  remarks TEXT NOT NULL DEFAULT '',
  prepared_by INTEGER REFERENCES users(user_id),
  submitted_at TEXT,
  supervisor_approved_by INTEGER REFERENCES users(user_id),
  supervisor_approved_at TEXT,
  hr_finalized_by INTEGER REFERENCES users(user_id),
  hr_finalized_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (report_date, department_id, shift_id, revision_no),
  CHECK (present_count + absent_count + approved_leave_count + other_attendance_count <= total_strength)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_current_submission
  ON attendance_submissions(report_date, department_id, shift_id) WHERE is_current = 1;
CREATE INDEX IF NOT EXISTS idx_submission_status_date
  ON attendance_submissions(report_date, status, department_id);

-- Exactly one attendance status per employee/submission prevents present+absent duplicates.
CREATE TABLE IF NOT EXISTS attendance_entries (
  attendance_entry_id INTEGER PRIMARY KEY,
  submission_id INTEGER NOT NULL REFERENCES attendance_submissions(submission_id) ON DELETE CASCADE,
  employee_no TEXT NOT NULL REFERENCES employees(employee_no),
  attendance_status TEXT NOT NULL CHECK (attendance_status IN (
    'present','absent','approved_leave','other'
  )),
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (submission_id, employee_no)
);
CREATE INDEX IF NOT EXISTS idx_attendance_employee ON attendance_entries(employee_no);

CREATE TABLE IF NOT EXISTS overtime_entries (
  overtime_entry_id INTEGER PRIMARY KEY,
  submission_id INTEGER NOT NULL REFERENCES attendance_submissions(submission_id) ON DELETE CASCADE,
  employee_no TEXT NOT NULL REFERENCES employees(employee_no),
  overtime_type TEXT NOT NULL CHECK (overtime_type IN ('lunch','tea_time','dinner','other')),
  overtime_hours REAL NOT NULL DEFAULT 0 CHECK (overtime_hours >= 0),
  supervisor_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (supervisor_status IN ('pending','approved','rejected')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (submission_id, employee_no, overtime_type)
);

CREATE TABLE IF NOT EXISTS absence_records (
  absence_id INTEGER PRIMARY KEY,
  employee_no TEXT NOT NULL REFERENCES employees(employee_no),
  department_id INTEGER NOT NULL REFERENCES departments(department_id),
  shift_id INTEGER REFERENCES shifts(shift_id),
  report_date TEXT NOT NULL CHECK (date(report_date, '+0 days') = report_date),
  absence_start_date TEXT NOT NULL CHECK (date(absence_start_date, '+0 days') = absence_start_date),
  expected_return_date TEXT CHECK (expected_return_date IS NULL OR date(expected_return_date, '+0 days') = expected_return_date),
  actual_return_date TEXT CHECK (actual_return_date IS NULL OR date(actual_return_date, '+0 days') = actual_return_date),
  absent_days INTEGER NOT NULL DEFAULT 1 CHECK (absent_days >= 1),
  early_approved_leave INTEGER NOT NULL DEFAULT 0 CHECK (early_approved_leave IN (0,1)),
  informed_status TEXT NOT NULL DEFAULT 'not_informed' CHECK (informed_status IN ('informed','not_informed')),
  informed_at TEXT,
  absence_reason TEXT NOT NULL DEFAULT '',
  comment TEXT NOT NULL DEFAULT '',
  medical_document_status TEXT NOT NULL DEFAULT 'not_required'
    CHECK (medical_document_status IN ('not_required','pending','received','restricted')),
  restricted_attachment_key TEXT,
  follow_up_status TEXT NOT NULL DEFAULT 'open'
    CHECK (follow_up_status IN ('open','in_progress','resolved','escalated')),
  hr_confidential_note TEXT NOT NULL DEFAULT '',
  record_status TEXT NOT NULL DEFAULT 'active'
    CHECK (record_status IN ('active','corrected','closed')),
  created_by INTEGER REFERENCES users(user_id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_absence_followup ON absence_records(follow_up_status, report_date);
CREATE INDEX IF NOT EXISTS idx_absence_employee ON absence_records(employee_no, absence_start_date);
CREATE INDEX IF NOT EXISTS idx_absence_department ON absence_records(department_id, report_date);

CREATE TABLE IF NOT EXISTS canteen_categories (
  category_id INTEGER PRIMARY KEY,
  category_code TEXT NOT NULL UNIQUE,
  category_name TEXT NOT NULL UNIQUE,
  sort_order INTEGER NOT NULL DEFAULT 0,
  unit_name TEXT NOT NULL DEFAULT 'servings',
  time_label TEXT NOT NULL DEFAULT '',
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
);

CREATE TABLE IF NOT EXISTS canteen_requirements (
  requirement_id INTEGER PRIMARY KEY,
  report_date TEXT NOT NULL CHECK (date(report_date, '+0 days') = report_date),
  plan_type TEXT NOT NULL CHECK (plan_type IN ('actual','planned')),
  category_id INTEGER NOT NULL REFERENCES canteen_categories(category_id),
  shift_id INTEGER REFERENCES shifts(shift_id),
  system_calculated_quantity INTEGER NOT NULL DEFAULT 0 CHECK (system_calculated_quantity >= 0),
  manual_adjustment_quantity INTEGER NOT NULL DEFAULT 0,
  final_approved_quantity INTEGER NOT NULL DEFAULT 0 CHECK (final_approved_quantity >= 0),
  adjustment_reason_summary TEXT NOT NULL DEFAULT '',
  comments TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','under_review','finalized','sent','received')),
  version_no INTEGER NOT NULL DEFAULT 1 CHECK (version_no >= 1),
  generated_by INTEGER REFERENCES users(user_id),
  approved_by INTEGER REFERENCES users(user_id),
  generated_at TEXT,
  approved_at TEXT,
  UNIQUE (report_date, plan_type, category_id, shift_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_canteen_date_type ON canteen_requirements(report_date, plan_type, status);

CREATE TABLE IF NOT EXISTS canteen_adjustments (
  adjustment_id INTEGER PRIMARY KEY,
  requirement_id INTEGER NOT NULL REFERENCES canteen_requirements(requirement_id),
  adjustment_amount INTEGER NOT NULL CHECK (adjustment_amount <> 0),
  adjustment_reason TEXT NOT NULL CHECK (length(trim(adjustment_reason)) > 0),
  reason_category TEXT NOT NULL CHECK (reason_category IN (
    'visitors','meetings','training','special_event','additional_overtime',
    'last_minute_change','other'
  )),
  adjusted_by INTEGER NOT NULL REFERENCES users(user_id),
  adjusted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_canteen_adjustments_requirement ON canteen_adjustments(requirement_id);

CREATE TABLE IF NOT EXISTS workflow_history (
  workflow_event_id INTEGER PRIMARY KEY,
  submission_id INTEGER NOT NULL REFERENCES attendance_submissions(submission_id),
  actor_user_id INTEGER REFERENCES users(user_id),
  action TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_workflow_submission ON workflow_history(submission_id, created_at);

CREATE TABLE IF NOT EXISTS report_versions (
  report_version_id INTEGER PRIMARY KEY,
  report_type TEXT NOT NULL CHECK (report_type IN ('daily_absent','canteen_requirement','attendance')),
  report_date TEXT NOT NULL CHECK (date(report_date, '+0 days') = report_date),
  version_no INTEGER NOT NULL CHECK (version_no >= 1),
  version_label TEXT NOT NULL DEFAULT 'Final',
  is_provisional INTEGER NOT NULL DEFAULT 0 CHECK (is_provisional IN (0,1)),
  status TEXT NOT NULL DEFAULT 'generated' CHECK (status IN ('generated','approved','sent','superseded')),
  file_key TEXT,
  generated_by INTEGER REFERENCES users(user_id),
  generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (report_type, report_date, version_no)
);

CREATE TABLE IF NOT EXISTS email_recipient_settings (
  recipient_setting_id INTEGER PRIMARY KEY,
  report_type TEXT NOT NULL CHECK (report_type IN ('daily_absent','canteen','reminder','correction')),
  recipient_kind TEXT NOT NULL CHECK (recipient_kind IN ('to','cc','bcc')),
  email_address TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  created_by INTEGER REFERENCES users(user_id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (report_type, recipient_kind, email_address)
);

CREATE TABLE IF NOT EXISTS email_history (
  email_id INTEGER PRIMARY KEY,
  report_version_id INTEGER REFERENCES report_versions(report_version_id),
  report_type TEXT NOT NULL CHECK (report_type IN ('daily_absent','canteen','reminder','correction')),
  report_date TEXT NOT NULL CHECK (date(report_date, '+0 days') = report_date),
  version_no INTEGER NOT NULL DEFAULT 1,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  to_recipients TEXT NOT NULL,
  cc_recipients TEXT NOT NULL DEFAULT '',
  bcc_recipients TEXT NOT NULL DEFAULT '',
  attachment_name TEXT NOT NULL DEFAULT '',
  sender_user_id INTEGER REFERENCES users(user_id),
  delivery_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (delivery_status IN ('pending','sent','failed','test','not_configured')),
  failure_reason TEXT NOT NULL DEFAULT '',
  resend_of_email_id INTEGER REFERENCES email_history(email_id),
  corrected_report_version_id INTEGER REFERENCES report_versions(report_version_id),
  sent_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_email_history_date_type ON email_history(report_date, report_type, delivery_status);

CREATE TABLE IF NOT EXISTS email_templates (
  template_id INTEGER PRIMARY KEY,
  report_type TEXT NOT NULL UNIQUE,
  sender_display_name TEXT NOT NULL DEFAULT 'YD HR Coordinator',
  subject_template TEXT NOT NULL,
  body_template TEXT NOT NULL,
  preferred_attachment_format TEXT NOT NULL DEFAULT 'xlsx'
    CHECK (preferred_attachment_format IN ('xlsx','pdf','xlsx_pdf')),
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  updated_by INTEGER REFERENCES users(user_id),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS import_batches (
  import_batch_id INTEGER PRIMARY KEY,
  workbook_name TEXT NOT NULL,
  file_sha256 TEXT NOT NULL UNIQUE,
  import_type TEXT NOT NULL,
  department_id INTEGER REFERENCES departments(department_id),
  status TEXT NOT NULL DEFAULT 'uploaded'
    CHECK (status IN ('uploaded','mapping','validated','partially_imported','imported','failed')),
  total_rows INTEGER NOT NULL DEFAULT 0,
  accepted_rows INTEGER NOT NULL DEFAULT 0,
  rejected_rows INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER REFERENCES users(user_id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS import_row_errors (
  import_row_error_id INTEGER PRIMARY KEY,
  import_batch_id INTEGER NOT NULL REFERENCES import_batches(import_batch_id) ON DELETE CASCADE,
  row_number INTEGER NOT NULL,
  field_name TEXT NOT NULL DEFAULT '',
  error_code TEXT NOT NULL,
  error_message TEXT NOT NULL,
  raw_row_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (import_batch_id, row_number, field_name, error_code)
);

CREATE TABLE IF NOT EXISTS notifications (
  notification_id INTEGER PRIMARY KEY,
  user_id INTEGER REFERENCES users(user_id),
  department_id INTEGER REFERENCES departments(department_id),
  notification_type TEXT NOT NULL,
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  related_record_type TEXT NOT NULL DEFAULT '',
  related_record_id INTEGER,
  read_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_notifications_user_unread ON notifications(user_id, read_at, created_at);

CREATE TABLE IF NOT EXISTS audit_log (
  audit_id INTEGER PRIMARY KEY,
  user_id INTEGER REFERENCES users(user_id),
  action TEXT NOT NULL,
  affected_record_type TEXT NOT NULL DEFAULT '',
  affected_record_id TEXT NOT NULL DEFAULT '',
  previous_value_json TEXT,
  new_value_json TEXT,
  authorized_reason TEXT NOT NULL DEFAULT '',
  ip_address TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS backup_history (
  backup_id INTEGER PRIMARY KEY,
  backup_key TEXT NOT NULL,
  backup_type TEXT NOT NULL DEFAULT 'full' CHECK (backup_type IN ('full','database','documents')),
  status TEXT NOT NULL CHECK (status IN ('started','successful','failed','restored')),
  checksum_sha256 TEXT NOT NULL DEFAULT '',
  created_by INTEGER REFERENCES users(user_id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT,
  failure_reason TEXT NOT NULL DEFAULT '',
  restore_approved_by INTEGER REFERENCES users(user_id),
  restored_at TEXT
);

CREATE TABLE IF NOT EXISTS system_settings (
  setting_key TEXT PRIMARY KEY,
  setting_value TEXT NOT NULL,
  updated_by INTEGER REFERENCES users(user_id),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Department/shift/date status for HR coordination; no employee-level details exposed here.
CREATE VIEW IF NOT EXISTS v_department_submission_status AS
SELECT d.department_id, d.name AS department_name, s.shift_id, s.name AS shift_name,
       a.report_date, a.status, a.total_strength, a.present_count, a.absent_count,
       a.submitted_at, a.supervisor_approved_at, a.hr_finalized_at, a.is_current
FROM attendance_submissions a
JOIN departments d ON d.department_id = a.department_id
JOIN shifts s ON s.shift_id = a.shift_id
WHERE a.is_current = 1;

-- Safe aggregate view intended for the Canteen role. Do not grant access to employee or absence tables.
CREATE VIEW IF NOT EXISTS v_canteen_requirement_summary AS
SELECT r.report_date, r.plan_type, c.category_name, s.name AS shift_name,
       r.system_calculated_quantity, r.manual_adjustment_quantity,
       r.final_approved_quantity, r.status, r.version_no
FROM canteen_requirements r
JOIN canteen_categories c ON c.category_id = r.category_id
LEFT JOIN shifts s ON s.shift_id = r.shift_id;

INSERT OR IGNORE INTO schema_migrations(version, description)
VALUES (1, 'Initial centralized attendance, absence, workflow, canteen and audit schema');

COMMIT;
