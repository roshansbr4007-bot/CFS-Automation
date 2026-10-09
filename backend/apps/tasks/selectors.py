"""Scoped reads for tasks. Views never build task querysets themselves.

Visibility (approved Phase 3 rules, same department pattern as Phase 2):
- tasks.view_all_tasks (HR, Admin): every task.
- everyone: tasks they created (Sent) and tasks assigned to them (Received).
- tasks.view_team_tasks (Operations Manager): plus tasks of their own department, using the
  department of their own ACTIVE employee record.
Tasks outside scope are not in the queryset, so the API answers 404.
"""

from datetime import time, timedelta

from django.db.models import Q
from django.utils.dateparse import parse_date

from apps.core.errors import FieldValidationError
from apps.core.timeutils import ist_datetime
from apps.org.models import Employee

from . import perms, policy
from .models import Task, TaskPriority, TaskSource, TaskStatus

VIEWS = ("received", "sent", "all")
SOURCES = {"scheduled": TaskSource.SCHEDULED, "manual": TaskSource.MANUAL}


def _with_related(qs):
    return qs.select_related(
        "department",
        "category",
        "created_by",
        "assigned_to",
        "assigned_by",
        "completed_by",
        "template",
        "responsibility",
        "schedule",
    ).prefetch_related("sla_clocks")


def task_after_authorized_write(pk: int) -> Task:
    """The task as it is after a write that the service layer has ALREADY authorized and
    committed, for that single response only.

    Needed because an authorized write can move a task out of the writer's own scope (an
    Operations Manager moving a task to another department). Never use this to read tasks for
    a request: every read goes through visible_tasks(), so the next request answers 404.
    """
    return _with_related(Task.objects).get(pk=pk)


def visible_tasks(user):
    qs = _with_related(Task.objects)
    if not getattr(user, "is_authenticated", False):
        return qs.none()
    if user.has_perm(perms.VIEW_ALL_TASKS):
        return qs
    scope = Q(created_by=user) | Q(assigned_to__user=user)
    if user.has_perm(perms.VIEW_TEAM_TASKS):
        department_id = policy.team_department_id(user)
        if department_id is not None:
            scope |= Q(department_id=department_id)
    return qs.filter(scope)


def _ist_day(raw: str, name: str):
    try:
        day = parse_date(raw)
    except ValueError:
        day = None
    if day is None:
        raise FieldValidationError(fields={name: ["Use a real date as YYYY-MM-DD."]})
    return ist_datetime(day, time(0, 0))


def _choice(params, name, choices):
    raw = params.get(name)
    if not raw:
        return None
    if raw not in choices:
        raise FieldValidationError(fields={name: [f"Use one of: {', '.join(choices)}."]})
    return raw


def _int(params, name):
    raw = params.get(name)
    if not raw:
        return None
    if not str(raw).isdigit():
        raise FieldValidationError(fields={name: ["Must be a number."]})
    return int(raw)


def list_tasks(user, params):
    qs = visible_tasks(user)
    view = _choice(params, "view", VIEWS) or "all"
    if view == "received":
        qs = qs.filter(assigned_to__user=user)
    elif view == "sent":
        qs = qs.filter(created_by=user)

    if source := _choice(params, "source", SOURCES):  # Phase 5: scheduled vs manual work
        qs = qs.filter(source=SOURCES[source])
    if status := _choice(params, "status", TaskStatus.values):
        qs = qs.filter(status=status)
    if priority := _choice(params, "priority", TaskPriority.values):
        qs = qs.filter(priority=priority)
    if (assignee := _int(params, "assignee")) is not None:
        qs = qs.filter(assigned_to_id=assignee)
    if (department := _int(params, "department")) is not None:
        qs = qs.filter(department_id=department)
    if search := (params.get("search") or "").strip():
        qs = qs.filter(Q(title__icontains=search) | Q(description__icontains=search))
    if raw_from := params.get("from"):
        qs = qs.filter(created_at__gte=_ist_day(raw_from, "from"))
    if raw_to := params.get("to"):
        qs = qs.filter(created_at__lt=_ist_day(raw_to, "to") + timedelta(days=1))
    return qs.order_by("-created_at", "-id")


def assignable_employees(user):
    """Employees this user may pick as assignee (create or reassign)."""
    active = Employee.objects.filter(is_active=True).select_related("department")
    if not policy.can_create(user):
        return active.none()
    if user.has_perm(perms.MANAGE_ALL_TASKS) or user.has_perm(perms.ASSIGN):
        return active.order_by("full_name", "id")
    scope = Q(user=user)
    if user.has_perm(perms.MANAGE_TEAM_TASKS):
        department_id = policy.team_department_id(user)
        if department_id is not None:
            scope |= Q(department_id=department_id)
    return active.filter(scope).order_by("full_name", "id")
