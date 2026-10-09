# CFS Operations Platform — Phase 1

Phase 1 is the project foundation for the CFS Operations platform. It contains:

- sign-in with email and password (session cookie + CSRF protection)
- four roles: **Employee**, **Operations Manager**, **HR**, **Admin**
- Admin user management (create users, change roles, activate/deactivate, set passwords)
- an **append-only audit log** (PostgreSQL itself refuses edits and deletes)
- OpenAPI / Swagger API documentation
- a React + TypeScript + MUI web app with role-based navigation

It does **not** yet contain tasks, SLAs, calendars, notifications, employees/departments or KPIs.
Those arrive in later phases (Celery + Redis in Phase 5, WebSockets in Phase 8).

| Part | Technology |
| --- | --- |
| Backend (all business rules) | Python 3.12, Django 5.2, Django REST Framework 3.16 |
| Database | PostgreSQL 16 (Docker) |
| Frontend (display only) | React 18, TypeScript, MUI 6, Vite |
| API docs | drf-spectacular (OpenAPI 3 + Swagger UI) |
| Tests | pytest (backend), Vitest (frontend), Playwright (end-to-end) |

---

## Contents

1. [Install the prerequisites](#1-install-the-prerequisites)
2. [Unpack the project](#2-unpack-the-project)
3. [Create the `.env` file](#3-create-the-env-file)
4. [Start PostgreSQL](#4-start-postgresql)
5. [Set up and start the backend](#5-set-up-and-start-the-backend)
6. [Set up and start the frontend](#6-set-up-and-start-the-frontend)
7. [First use: sign in and create test users](#7-first-use-sign-in-and-create-test-users)
8. [Run all checks and tests](#8-run-all-checks-and-tests)
9. [Phase 1 verification checklist](#9-phase-1-verification-checklist)
10. [What to send back](#10-what-to-send-back)
11. [Troubleshooting](#11-troubleshooting)
12. [Reference](#12-reference)

Commands below are for **Windows PowerShell**. macOS/Linux equivalents are in
[section 12.6](#126-macos--linux-commands).

---

## Phase 2 (in progress): Organisation module

Step 1 of Phase 2 adds the `org` app: departments, employees (optionally linked 1:1 to a login),
reporting managers and each employee's first login per IST day. Leave arrives in later steps.

| Method and path (`/api/v1/`) | Who |
| --- | --- |
| `GET departments/`, `GET departments/{id}/` | Signed in |
| `POST departments/`, `PATCH departments/{id}/` (code cannot change) | Admin |
| `GET employees/` | Operations Manager (own department), HR, Admin |
| `POST employees/`, `PATCH employees/{id}/` (send `version`) | HR, Admin |
| `GET employees/{id}/`, `GET employees/{id}/logins/` | Within your scope (otherwise 404) |
| `GET employees/me/`, `GET employees/me/logins/` | Signed in (404 `no_employee_record` if none) |
| `POST employees/{id}/link-login/`, `POST employees/{id}/unlink-login/` (send `version`) | Admin |

New migrations: `org.0001_initial`, `org.0002_seed_departments` (OPS live; RM, INS, LOAN, HR),
`accounts.0003_org_role_permissions`. Apply them with `python manage.py migrate`.

---

## SLA foundation, task types and notifications

The backend calculates every SLA value; the React app only displays them.

* **Task types** (`task-templates/`): Feed Upload (fixed time 10:00, 2 h), Birthday Wishes
  (login, 2 h), SIP/STP Check (login, 3 h), Broker Mapping (assignment, 24 h, acknowledgement
  required), Reconciliation (dependency, 24 h — waits for the dependency engine), Mail Checking
  (same day until company work_end — inactive until Admin sets it), SIP Failure (event, 24 h).
  Ad-hoc tasks have no SLA ("No SLA configured").
* **Rules** are versioned and snapshotted onto each clock. Admin supersedes a rule with
  `POST sla-rules/{id}/supersede/`; running clocks keep their original deadline.
* **Clocks**: an Acknowledgement clock (2 h from assignment, restarted on reassignment) and a
  Resolution clock started by the task type's trigger. If the trigger already happened that day,
  the clock starts at the original trigger time. Blocked never pauses a clock; Cancel stops it;
  Complete stops it at the server-recorded `completed_at` (MET or MISSED).
* **SLA states**: Not started, On track, Warning (50%), Critical (75%), Overdue (100%). These are
  never workflow statuses.
* **Settings** (`sla-settings/`, Admin): company work_end and login fallback time. Both start empty.

### Run the SLA checker

Keep this running next to the backend (a second terminal, or Windows Task Scheduler):

```powershell
cd backend
.venv\Scripts\Activate.ps1
python manage.py sla_tick --loop 60
```

Every 60 seconds it records each threshold once and sends notifications:

| Threshold | Who | How |
| --- | --- | --- |
| 50% Warning | assigned employee | in-app |
| 75% Critical | assigned employee | in-app + email |
| 100% Overdue (Acknowledgement or Resolution) | assigned employee, every active HR user, the Boss recipient | in-app + email |

**Boss recipient.** There is no Boss role, and Admins are not assumed to be the Boss. The Boss is
resolved by the function in `SLA_BOSS_RESOLVER` (default: the assignee's **reporting manager**,
set on the Employees screen, who needs an active linked login). If no Boss can be resolved, nobody
is guessed: the overdue escalation still goes to the employee and HR, the gap is recorded in the
audit log as `task.sla_escalation_recipient_missing`, and `sla_tick` prints a warning. Set a
reporting manager for every employee, or replace the resolver once a dedicated escalation
mapping is approved.

Restarting it never duplicates a notification (unique `dedup_key` per clock, threshold and person).
In development, emails are printed to the console; set the `EMAIL_*` values in `.env` for SMTP.

New migrations: `tasks.0002`, `tasks.0003` (task types), `sla.0001`, `sla.0002` (rules),
`notifications.0001`, `accounts.0005` (Admin may manage SLA rules).

---

## Phase 3: Task engine

Phase 3 adds the `tasks` app and the **Tasks** screen (Received, Sent and All permitted tasks).
SLA, deadlines, recurring tasks, calendars, notifications and KPIs are later phases.

Workflow status has exactly five values: `PENDING`, `IN_PROGRESS`, `BLOCKED` (shown as "On hold"),
`COMPLETED`, `CANCELLED`. Acknowledgment and verification are separate fields, never statuses.
A rejected verification sends the **same** task back to In Progress (rework count + 1).

| Method and path (`/api/v1/`) | Who |
| --- | --- |
| `GET tasks/?view=received\|sent\|all` (+ status, priority, assignee, department, search, from, to) | Signed in (scoped) |
| `POST tasks/` | Employee (for self), Operations Manager (own department), Admin |
| `GET tasks/{id}/`, `PATCH tasks/{id}/` (send `version`; `received_at` changes need `received_at_reason`) | Scoped; edits by creator before acknowledgment, Ops Manager (department), Admin |
| `GET tasks/assignees/` | Anyone who may create tasks |
| `POST tasks/{id}/acknowledge/`, `start/`, `complete/` | The assignee only |
| `POST tasks/{id}/block/`, `unblock/` | Assignee, Ops Manager (department), Admin |
| `POST tasks/{id}/cancel/`, `reassign/` | Creator before acknowledgment, Ops Manager (department), Admin |
| `POST tasks/{id}/verify/`, `reject-verification/` | Ops Manager (department), Admin |
| `GET/POST tasks/{id}/comments/` | Read: anyone who can see the task. Write: assignee, creator, managers |
| `GET/POST tasks/{id}/attachments/`, `GET tasks/{id}/attachments/{aid}/download/` | Same as comments |

HR is read-only on tasks. `tasks.assign` (assign to someone other than yourself) exists but is
granted to no role. Every action needs the task's current `version`; a stale one returns 409.

Attachments are stored privately under `MEDIA_ROOT` (default `backend/media/`) and are only served
through the permission-checked download endpoint. Allowed: PDF, PNG, JPG, XLSX, EML, max 10 MB.

New migrations: `tasks.0001_initial`, `accounts.0004_task_role_permissions`.

---

## 1. Install the prerequisites

| Tool | Version | Check with | Get it |
| --- | --- | --- | --- |
| Python | **3.12** | `py -0` (lists installed Pythons) | python.org → Windows installer; tick "Add python.exe to PATH" |
| Node.js | **20 LTS** or newer | `node --version` | nodejs.org |
| Docker Desktop | any recent | `docker --version` | docker.com |
| Git (optional) | any | `git --version` | git-scm.com |

`py -0` must show a line with `3.12`. If it does not, install Python 3.12 before continuing.

---

## 2. Unpack the project

Unzip `cfs-ops-platform-phase1.zip`. You get a folder called `cfs-ops-platform`:

```text
cfs-ops-platform/
├── README.md              ← this file
├── .env.example           ← template for your settings
├── docker-compose.yml     ← PostgreSQL 16
├── Makefile               ← shortcuts (macOS/Linux only)
├── backend/               ← Django project
└── frontend/              ← React app
```

**Already ran an earlier version?** Unzip over the same `cfs-ops-platform` folder and allow files to
be replaced. Your `.env`, your virtual environment and your database are not in the zip, so they
are kept. The database volume is named after the folder (`cfs-ops-platform_pgdata`), so your data and
your Admin user are still there.

Open PowerShell in the `cfs-ops-platform` folder. Every step below starts from that folder unless it
says otherwise.

---

## 3. Create the `.env` file

```powershell
Copy-Item .env.example .env
notepad .env
```

Set these values and save:

| Variable | What to put |
| --- | --- |
| `DJANGO_SECRET_KEY` | Any long random text (40+ characters). Never share it. |
| `POSTGRES_PASSWORD` | A password for the local database, e.g. `LocalDb-2026-Cfs` |
| `DATABASE_URL` | `postgres://cfs_owner:<same password>@localhost:5432/cfs_ops` |
| `INITIAL_ADMIN_EMAIL` | The email the first Admin will sign in with |
| `INITIAL_ADMIN_PASSWORD` | Leave **empty** (you will be asked for it) |

The password inside `DATABASE_URL` **must be exactly the same** as `POSTGRES_PASSWORD`.

> If you already started the database once with a different password, the old password stays in
> force. Either keep using it in `.env`, or reset the database (see [11.4](#114-password-authentication-failed-for-user-cfs_owner)).

---

## 4. Start PostgreSQL

```powershell
docker compose up -d db
docker ps
```

Expected: a container `cfs-ops-platform-db-1` with status `Up ... (healthy)` and port `5432`.
If it says `starting`, wait 10 seconds and run `docker ps` again.

---

## 5. Set up and start the backend

### 5.1 Create and activate a virtual environment (first time only)

```powershell
cd backend
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
```

Your prompt now starts with `(.venv)`. If activation is blocked, see [11.2](#112-activateps1-cannot-be-loaded--running-scripts-is-disabled).

> You already have a working virtual environment in the repository root (`cfs-ops-platform\.venv`)?
> That is fine. Activate it instead with `..\.venv\Scripts\Activate.ps1` and skip `py -3.12 -m venv`.
> Use one environment consistently.

Check it is Python 3.12:

```powershell
python --version
```

Expected: `Python 3.12.x`.

### 5.2 Install the backend packages (first time, and after updates)

```powershell
python -m pip install --upgrade pip
pip install -r requirements\dev.txt
```

Expected: ends with `Successfully installed Django-5.2.x ... drf-spectacular-0.28.0 ...`.

### 5.3 Create the database tables

```powershell
python manage.py migrate
```

Expected (first time): a list ending with

```text
  Applying accounts.0001_initial... OK
  Applying audit.0001_initial... OK
  Applying accounts.0002_seed_roles... OK
  Applying audit.0002_append_only_trigger... OK
  Applying sessions.0001_initial... OK
```

If you ran it before, you see `No migrations to apply.` Both are correct.

### 5.4 Create the first Admin (first time only)

```powershell
python manage.py create_initial_admin --first-name "Your" --last-name "Name"
```

Type a password twice. It must be at least 8 characters, not common, not all numbers, and not too
similar to the email. Expected: `Admin you@example.com created.`

If you already created the Admin earlier, the command says `A user with email ... already exists.`
That is correct; skip this step.

### 5.5 Start the API server

```powershell
python manage.py runserver
```

Expected:

```text
System check identified no issues (0 silenced).
Django version 5.2.x, using settings 'config.settings.dev'
Starting development server at http://127.0.0.1:8000/
```

Leave this window open. Check in a browser:

| Address | Expected |
| --- | --- |
| http://127.0.0.1:8000/api/v1/health/ | `{"status":"ok","database":"ok"}` |
| http://127.0.0.1:8000/api/docs/ | Swagger page listing the `auth`, `users`, `roles`, `audit`, `health` sections |

---

## 6. Set up and start the frontend

Open a **second** PowerShell window in the `cfs-ops-platform` folder:

```powershell
cd frontend
npm install
npm run dev
```

`npm install` takes a minute the first time and creates `package-lock.json` (keep it).
Expected from `npm run dev`:

```text
  VITE v6.x  ready in ... ms
  ➜  Local:   http://localhost:5173/
```

Leave this window open too. The frontend forwards every `/api/...` request to the backend on
`http://127.0.0.1:8000`, so both windows must be running.

---

## 7. First use: sign in and create test users

1. Open **http://localhost:5173**. You see the **Sign in** page.
2. Sign in with your Admin email and password. You land on **Welcome, ...** with the `Admin` role.
3. The left navigation shows **Home, My profile, Users, Audit log**.
4. Open **Users → Add user** and create one user per role (use your own test emails):

   | Email (example) | Role |
   | --- | --- |
   | employee.test@example.com | Employee |
   | opsmanager.test@example.com | Operations Manager |
   | hr.test@example.com | HR |

5. Open **Audit log**. You see `user.created` rows by your Admin, plus `auth.login`. Click **Details**
   on a row to see the before/after values.
6. Click **Sign out**, then sign in as each test user and check the navigation:

   | Role | Navigation shown | Opening http://localhost:5173/admin/users |
   | --- | --- | --- |
   | Employee | Home, My profile | "You don't have access to this page" |
   | Operations Manager | Home, My profile | "You don't have access to this page" |
   | HR | Home, My profile, Audit log | "You don't have access to this page" |
   | Admin | Home, My profile, Users, Audit log | Users list |

---

## 8. Run all checks and tests

Stop nothing: keep PostgreSQL running. You can run these in a third PowerShell window.

### 8.1 Backend: format, lint, tests (window with the virtual environment active)

```powershell
cd backend
ruff check --fix .
ruff format .
ruff check .
ruff format --check .
python manage.py makemigrations --check --dry-run
python manage.py spectacular --validate --fail-on-warn --file schema.yml
pytest --cov
```

What each should show:

| Command | Expected |
| --- | --- |
| `ruff check --fix .` / `ruff format .` | Fixes layout only (import order, spacing). Run once. |
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `... files already formatted` |
| `makemigrations --check --dry-run` | `No changes detected` |
| `spectacular --validate --fail-on-warn` | No output and no error; creates `schema.yml` |
| `pytest --cov` | All tests `passed`, and a coverage table ending with `TOTAL ... 95%` or higher |

`pytest` creates a temporary `test_cfs_ops` database and deletes it afterwards; your real data is
not touched.

### 8.2 Frontend: lint, types, unit tests (in `frontend`)

```powershell
cd frontend
npm run lint
npm run typecheck
npm test
```

| Command | Expected |
| --- | --- |
| `npm run lint` | No errors |
| `npm run typecheck` | No output (no type errors) |
| `npm test` | `Test Files  5 passed`, `Tests  14 passed` (approximately) |

### 8.3 End-to-end test (backend and frontend must both be running)

In `frontend`, with your Admin credentials:

```powershell
npx playwright install chromium
$env:E2E_ADMIN_EMAIL = "you@example.com"
$env:E2E_ADMIN_PASSWORD = "your Admin password"
npx playwright test
```

Expected: `1 passed`. The test signs in as Admin, creates a new Employee user, signs out, signs in as
that Employee and checks the Employee cannot open the Users page. (It leaves one extra test user in
your local database; that is harmless.)

---

## 9. Phase 1 verification checklist

Tick each item. The "How" column says where it is checked.

| # | Item | How |
| --- | --- | --- |
| 1 | Custom email-based user model | Step 5.4 (email login); pytest `test_auth_api.py` |
| 2 | Four roles and permissions | Step 7.6 table; pytest `test_seed_and_command.py`, `test_roles_endpoint` |
| 3 | Admin user management | Step 7.4; pytest `test_users_api.py` |
| 4 | Login, logout, sessions | Step 7; pytest A1–A5, U4 (deactivation ends sessions) |
| 5 | `GET /api/v1/auth/me/` | pytest `test_me_returns_roles_and_permissions` |
| 6 | `POST /api/v1/auth/password-change/` | My profile → Change password; pytest A6, A7 |
| 7 | `/api/v1/users/` | Users screen; pytest `test_users_api.py` |
| 8 | `/api/v1/roles/` | Swagger → roles; pytest `test_roles_endpoint` |
| 9 | `/api/v1/audit-log/` | Audit log screen; pytest `apps/audit/tests/test_api.py` |
| 10 | Append-only audit trigger | pytest `test_append_only.py` (UPDATE, DELETE, TRUNCATE refused) |
| 11 | React + TypeScript + MUI | Steps 6–7; `npm run typecheck` |
| 12 | Admin user-management screen | Step 7.4 |
| 13 | Admin/HR audit-log screen | Step 7.5 (as Admin and as HR) |
| 14 | Protected routes and 403 | Step 7.6 table; `npm test` (ProtectedRoute, navigation) |
| 15 | OpenAPI / Swagger | Step 5.5 docs link; `spectacular --validate` |
| 16 | Backend tests | `pytest --cov` all passed, coverage ≥ 95% |
| 17 | Frontend tests | `npm test` all passed |
| 18 | End-to-end test | `npx playwright test` → 1 passed |
| 19 | API error format | Every error is `{"code", "message", "fields"}`; pytest `test_e1_error_shape` |
| 20 | README/setup consistency | You followed this file end to end without changes |

---

## 10. What to send back

Copy and paste the **complete output** of these, in this order:

1. `ruff check .` and `ruff format --check .`
2. `pytest --cov` (including the coverage table at the end)
3. `npm run lint`, `npm run typecheck`, `npm test`
4. `npx playwright test`
5. Any step in sections 4–7 that did not behave as described, with the error text

With those, Phase 1 can be confirmed or the exact remaining fix identified.

---

## 11. Troubleshooting

### 11.1 `No suitable Python runtime found` (from `py -3.12`)

Python 3.12 is not installed or not registered. Run `py -0` to see what is installed. Install
Python 3.12 from python.org, or create the environment with the full path, e.g.
`C:\Users\<you>\AppData\Local\Programs\Python\Python312\python.exe -m venv .venv`.

### 11.2 `Activate.ps1 cannot be loaded ... running scripts is disabled`

Allow local scripts for your user once, then activate again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.venv\Scripts\Activate.ps1
```

`.venv\Scripts\activate` without `.ps1` also fails in PowerShell; always use `Activate.ps1`.

### 11.3 `CommandError: ... This password is too short / too common / entirely numeric`

`create_initial_admin` enforces the password rules. Run it again with a stronger password (e.g. three
words and a number).

### 11.4 `password authentication failed for user "cfs_owner"`

The password in `DATABASE_URL` does not match the one the database was first created with. Either put
the original password in `.env`, or reset the local database (**deletes all local data**):

```powershell
docker compose down -v
docker compose up -d db
cd backend
python manage.py migrate
python manage.py create_initial_admin
```

### 11.5 Port 5432 already in use

Another PostgreSQL is running on your PC. Stop it (Services → postgresql → Stop), or change the
left-hand port in `docker-compose.yml` to `"5433:5432"` and use `localhost:5433` in `DATABASE_URL`.

### 11.6 Frontend shows errors, or the console says `ECONNREFUSED 127.0.0.1:8000`

The backend is not running. Start it (step 5.5) and reload the page.

### 11.7 `Security token missing or invalid. Reload the page.` (403 `csrf_failed`)

Open the app through **http://localhost:5173**, not `http://127.0.0.1:5173`, because
`CSRF_TRUSTED_ORIGINS` in `.env` lists `http://localhost:5173`. Then reload the page.

### 11.8 `pytest` cannot find settings or the database

Run `pytest` from inside the `backend` folder with the virtual environment active and Docker running.
It reads `DATABASE_URL` from `.env`.

### 11.9 `manage.py flush` fails with `audit_log is append-only`

This is intended: the audit log cannot be emptied. Use the reset in 11.4 for a clean local database.

### 11.10 Playwright: `Executable doesn't exist`

Run `npx playwright install chromium` once, then `npx playwright test` again.

### 11.11 The test is skipped: `Set E2E_ADMIN_PASSWORD to run against a live stack`

Set `$env:E2E_ADMIN_EMAIL` and `$env:E2E_ADMIN_PASSWORD` in the same PowerShell window before
running `npx playwright test`.

---

## 12. Reference

### 12.1 Environment variables (`.env`)

| Variable | Purpose |
| --- | --- |
| `DJANGO_SETTINGS_MODULE` | `config.settings.dev` locally; `config.settings.prod` in production |
| `DJANGO_SECRET_KEY` | Signing key; different in every environment |
| `DJANGO_DEBUG` | `true` locally, `false` in production |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` locally |
| `POSTGRES_PASSWORD` | Password of the Docker database |
| `DATABASE_URL` | Connection string used by Django and the tests |
| `CSRF_TRUSTED_ORIGINS` | `http://localhost:5173` locally |
| `INITIAL_ADMIN_EMAIL` | Email for `create_initial_admin` |
| `INITIAL_ADMIN_PASSWORD` | Optional, for CI only; leave empty locally |

The time zone is fixed in code: timestamps are stored in UTC and shown in IST (Asia/Kolkata).

### 12.2 API (all under `/api/v1/`)

| Method and path | Who may call it |
| --- | --- |
| `GET auth/csrf/`, `POST auth/login/`, `GET health/` | Anyone |
| `GET auth/me/`, `POST auth/logout/`, `POST auth/password-change/` | Signed-in users |
| `GET users/`, `POST users/`, `GET users/{id}/`, `PATCH users/{id}/`, `POST users/{id}/set-password/`, `GET roles/` | Admin |
| `GET audit-log/` | Admin, HR |

Users are never deleted (no `DELETE`), and the audit log has no write methods. Every error has the
shape `{"code": "...", "message": "...", "fields": {...}}`. Interactive docs: `/api/docs/`;
schema: `/api/schema/`.

### 12.3 Frontend routes

| Route | Who |
| --- | --- |
| `/login` | Anyone |
| `/` (Home), `/profile` | Signed in |
| `/admin/users` | Admin |
| `/admin/audit` | Admin, HR |
| `/403` | Shown when a role lacks access |
| any other path | "Page not found" |

### 12.4 Database

Tables created by Phase 1: `accounts_user` (email login), `audit_log` (append-only), plus Django's
`auth_group` (the four roles), `auth_permission`, `accounts_user_groups`, `django_session`.

| Task | Command (in `backend`, environment active) |
| --- | --- |
| Apply migrations | `python manage.py migrate` |
| Show migration status | `python manage.py showmigrations` |
| After changing a model | `python manage.py makemigrations` (review the file before committing) |
| Check nothing is missing | `python manage.py makemigrations --check --dry-run` |

### 12.5 Code style

After editing Python files run `ruff check --fix .` and `ruff format .` in `backend`. CI runs
`ruff check .`, `ruff format --check .`, the tests and the schema check on every push
(`.github/workflows/ci.yml`).

### 12.6 macOS / Linux commands

```bash
cp .env.example .env                      # then edit it
docker compose up -d db
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements/dev.txt
python manage.py migrate
python manage.py create_initial_admin
python manage.py runserver
# second terminal
cd frontend && npm install && npm run dev
```

The `Makefile` offers shortcuts: `make lint`, `make test-backend`, `make test-frontend`, `make e2e`,
`make schema`.

### 12.7 Production notes

Run with `DJANGO_SETTINGS_MODULE=config.settings.prod` behind HTTPS (gunicorn + Nginx) with
PostgreSQL 16 in an Indian region and daily backups. Give the application's database user only
`SELECT` and `INSERT` on `audit_log`; a separate owner account runs migrations. API docs are limited
to Admins in production. Full security hardening is Phase 13.
