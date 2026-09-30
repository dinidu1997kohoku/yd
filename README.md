# YD Daily Attendance, Absence and Canteen Reporting System

A runnable full-stack project foundation for centralizing YD department attendance, absence follow-up, supervisor review, canteen planning, and report delivery. The browser UI is served by a same-origin Python application server; SQLite stores the operational records.

## Run locally

Requires Python 3.10 or newer. The application uses only the Python standard library; Node.js 18+ and npm are optional conveniences for the `npm` commands and frontend smoke tests.

```sh
npm start
```

Or start it directly without Node.js:

```sh
python3 server.py
```

On first run, the server creates `instance/yd.sqlite3` and prints a one-time administrator username and generated password in the terminal. Save the password. Visit <http://localhost:4173> and sign in. The SQLite database and password are not committed to Git. If the password is lost, reset it from the host with `python3 server.py --reset-password --username admin` while the server is stopped.

Create accounts while the server is stopped (or in a second terminal) with the administrator role key:

```sh
python3 server.py --create-user --username supervisor1 --display-name "Line Supervisor" --role department_supervisor --department Appearance
```

The command prompts for a password of at least 12 characters. Supported roles are `system_administrator`, `department_user`, `department_supervisor`, `yd_hr_coordinator`, `hr_manager`, and `canteen_user`. Department users and supervisors require `--department`.

Run the tests:

```sh
npm test
python3 -m unittest discover -s test -p 'test_*.py' -v
```

## Database

- `database/schema.sql` contains the relational schema, uniqueness and date/count constraints, indexes, workflow/audit history, and aggregate canteen reporting views.
- `database/seed.sql` seeds the six roles, 27 departments, four shifts, canteen categories, templates, and baseline settings.
- The application initializes these files automatically on first run. To create a separate empty database manually: `sqlite3 yd.db < database/schema.sql && sqlite3 yd.db < database/seed.sql`.

## Implemented server-backed flows

- Secure sign-in with PBKDF2 password hashes, failed-login throttling, expiring HttpOnly/SameSite session cookies, CSRF tokens, and logout.
- Server-enforced role and assigned-department checks for bootstrap data, attendance submissions, absence records, employee access, supervisor/HR workflow actions, canteen adjustments/plans, and user creation.
- Attendance date/count and duplicate-submission checks; corrections require reasons; revisions are preserved.
- Employee master writes, authorized absence reporting, audit events, canteen system quantity recalculation from submitted totals, manual adjustments, planned quantities, and canteen receipt/finalization workflow.
- A provisional-report validation/preview endpoint and separate email histories. Email delivery intentionally fails closed until SMTP is configured; it does not claim success when no mail transport is available.
- A canteen role receives aggregate meal quantities only; employee and absence API routes are denied.
- Static files are allowlisted; database files, SQL, and server source are not served publicly. Standard security headers are applied.

## Configure email before use

Recipient allowlists are synchronized from `YD_DAILY_ABSENT_TO`, `YD_CANTEEN_TO`, and optional `YD_REPORT_CC` (comma-separated addresses) at startup; removed addresses are deactivated on restart. SMTP uses `YD_SMTP_HOST`, `YD_SMTP_PORT` (default 587), `YD_SMTP_USER`, `YD_SMTP_PASSWORD`, and `YD_SMTP_FROM`. Configure these in the server environment, then restart. Reports are previewed and downloaded as CSV from the server; send attempts validate configured recipients and record SMTP success or failure in history. Without an authorized recipient plus SMTP transport, sending fails closed.

For HTTPS deployments set `YD_COOKIE_SECURE=1` and terminate TLS at a trusted reverse proxy. Keep the database, backups, SMTP secrets, and uploaded documents out of the public web root.

## Current scope / production follow-up

This is a substantial runnable project foundation, not a claim that every item in the original enterprise specification is finished. Historical Excel parsing, native XLSX/PDF generation, encrypted document upload and malware scanning, backup/restore execution, comprehensive system-settings screens, public-holiday administration, and deployment hardening still need implementation. The email transport requires organization-approved SMTP and recipient configuration. Review the authorization model and run a security assessment before using real HR/medical records in production.
