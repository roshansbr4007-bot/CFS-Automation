"""Daily Activities and Admin (Boss) operations monitoring - Phase 5.1. Read-only.

Nothing here creates, starts or changes a task or a clock: the recurring generator creates daily
activities, the SLA engine owns start / deadline / state / outcome, and this module only reads
them (sla.task_sla) so the API can present them. Logging in never creates anything.

Definitions (documented, deterministic):
- Daily activities of a date D: SCHEDULED tasks whose occurrence_date is D (cancelled excluded).
  For today, an employee's own list also carries over earlier daily activities still open.
- Assigned-task workload of a date D: MANUAL tasks of the employee, not cancelled, assigned on or
  before D and not completed before D. "Completed" = completed on D; "pending" = not completed
  by the end of D; "overdue" = pending and its SLA deadline passed (by the end of D, or by now
  for today). Ad-hoc tasks without an SLA have no deadline and are never overdue.
- On time / late: the SLA engine's recorded outcome (MET / MISSED) of the resolution clock.
"""

from datetime import date, datetime, time, timedelta

from django.db.models import Q

from apps.core.timeutils import ist_datetime, to_ist
from apps.org.models import Employee
from apps.sla import services as sla
from apps.sla.models import SlaState

from .models import Task, TaskSource, TaskStatus

OPEN = (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED)
DAILY_STATUS_FILTERS = ("pending", "completed", "overdue")
SLA_STATE_FILTERS = tuple(SlaState.values)


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = ist_datetime(day, time(0, 0))
    return start, start + timedelta(days=1)


def _result(task, resolution) -> str | None:
    if task.status != TaskStatus.COMPLETED:
        return None
    if resolution is None or resolution.get("outcome") is None:
        return "NO_DEADLINE"
    return "ON_TIME" if resolution["outcome"] == "MET" else "LATE"


def _with_related(qs):
    return qs.select_related(
        "responsibility", "schedule", "assigned_to", "assigned_by", "department"
    ).prefetch_related("sla_clocks")


def activity_row(task, now: datetime) -> dict:
    data = sla.task_sla(task, now)
    resolution = data["resolution"]
    scheduled_start = resolution["start_at"] if resolution else None
    if scheduled_start is None and task.schedule_id and task.occurrence_date:
        scheduled_start = ist_datetime(task.occurrence_date, task.schedule.run_time)
    is_open = task.status in OPEN
    running = is_open and resolution is not None and resolution["start_at"] is not None
    return {
        "task_id": task.pk,
        "reference": task.reference,
        "title": task.title,
        "responsibility": task.responsibility,
        "occurrence_date": task.occurrence_date,
        "scheduled_start": scheduled_start,
        "deadline": resolution["due_at"] if resolution else None,
        "status": task.status,
        "sla_state": resolution["state"] if resolution else None,
        "sla_note": data["resolution_note"],
        "remaining_seconds": resolution["remaining_seconds"] if running else None,
        "completed_at": task.completed_at,
        "completion_result": _result(task, resolution),
        "is_overdue": running and resolution["state"] == SlaState.OVERDUE,
        "assignee": task.assigned_to,
    }


def assigned_row(task, day: date, now: datetime) -> dict:
    data = sla.task_sla(task, now)
    resolution = data["resolution"]
    start, end = day_bounds(day)
    completed_on_day = (
        task.status == TaskStatus.COMPLETED
        and task.completed_at is not None
        and start <= task.completed_at < end
    )
    deadline = resolution["due_at"] if resolution else None
    cutoff = min(now, end)
    pending = not completed_on_day
    return {
        "task_id": task.pk,
        "reference": task.reference,
        "title": task.title,
        "priority": task.priority,
        "raised_by": task.created_by,
        "department": task.department,
        "category": task.category,
        "assigned_at": task.assigned_at,
        "assigned_by": task.assigned_by,
        "deadline": deadline,
        "status": task.status,
        "sla_state": resolution["state"] if resolution else None,
        "completed_at": task.completed_at,
        "completion_result": _result(task, resolution),
        "completed_on_day": bool(completed_on_day),
        "is_overdue": bool(pending and deadline is not None and deadline < cutoff),
        # Phase 6B (additive): the SLA engine's remaining time while the task is open and running.
        "remaining_seconds": (
            resolution["remaining_seconds"]
            if task.status in OPEN and resolution and resolution["start_at"]
            else None
        ),
    }


# --- queries ----------------------------------------------------------------------------------


def daily_activity_tasks(employees, day: date, *, carry_over_open: bool = False):
    q = Q(occurrence_date=day)
    if carry_over_open:
        q |= Q(occurrence_date__lt=day, status__in=OPEN)
    return _with_related(
        Task.objects.filter(source=TaskSource.SCHEDULED, assigned_to__in=employees)
        .filter(q)
        .exclude(status=TaskStatus.CANCELLED)
        .order_by("occurrence_date", "schedule__run_time", "title", "id")
    )


def assigned_workload_tasks(employees, day: date):
    start, end = day_bounds(day)
    return _with_related(
        Task.objects.select_related("created_by", "category").filter(
            source=TaskSource.MANUAL, assigned_to__in=employees, assigned_at__lt=end
        )
        .exclude(status=TaskStatus.CANCELLED)
        .filter(Q(status__in=OPEN) | Q(completed_at__gte=start))
        .order_by("assigned_at", "id")
    )


def my_daily_activities(user, day: date, now: datetime) -> list[dict]:
    """The signed-in employee's own daily activities (generated by the scheduler, never here)."""
    employee = Employee.objects.filter(user=user, is_active=True).first()
    if employee is None:
        return []
    today = to_ist(now).date()
    tasks = daily_activity_tasks([employee], day, carry_over_open=(day == today))
    return [activity_row(t, now) for t in tasks]


# --- counts -----------------------------------------------------------------------------------


def daily_counts(rows) -> dict:
    return {
        "total": len(rows),
        "completed": sum(r["status"] == TaskStatus.COMPLETED for r in rows),
        "pending": sum(r["status"] in OPEN for r in rows),
        "overdue": sum(r["is_overdue"] for r in rows),
        "completed_late": sum(r["completion_result"] == "LATE" for r in rows),
    }


def assigned_counts(rows) -> dict:
    return {
        "total": len(rows),
        "completed": sum(r["completed_on_day"] for r in rows),
        "pending": sum(not r["completed_on_day"] for r in rows),
        "overdue": sum(r["is_overdue"] for r in rows),
        "completed_late": sum(
            r["completed_on_day"] and r["completion_result"] == "LATE" for r in rows
        ),
    }


def filter_daily(rows, status: str | None, sla_state: str | None) -> list[dict]:
    if status == "pending":
        rows = [r for r in rows if r["status"] in OPEN]
    elif status == "completed":
        rows = [r for r in rows if r["status"] == TaskStatus.COMPLETED]
    elif status == "overdue":
        rows = [r for r in rows if r["is_overdue"]]
    if sla_state:
        rows = [r for r in rows if r["sla_state"] == sla_state]
    return rows


def filter_assigned(rows, status: str | None) -> list[dict]:
    if status == "pending":
        return [r for r in rows if not r["completed_on_day"]]
    if status == "completed":
        return [r for r in rows if r["completed_on_day"]]
    if status == "overdue":
        return [r for r in rows if r["is_overdue"]]
    return rows


def employee_summary(day: date, now: datetime, *, department_id=None, employee_id=None) -> list:
    employees = Employee.objects.filter(is_active=True).select_related("department")
    if department_id is not None:
        employees = employees.filter(department_id=department_id)
    if employee_id is not None:
        employees = employees.filter(pk=employee_id)
    employees = list(employees.order_by("full_name", "id"))
    daily, assigned = {}, {}
    for task in daily_activity_tasks(employees, day):
        daily.setdefault(task.assigned_to_id, []).append(activity_row(task, now))
    for task in assigned_workload_tasks(employees, day):
        assigned.setdefault(task.assigned_to_id, []).append(assigned_row(task, day, now))
    return [
        {
            "employee": employee,
            "daily_activity": daily_counts(daily.get(employee.pk, [])),
            "assigned_tasks": assigned_counts(assigned.get(employee.pk, [])),
        }
        for employee in employees
    ]
