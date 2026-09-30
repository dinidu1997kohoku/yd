# YD Daily Attendance, Absence and Canteen Reporting System

A responsive, interactive front-end prototype for centralizing YD department attendance submissions, absence follow-up, supervisor review, HR coordination, canteen quantities, and report delivery.

## Run locally

Requires Node.js 18 or newer. There are no third-party package dependencies:

```sh
npm start
```

Open `http://localhost:4173` (or the Arena live preview). Run the built-in smoke tests with:

```sh
npm test
```

A Python static-server alternative is also available: `python3 -m http.server 4173 --bind 0.0.0.0`.

## Included in this prototype

- Dashboard with submission progress, attendance metrics, alerts, and canteen summaries.
- Role-preview switcher for YD HR Coordinator, Department User, Department Supervisor, HR Manager, Canteen User, and System Administrator. Navigation and displayed sample data are scoped by role.
- Daily attendance form with calendar date input, department/shift selection, total validation, draft/submission flow, and Employee Master picker.
- Absence follow-up with duration warnings and restricted HR-only detail views.
- Department submission statuses, supervisor approval/correction workflow, and HR coordination.
- Today actual and tomorrow planned canteen quantities, authorized adjustment capture, and receipt confirmation.
- Dedicated daily-absence and canteen email preview flows with recipients, subject/message editing, provisional-report checks, resend history, and explicit confirmation.
- Employee Master, departments, shifts, import mapping preview, user/permission screens, report views, audit log, and settings/backup screens.
- CSV exports, mobile-responsive layout, and browser-local persistence using `localStorage`.
- Reference SQLite schema and seed data in `database/schema.sql` and `database/seed.sql`.

## Database files

- `database/schema.sql` creates the relational tables, indexes, constraints, audit/workflow history, and department/canteen reporting views.
- `database/seed.sql` inserts roles, the 27 departments, four shifts, canteen categories, email templates, and baseline settings. It intentionally creates no default account or password.

Apply them to a SQLite database in order with:

```sh
sqlite3 yd.db < database/schema.sql
sqlite3 yd.db < database/seed.sql
```

These SQL files are schema/seed groundwork only; the current web prototype does not connect to the database yet.

## Prototype and production boundary

This repository was initially empty, so this implementation is a self-contained front-end prototype with illustrative sample records. It is **not a production HR system** and does not provide server-side authentication, authorization, database storage, encrypted document handling, or a mail transport. The role selector is a UI preview—not an access-control boundary. Email actions are recorded as demo events only; no email is delivered. Excel upload currently previews selected filenames but does not parse workbooks. Exports are CSV (openable in Excel), not native `.xlsx`/PDF files. Browser `localStorage` is not a secure database.

Before handling real employee or medical information, deploy a protected backend with authenticated sessions, server-enforced role and department permissions, audited database transactions, encrypted attachment storage, malware scanning, validated Excel parsing, real PDF/XLSX generation, and a configured email service. Replace all sample recipients and records with authorized production data.
