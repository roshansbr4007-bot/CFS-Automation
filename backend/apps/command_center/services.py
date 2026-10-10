"""Admin Command Center (Phase 6A): a read-only observation layer.

Every task / activity / SLA number comes from the existing monitoring building blocks
(apps.tasks.monitoring), which read the existing SLA engine; nothing here recalculates a deadline
or a state. Scheduler status comes from SchedulerHeartbeat; live infrastructure checks are kept in
health() so the summary stays fast. Statuses are honest: HEALTHY, FAILED, NEVER_RUN or UNKNOWN.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db import connection
from django.db.models import Count
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.core.timeutils import to_ist
from apps.org.models import Employee
from apps.recurring.models import ScheduleOccurrence
from apps.tasks import monitoring

from .heartbeat import JOBS
from .models import SchedulerHeartbeat

HEALTHY, FAILED, NEVER_RUN, UNKNOWN = "HEALTHY", "FAILED", "NEVER_RUN", "UNKNOWN"
# Beat runs each job every 60 s; a last success older than this is no longer evidence of health.
STALE_AFTER = timedelta(minutes=5)
RECENT_EVENTS = 20
PING_TIMEOUT_SECONDS = 1.0
SLA_OPEN_STATES = ("NOT_STARTED", "ON_TRACK", "WARNING", "CRITICAL", "OVERDUE")


# --- scheduler status (from heartbeats; no live calls) ----------------------------------------


def job_status(beat: SchedulerHeartbeat | None, now: datetime) -> tuple[str, str]:
    """HEALTHY only with fresh evidence of success; FAILED when the last run failed."""
    if beat is None or beat.last_started_at is None:
        return NEVER_RUN, "No run has been recorded yet."
    if beat.last_status == SchedulerHeartbeat.STATUS_FAILED:
        return FAILED, beat.last_error or "The last run failed."
    if beat.last_success_at is None:
        return UNKNOWN, "Started, but no successful run has been recorded."
    if now - beat.last_success_at > STALE_AFTER:
        minutes = int((now - beat.last_success_at).total_seconds() // 60)
        return UNKNOWN, f"Last successful run was {minutes} minutes ago."
    return HEALTHY, "Running on schedule."


def scheduler(now: datetime) -> list[dict]:
    beats = {b.job: b for b in SchedulerHeartbeat.objects.filter(job__in=JOBS)}
    rows = []
    for job, name in JOBS.items():
        beat = beats.get(job)
        status, detail = job_status(beat, now)
        rows.append({
            "job": job,
            "name": name,
            "status": status,
            "detail": detail,
            "last_started_at": beat.last_started_at if beat else None,
            "last_finished_at": beat.last_finished_at if beat else None,
            "last_success_at": beat.last_success_at if beat else None,
            "last_summary": beat.last_summary if beat else {},
        })
    return rows


def todays_occurrences(day) -> dict:
    counts = dict(
        ScheduleOccurrence.objects.filter(occurrence_date=day)
        .values_list("status")
        .annotate(n=Count("id"))
    )
    return {key.lower(): counts.get(key, 0) for key in ("GENERATED", "SKIPPED", "MISSED", "FAILED")}


# --- operations / SLA snapshot (existing monitoring rows) --------------------------------------


def _sla_counts(rows, *, completed_key) -> dict:
    counts = {state.lower(): 0 for state in SLA_OPEN_STATES}
    counts.update({"completed_on_time": 0, "completed_late": 0})
    for row in rows:
        if row["status"] in monitoring.OPEN and row["sla_state"] in SLA_OPEN_STATES:
            counts[row["sla_state"].lower()] += 1
        elif completed_key(row) and row["completion_result"] == "ON_TIME":
            counts["completed_on_time"] += 1
        elif completed_key(row) and row["completion_result"] == "LATE":
            counts["completed_late"] += 1
    return counts


def recent_events() -> list[dict]:
    """Metadata only: never old / new values, IP, request id or context."""
    rows = AuditLog.objects.select_related("actor_user").order_by("-occurred_at", "-id")
    return [
        {
            "id": e.id,
            "action": e.action,
            "entity_type": e.entity_type,
            "entity_id": e.entity_id,
            "actor": e.actor_user,
            "occurred_at": e.occurred_at,
        }
        for e in rows[:RECENT_EVENTS]
    ]


# --- Phase 6B: filters, row collection, overview, attention ----------------------------------

SCHEDULED, MANUAL = "SCHEDULED", "MANUAL"
SOURCES = (SCHEDULED, MANUAL)
ATTENTION_SLA_STATES = ("CRITICAL", "WARNING", "OVERDUE")
OVERVIEW_SLA_STATES = ("ON_TRACK", "WARNING", "CRITICAL", "OVERDUE")


@dataclass(frozen=True)
class Filters:
    """Server-side filters. Department and employee select employees (as the Operations
    Monitor does); source, status and SLA state select rows. All values come from existing
    models and states."""

    day: date
    department_id: int | None = None
    employee_id: int | None = None
    source: str | None = None
    status: str | None = None
    sla_state: str | None = None


def _keep(row: dict, filters: Filters) -> bool:
    if filters.status and row["status"] != filters.status:
        return False
    return not filters.sla_state or row["sla_state"] == filters.sla_state


def _rows_for(employees, filters: Filters, now: datetime) -> tuple[dict, dict]:
    """Existing monitoring rows (one query per kind, related data and SLA clocks prefetched),
    each tagged with its source. Scheduled and manual work are never mixed."""
    daily, assigned = {}, {}
    if filters.source in (None, SCHEDULED):
        tasks = list(monitoring.daily_activity_tasks(employees, filters.day))
        for task, row in zip(tasks, monitoring.activity_rows(tasks, now), strict=True):
            row = {**row, "source": SCHEDULED}
            if _keep(row, filters):
                daily.setdefault(task.assigned_to_id, []).append(row)
    if filters.source in (None, MANUAL):
        for task in monitoring.assigned_workload_tasks(employees, filters.day):
            row = {**monitoring.assigned_row(task, filters.day, now), "source": MANUAL}
            if _keep(row, filters):
                assigned.setdefault(task.assigned_to_id, []).append(row)
    return daily, assigned


def _employees(filters: Filters) -> list:
    qs = Employee.objects.filter(is_active=True).select_related("department")
    if filters.department_id is not None:
        qs = qs.filter(department_id=filters.department_id)
    if filters.employee_id is not None:
        qs = qs.filter(pk=filters.employee_id)
    return list(qs.order_by("full_name", "id"))


def attention(rows) -> list[str]:
    """Factual operational states of an employee's open work (not a score or a ranking):
    OVERDUE, CRITICAL, WARNING, BLOCKED; ON_TRACK when open work has none of these;
    NO_ACTIVE_WORK when nothing is open."""
    open_rows = [r for r in rows if r["status"] in monitoring.OPEN]
    if not open_rows:
        return ["NO_ACTIVE_WORK"]
    flags = []
    if any(r["sla_state"] == "OVERDUE" or r["is_overdue"] for r in open_rows):
        flags.append("OVERDUE")
    for state in ("CRITICAL", "WARNING"):
        if any(r["sla_state"] == state for r in open_rows):
            flags.append(state)
    if any(r["status"] == "BLOCKED" for r in open_rows):
        flags.append("BLOCKED")
    return flags or ["ON_TRACK"]


def _by_status(rows, status: str) -> int:
    return sum(r["status"] == status for r in rows)


def overview(employees, daily: dict, assigned: dict) -> dict:
    all_daily = [r for rows in daily.values() for r in rows]
    all_assigned = [r for rows in assigned.values() for r in rows]
    open_rows = [r for r in all_daily + all_assigned if r["status"] in monitoring.OPEN]
    return {
        "employees": {
            "total_active": len(employees),
            "with_work": sum(bool(daily.get(e.pk) or assigned.get(e.pk)) for e in employees),
        },
        "daily_activities": {
            "scheduled": len(all_daily),
            "completed": _by_status(all_daily, "COMPLETED"),
            "pending": _by_status(all_daily, "PENDING"),
            "in_progress": _by_status(all_daily, "IN_PROGRESS"),
            "blocked": _by_status(all_daily, "BLOCKED"),
            "overdue": sum(r["is_overdue"] for r in all_daily),
        },
        "assigned_tasks": {
            "total": len(all_assigned),
            "active": sum(r["status"] in monitoring.OPEN for r in all_assigned),
            "completed": sum(r["completed_on_day"] for r in all_assigned),
            "pending": _by_status(all_assigned, "PENDING"),
            "in_progress": _by_status(all_assigned, "IN_PROGRESS"),
            "blocked": _by_status(all_assigned, "BLOCKED"),
            "overdue": sum(r["is_overdue"] for r in all_assigned),
        },
        "sla": {
            state.lower(): sum(r["sla_state"] == state for r in open_rows)
            for state in OVERVIEW_SLA_STATES
        },
    }


def summary(now: datetime | None = None, filters: Filters | None = None) -> dict:
    """Without filters this is exactly the Phase 6A summary (today, every active employee),
    plus the additive Phase 6B `overview` and per-employee `attention`."""
    now = now or timezone.now()
    filters = filters or Filters(day=to_ist(now).date())
    employees = _employees(filters)
    daily, assigned = _rows_for(employees, filters, now)
    all_daily = [row for rows in daily.values() for row in rows]
    all_assigned = [row for rows in assigned.values() for row in rows]
    return {
        "date": filters.day,
        "server_time": now,
        "scheduler": scheduler(now),
        "todays_occurrences": todays_occurrences(filters.day),
        "operations": {
            "daily_activity": monitoring.daily_counts(all_daily),
            "assigned_tasks": monitoring.assigned_counts(all_assigned),
        },
        "sla": {
            "daily_activity": _sla_counts(
                all_daily, completed_key=lambda r: r["status"] == "COMPLETED"
            ),
            "assigned_tasks": _sla_counts(
                all_assigned, completed_key=lambda r: r["completed_on_day"]
            ),
        },
        "overview": overview(employees, daily, assigned),
        "employees": [
            {
                "employee": employee,
                "daily_activity": monitoring.daily_counts(daily.get(employee.pk, [])),
                "assigned_tasks": monitoring.assigned_counts(assigned.get(employee.pk, [])),
                "attention": attention(daily.get(employee.pk, []) + assigned.get(employee.pk, [])),
            }
            for employee in employees
        ],
        "recent_events": recent_events(),
    }


def employee_detail(employee, filters: Filters, now: datetime | None = None) -> dict:
    """Read-only drill-down: one employee's daily activities and assigned tasks, kept apart."""
    now = now or timezone.now()
    daily, assigned = _rows_for([employee], filters, now)
    daily_rows, assigned_rows = daily.get(employee.pk, []), assigned.get(employee.pk, [])
    return {
        "date": filters.day,
        "server_time": now,
        "employee": employee,
        "attention": attention(daily_rows + assigned_rows),
        "daily_activities": daily_rows,
        "assigned_tasks": assigned_rows,
    }


def sla_attention(filters: Filters, now: datetime | None = None) -> dict:
    """Open work whose existing SLA state needs attention, grouped by that state. ON_TRACK /
    NOT_STARTED items appear only when explicitly requested through the sla_state filter."""
    now = now or timezone.now()
    states = (filters.sla_state,) if filters.sla_state else ATTENTION_SLA_STATES
    row_filters = Filters(**{**filters.__dict__, "sla_state": None})
    employees = _employees(row_filters)
    daily, assigned = _rows_for(employees, row_filters, now)
    groups = {state.lower(): [] for state in (*ATTENTION_SLA_STATES, "ON_TRACK", "NOT_STARTED")}
    for employee in employees:
        for row in daily.get(employee.pk, []) + assigned.get(employee.pk, []):
            if row["status"] in monitoring.OPEN and row["sla_state"] in states:
                groups[row["sla_state"].lower()].append({**row, "employee": employee})
    for items in groups.values():
        items.sort(key=lambda r: (r["deadline"] is None, r["deadline"] or now, r["task_id"]))
    return {"date": filters.day, "server_time": now, **groups}


# --- live health checks (kept out of the summary) ----------------------------------------------


def _check(name: str, probe) -> dict:
    """Runs one probe; the probe returns (status, detail). Any exception -> UNKNOWN."""
    try:
        status, detail = probe()
    except Exception as error:  # an unreachable dependency is reported, never raised
        status, detail = UNKNOWN, f"Could not check: {type(error).__name__}"
    return {"name": name, "status": status, "detail": detail}


def _database():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as error:
        return FAILED, f"Not reachable: {type(error).__name__}"
    return HEALTHY, "Connected."


def redis_ping(url: str) -> bool:
    import redis  # dependency of Celery's Redis broker (requirements/base.txt)

    client = redis.Redis.from_url(
        url, socket_connect_timeout=PING_TIMEOUT_SECONDS, socket_timeout=PING_TIMEOUT_SECONDS
    )
    try:
        return bool(client.ping())
    finally:
        client.close()


def _redis():
    url = getattr(settings, "CELERY_BROKER_URL", "") or ""
    if not url.startswith(("redis://", "rediss://")):
        return UNKNOWN, "The broker is not Redis; not checked."
    try:
        ok = redis_ping(url)
    except Exception as error:
        return FAILED, f"Not reachable: {type(error).__name__}"
    return (HEALTHY, "Reachable.") if ok else (FAILED, "No PING reply.")


def celery_ping() -> list:
    from config.celery import app

    return app.control.ping(timeout=PING_TIMEOUT_SECONDS) or []


def _celery_workers():
    replies = celery_ping()
    if not replies:
        return FAILED, f"No worker replied within {PING_TIMEOUT_SECONDS:g} s."
    return HEALTHY, f"{len(replies)} worker(s) replied."


def health() -> dict:
    redis_check = _check("redis", _redis)
    if redis_check["status"] == HEALTHY:
        workers = _check("celery_workers", _celery_workers)
    else:  # never ping through an unreachable broker: Celery would keep retrying and hang
        workers = {
            "name": "celery_workers",
            "status": UNKNOWN,
            "detail": "Not checked: the message broker (Redis) is not reachable.",
        }
    checks = [
        {"name": "api", "status": HEALTHY, "detail": "Answered this request."},
        _check("database", _database),
        redis_check,
        workers,
    ]
    statuses = {c["status"] for c in checks}
    overall = FAILED if FAILED in statuses else UNKNOWN if UNKNOWN in statuses else HEALTHY
    return {"checked_at": timezone.now(), "overall": overall, "checks": checks}
