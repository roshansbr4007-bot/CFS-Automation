# Phase 5.1 — Daily Activities, Employee Countdown, Admin (Boss) Monitoring

> **Status: implemented, NOT fully verified.** Backend tests and the frontend tests and build were not executed in the sandbox (see section 13). Run section 12 on Windows before treating Phase 5.1 as complete.

## 1. Purpose

Present the scheduler's daily responsibility work clearly to employees (with start, deadline, SLA state and a live countdown), keep it separate from manually assigned tasks, and give Admin (the Boss) an employee-wise monitoring view of both.

## 2. Features implemented

- **Employee "Daily / scheduled responsibilities" section** (Tasks → Received). Columns: activity, start, deadline, status, SLA state, remaining time (live countdown), completed at with an On time / Late result, and Open. Today's activities plus earlier daily activities that are still open. Refreshes from the backend every 60 s.
- **"Assigned tasks" section:** unchanged; manual tasks only.
- **Completion:** uses the existing task completion. The original scheduled start and deadline are never changed. On time / Late is the SLA engine's recorded outcome (MET / MISSED). An activity without an SLA (Brokerage Calculation) shows its schedule time and "No deadline".
- **Admin Operations monitor:**
  - per-employee summary, with daily activities and assigned tasks kept separate;
  - date choice: Today / Yesterday / Custom;
  - department and employee filters;
  - drill-down per employee, with its own filters.

## 3. Backend APIs added

No existing API changed.

| Method and path | Who | Purpose |
| --- | --- | --- |
| `GET /api/v1/tasks/daily-activities/?date=` | any signed-in user | My own generated daily activities (default: today IST) |
| `GET /api/v1/operations/daily-summary/?date=&department=&employee=` | Admin only | Employee-wise counts: `daily_activity` and `assigned_tasks`, each with total / completed / pending / overdue / completed_late |
| `GET /api/v1/operations/employees/{id}/daily-activities/?date=&status=&sla_state=` | Admin only | One employee's daily activities. `status`: pending, completed, overdue. `sla_state`: NOT_STARTED, ON_TRACK, WARNING, CRITICAL, OVERDUE |
| `GET /api/v1/operations/employees/{id}/assigned-tasks/?date=&status=` | Admin only | One employee's manual-task workload for the date |

### Definitions (`backend/apps/tasks/monitoring.py`)

- **Daily activities of date D:** scheduled tasks whose occurrence date is D. Cancelled ones are excluded.
- **Assigned workload of date D:** manual tasks assigned on or before D, not completed before D, not cancelled.
  - *Completed* = completed on D.
  - *Pending* = not completed by the end of D.
  - *Overdue* = pending and its SLA deadline has passed.
- **No deadline:** manual ad-hoc tasks without an SLA have no deadline, so they are never overdue.

## 4. Frontend pages and components added

- **New:** `components/Countdown.tsx`, `features/tasks/DailyActivities.tsx`, `features/operations/OperationsMonitorPage.tsx` (route `/admin/operations`, menu "Operations monitor").
- **Changed:** `TasksPage.tsx` (the daily section now uses `DailyActivities`); `SlaBadge.tsx` (adds `SlaStateChip`); `DateTimeText.tsx` (adds `formatTimeIST`); types, endpoints, routes, navigation; tests.

## 5. Files changed

23 files: 8 new, 15 modified. See `CHANGED_FILES.txt`.

## 6. Migration status

**No migrations.** All migration folders are identical to Phase 5.

## 7. Permission behaviour

- **Monitoring:** the endpoints and page require the existing Admin-only permission `tasks.manage_all_tasks`.
- **Other roles:** HR, Operations Manager and Employee get 403; anonymous users get 401.
- **No changes:** no permission or role grant changed (`accounts/roles.py` untouched).
- **Daily activities:** `tasks/daily-activities/` returns only the caller's own activities.

## 8. SLA behaviour

- **Unchanged:** the SLA engine, thresholds (50 / 75 / 100%) and rules (`sla/services.py` untouched).
- **Source of truth:** start, deadline, state, outcome and `remaining_seconds` come from the existing engine. `remaining_seconds` is measured at response time (negative = overdue).
- **Countdown:** the frontend counts down from that value, measuring only the time since the response, so the browser's clock and timezone never decide a deadline. At zero it shows "00m remaining" and Overdue; the next refresh shows the backend state.
- **Example — Feed Upload, 10:00 → 12:00:**

  | Time | Shown |
  | --- | --- |
  | 10:50 | On track, 1h 10m left |
  | 11:00 | Warning, 1h 00m left |
  | 11:30 | Critical, 30m left |
  | after 12:00 | Overdue |

  Completed at 11:35 shows "Completed on time"; completed at 12:20 shows "Completed late".

## 9. Admin = Boss monitoring

- **Monitoring only.** Admin acts as Boss **for monitoring and the dashboard only**.
- **Escalation unchanged:**
  - overdue SLA escalation recipients are exactly as before (assigned employee + active HR + the Boss resolved by `SLA_BOSS_RESOLVER`, i.e. the assignee's reporting manager);
  - Admin is **not** added to escalation notifications or emails;
  - `notifications/services.py`, `sla/services.py` and settings are untouched.

## 10. Login does NOT create scheduled activities

Login only reads activities that already exist. `org/services.py` (login recording) is untouched, and the new monitoring code contains no create / save / update / delete calls.

## 11. The Celery generator remains responsible

Scheduled activities are created only by the recurring generator (`recurring/generator.py`, run every minute by Celery Beat via `recurring/tasks.py`). Both files are unchanged in Phase 5.1.

## 12. Local Windows verification

Copy the `backend` and `frontend` folders from this ZIP over your project, keeping the paths, then run:

```powershell
cd backend
python manage.py makemigrations --check --dry-run
pytest apps/recurring/tests/test_daily_activities.py -q
pytest -q
pytest --cov

cd ..\frontend
npm run typecheck
npm test
npm run build
```

Expected: no migration changes, every test passing, and coverage at or above the Phase 5 level (98%+).

## 13. Sandbox verification limitations (exact)

| Command | Result in the sandbox |
| --- | --- |
| `python manage.py makemigrations --check --dry-run` | **NOT executed.** `ModuleNotFoundError: No module named 'django'` |
| `pytest …` (all three) | **NOT executed.** `/bin/sh: 1: pytest: not found` |
| `npm run typecheck` | **PASSED** (`tsc --noEmit`, exit 0, no errors) |
| `npm test` | **NOT executed.** `sh: 1: vitest: Permission denied`; started via node: `Cannot find module '@rollup/rollup-linux-x64-gnu'` |
| `npm run build` | **NOT executed.** `sh: 1: vite: Permission denied` (same Rollup cause) |

**Causes:**
- **Backend:** the sandbox has no Django, pytest, Celery or PostgreSQL, and no network to install them.
- **Frontend:** the uploaded `node_modules` comes from Windows, so its launcher scripts lack execute permission and it contains only Rollup's Windows native binary.

**Checked without executing:**
- **Backend:**
  - all Python files compile;
  - no undefined names, unused imports, import-order or line-length problems;
  - no OpenAPI component collisions.
- **Frontend:** typecheck and ESLint pass (0 errors).
- **Code inspection:**
  - no migration changes;
  - the escalation, permission, login and generator files are untouched;
  - existing backend files received only additions (one changed import line in `tasks/api/views.py`).

**Backend tests were NOT executed. Frontend tests and build were NOT executed. Phase 5.1 is not fully verified.**
