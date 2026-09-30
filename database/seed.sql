-- Reference seed data only. This does NOT create a user account or set a default password.
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

INSERT OR IGNORE INTO roles(role_key, display_name, description) VALUES
 ('system_administrator','System Administrator','Manage users, departments, roles, shifts, settings, audit and backups.'),
 ('department_user','Department User','Submit daily information for the assigned department.'),
 ('department_supervisor','Department Supervisor','Review, approve or return assigned department submissions.'),
 ('yd_hr_coordinator','YD HR Coordinator','Coordinate all departments and finalize/send daily reports.'),
 ('hr_manager','HR Manager / Report Viewer','Read-only management reports and analytics.'),
 ('canteen_user','Canteen User','View finalized meal quantities and acknowledge receipt only.');

INSERT OR IGNORE INTO departments(name, department_type) VALUES
 ('Appearance','production_line'),('ARC','production_line'),('ATT','production_line'),
 ('Body Assembly','production_line'),('Collimator','production_line'),('Common','department'),
 ('Crystal','production_line'),('Document','department'),('Engineering','department'),
 ('Equipment','department'),('Fiber','production_line'),('FR','production_line'),
 ('Inspection','department'),('Maintenance','department'),('Material Processing','production_line'),
 ('MUX Crystal','production_line'),('Passive','production_line'),('PBC','production_line'),
 ('PC','production_line'),('QC','department'),('QC-C','department'),('QC-E','department'),
 ('Silicon','production_line'),('Staff','department'),('Stores','department'),
 ('Training Section','department'),('Unit','department');

INSERT OR IGNORE INTO shifts(name, starts_at, ends_at, crosses_midnight) VALUES
 ('Day 1','06:00','14:00',0),
 ('Day 2','08:00','16:00',0),
 ('Evening','14:00','22:00',0),
 ('Night','22:00','06:00',1);

INSERT OR IGNORE INTO canteen_categories(category_code, category_name, sort_order, unit_name, time_label) VALUES
 ('TEA_0700','7:00 AM tea',10,'servings','07:00'),
 ('TEA_1000','10:00 AM tea',20,'servings','10:00'),
 ('TEA_1500','3:00 PM tea',30,'servings','15:00'),
 ('TEA_1600','4:00 PM tea',40,'servings','16:00'),
 ('LUNCH','Lunch',50,'meals',''),
 ('DINNER','Dinner',60,'meals',''),
 ('SNACK_TEA','Snack & tea',70,'sets',''),
 ('VEGETARIAN','Vegetarian meals',80,'meals',''),
 ('SPECIAL_MEAL','Special meals',90,'meals','');

INSERT OR IGNORE INTO email_templates(report_type, subject_template, body_template, preferred_attachment_format) VALUES
 ('daily_absent','Daily Absent Summary Report {report_date}',
  'Dear Rishmi san,\n\nPlease find attached the Daily Absent Summary Report for {report_date}.\n\nThanks,\n{sender_name}', 'xlsx'),
 ('canteen','YD Tea and Meal Requirement Report {report_date}',
  'Dear Canteen Team,\n\nPlease find attached the YD Tea and Meal Requirement Report for {report_date}.\n\nPlease refer to the attached report for required tea, lunch, dinner, and snack quantities.\n\nThanks,\n{sender_name}', 'xlsx'),
 ('reminder','Daily attendance submission reminder {report_date}',
  'Please submit your department attendance report for {report_date}.', 'xlsx'),
 ('correction','Correction required: daily attendance {report_date}',
  'Please review the correction request and resubmit your department report.', 'xlsx');

INSERT OR IGNORE INTO system_settings(setting_key, setting_value) VALUES
 ('timezone','Asia/Colombo'),
 ('submission_deadline','08:00'),
 ('session_timeout_minutes','30'),
 ('max_failed_login_attempts','5'),
 ('allow_future_attendance_dates','0'),
 ('default_report_attachment_format','xlsx');

COMMIT;
