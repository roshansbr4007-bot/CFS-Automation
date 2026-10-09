# Phase 5 — Responsibilities, recurring work and automatic task generation

Merge this section into the project README.

## What runs locally

| Process | Purpose |
| --- | --- |
| PostgreSQL (Docker) | database |
| Redis (Docker) | Celery message broker |
| Django (`runserver`) | API |
| Celery worker | runs scheduled jobs |
| **Celery Beat (exactly one)** | the one scheduler: every minute it queues the recurring generator and the SLA checker |
| React (`npm run dev`) | UI |

`python manage.py sla_tick --loop 60` is no longer needed: Celery Beat runs the same SLA pass every minute. `sla_tick --once` and `generate_recurring_tasks` remain for manual one-off runs.

## First-time setup (Windows PowerShell)

```powershell
docker compose up -d db redis
cd backend
.venv\Scripts\Activate.ps1
pip install -r requirements\dev.txt      # adds celery and redis
python manage.py migrate
python manage.py schema_audit              # every table must match the models
```

Optional `.env` setting (this is the default):

```
CELERY_BROKER_URL=redis://localhost:6379/0
```

## Daily development: five terminals

```powershell
# 1. API
cd backend; .venv\Scripts\Activate.ps1; python manage.py runserver 8000
# 2. Celery worker (Windows needs the solo pool)
cd backend; .venv\Scripts\Activate.ps1; celery -A config worker -l info --pool=solo
# 3. Celery Beat (ONE only)
cd backend; .venv\Scripts\Activate.ps1; celery -A config beat -l info
# 4. Frontend
cd frontend; npm run dev
# 5. Manual one-off runs when needed
python manage.py generate_recurring_tasks
python manage.py sla_tick --once
```

## Production

- Run the worker(s) and **exactly one** `celery -A config beat` process (one service or container). Two Beat processes would queue every job twice; generation would still not duplicate (the occurrence ledger is unique per schedule and business date), but run only one.
- Celery's timezone is `Asia/Kolkata`; timestamps are still stored in UTC.

## How generation works

1. Every minute Beat queues `recurring.generate_recurring_tasks`.
2. For each active schedule of an active responsibility, the generator asks the Company Calendar whether the business date is a working day. A monthly date that falls on a non-working day moves to the next working day.
3. Once the schedule's time (10:00 IST) has passed, it writes the occurrence ledger row and the task in one transaction. The ledger is unique per schedule and business date, so repeated or concurrent runs never duplicate.
4. The assignee is the responsibility's owner **on that date** (ownership history). The creator is the system identity **CFS Scheduler**, which cannot sign in.
5. The existing SLA engine starts the clock from the task type (Feed Upload and Mail Checking: 10:00→12:00; SIP/STP/Switch: 10:00→13:00; Brokerage Calculation: no SLA).
6. No owner: the occurrence is **SKIPPED**, audited, and an in-app warning goes to the Admins and the department's Operations Managers.
7. Recovery rules:
   - **Late daily run:** today's task is still created once, with its SLA counted from 10:00.
   - **Fully missed earlier days:** recorded as **MISSED**, not created.
   - **Missed monthly occurrence:** created when the scheduler resumes.
8. A scheduled task that is deleted is never created again (the ledger keeps the occurrence).

## First configuration after migrating

1. **Responsibilities → Change owner:** give Feed Upload, Mail Checking, SIP/STP/Switch Checking and Brokerage Calculation an owner, starting today or later. Until then, occurrences are SKIPPED and reported.
2. **Company calendar:** add this year's holidays and any special working days.
