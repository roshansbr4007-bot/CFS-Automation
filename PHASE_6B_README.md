# Phase 6B — Command Center operational monitoring

> **Status: implemented, not executed.** The sandbox could not run pytest (not installed) or vitest (Windows-only Rollup binary). TypeScript and ESLint pass with 0 errors; backend files compile and pass static checks. Run the commands in "Tests" before treating Phase 6B as complete.

## Purpose

Read-only visibility for Admin (the Boss): today's people, daily activities, assigned tasks, SLA attention and system health, with a per-employee drill-down. No write actions, no new rules, no scores or rankings.

## Architecture

- **Additive over Phase 6A.** `apps/command_center/services.py` gains a row collector with server-side filters, `overview()`, `attention()`, `employee_detail()` and `sla_attention()`. The Phase 6A `summary()` uses the same collector; **without filters its output is unchanged**, plus the additive `overview` block and per-employee `attention`.
- **The single source of truth stays the same.** Every row comes from the existing `tasks/monitoring.py` building blocks (`daily_activity_tasks`, `assigned_workload_tasks`, `activity_row`, `assigned_row`), which read the existing SLA engine (`sla.task_sla`). Nothing recalculates SLA, overdue or completion.
- **One additive field** in the shared row: `assigned_row` now includes `remaining_seconds`, taken from the SLA engine as `activity_row` already does.
- **Scheduled and manual work never mix.**
  - Daily activities are `source = SCHEDULED` tasks with an occurrence date.
  - Assigned tasks are `source = MANUAL` tasks.
  - Every row in the API carries `source`.
- **Queries:** one per row kind, with related data and SLA clocks prefetched. A test asserts the query count is the same for 1 and 9 employees.
- No migrations, no new permission, no audit of reads, no WebSockets.

## API endpoints (all GET, Admin only)

| Endpoint | Status | Purpose |
| --- | --- | --- |
| `/api/v1/command-center/summary/` | extended | Phase 6A summary plus `overview` (employees, daily activities, assigned tasks, SLA states of open work) and `employees[].attention`. Accepts every filter below. |
| `/api/v1/command-center/employees/{id}/` | new | One employee's `daily_activities` and `assigned_tasks`, kept apart. Accepts `date`, `source`, `status`, `sla_state`. 404 if the employee does not exist. |
| `/api/v1/command-center/sla-attention/` | new | Open work grouped as `critical`, `warning`, `overdue` (existing SLA states). `on_track` / `not_started` are filled only when requested with `sla_state`. |
| `/api/v1/command-center/health/` | unchanged | Live API / database / Redis / Celery checks (Phase 6A). |

**`attention`** lists the factual states of the employee's open work, most serious first:
- `OVERDUE`, `CRITICAL`, `WARNING` and `BLOCKED`, as they apply;
- `ON_TRACK` when there is open work with none of these;
- `NO_ACTIVE_WORK` when nothing is open.

It is not a score or a ranking.

A separate `/command-center/employees/` list was **not** added: the summary already returns the employee list, and a second endpoint would duplicate it.

## Filters (server-side, validated; invalid values return 400)

| Parameter | Values | Applies to |
| --- | --- | --- |
| `date` | YYYY-MM-DD; default = today's business date (IST, from the server) | rows |
| `department` | department id; selects employees, as the Operations Monitor does | employees |
| `employee` | employee id | employees |
| `source` | `SCHEDULED` or `MANUAL` | rows |
| `status` | existing task statuses | rows |
| `sla_state` | existing SLA states | rows |

## Frontend behaviour (`/admin/command-center`)

- **Filters:** date (defaults to the server's business date), department, employee, source, status, SLA state, and Clear filters.
- **Today overview:** active employees, employees with work, daily activities, active assigned tasks, overdue work, critical SLA.
- **Today's operations:**
  - **Daily activity monitor:** total, completed, pending, overdue, completed late, in progress, blocked.
  - **Assigned task monitor:** the same, plus active.
- **SLA attention:** Critical / Warning / Overdue tables showing employee, work, source, deadline, SLA state and remaining time.
- **Employees:**
  - the Phase 6A table plus a **Current attention** column;
  - **View** opens a read-only drill-down with separate Daily activities and Assigned tasks tables.
- **Unchanged Phase 6A sections:** system health (live), scheduler health, SLA snapshot, recent events, quick actions.
- **States:** each section has its own loading message, error with **Retry**, and empty message. No zero values are shown while data is loading.
- **Refresh:** summary and SLA attention poll every 60 s. Health stays manual (Refresh), as in Phase 6A.

## Authorization

The existing Admin-only `tasks.manage_all_tasks` (Operations Monitor permission class):
- anonymous → 401;
- Employee, HR and Operations Manager → 403;
- Admin → 200.

No permission or role was changed.

## Known limitations and conflicts (existing rules preserved, not changed)

1. **Overdue for assigned tasks** uses the approved Phase 5.1 definition: open, with a deadline before the end of the date (or now, for today). That matches the SLA engine's OVERDUE state for today's open work, but can differ for past dates. The SLA attention panel and the SLA counts use the SLA state itself.
2. **Cancelled** tasks are excluded by the existing workload definition, so no "cancelled" count is shown. Counting them would need a new "cancelled on this date" rule.
3. **The summary does not run live health checks** (approved Phase 6A decision: keep the summary fast). The System health section uses `/health/`.
4. The department filter selects employees by their department, as the Operations Monitor does, not by the task's department.

## Tests

**Backend** (`apps/command_center/tests/test_phase6b.py`, 24 cases):
- **Access:** Admin only, for all three endpoints.
- **Counts and separation:** overview counts with scheduled and manual work kept apart; attention per employee.
- **Filters:** source, status, SLA state, date, department and employee; an empty result; 400 for invalid filters.
- **Drill-down:** each employee's own work by source, the filters, and 404.
- **SLA attention:** groups and their order; on-track work only on request; the filters.
- **Read-only and performance:** nothing changes after reading; constant query count.

The permission matrix gains 2 endpoints × 5 callers.

**Frontend** (`CommandCenterPage.test.tsx`): the 6 Phase 6A tests keep their assertions; their mocks gain the new fields and endpoints. The 5 new tests cover:
- overview, SLA groups and attention;
- server-side filters;
- the read-only drill-down;
- empty state plus retryable error;
- 60-second polling.

**Expected totals:** backend **824** (790 + 34), frontend **67** (62 + 5).

```powershell
cd backend
python manage.py makemigrations --check --dry-run   # expect: No changes detected
pytest apps/command_center -q
pytest -q
pytest --cov
cd ..\frontend
npx tsc --noEmit
npx eslint src
npx vitest run --reporter=verbose --testTimeout=30000
```

## Manual verification

1. Sign in as Admin, open **Command center**, and check that the date shows today's business date.
2. Compare **Today overview** with the Operations Monitor for the same day: the daily and assigned totals should match.
3. Set **Source = Assigned tasks**: daily-activity counts drop to 0, and nothing scheduled appears in SLA attention.
4. Press **View** on an employee: the two tables are separate, and there are no edit buttons.
5. Sign in as HR or an Operations Manager and open `/admin/command-center`: you should see "You don't have access to this page".
