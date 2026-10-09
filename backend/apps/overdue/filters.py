"""Validated list / report filters for overdue cases (Phase 9). Invalid values -> 400."""

from datetime import time, timedelta

from django.utils.dateparse import parse_date

from apps.core.errors import FieldValidationError
from apps.core.timeutils import ist_datetime
from apps.tasks.models import TaskPriority

from .models import OverdueCause, OverdueStatus


def _int(params, name):
    raw = params.get(name)
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise FieldValidationError(fields={name: ["Enter a whole number."]}) from None
    if value < 1:
        raise FieldValidationError(fields={name: ["Enter a positive number."]})
    return value


def _choice(params, name, allowed):
    raw = params.get(name)
    if raw in (None, ""):
        return None
    if raw not in allowed:
        raise FieldValidationError(fields={name: [f"Use one of: {', '.join(allowed)}."]})
    return raw


def _date(params, name):
    raw = params.get(name)
    if raw in (None, ""):
        return None
    value = parse_date(raw) if isinstance(raw, str) else None
    if value is None:
        raise FieldValidationError(fields={name: ["Use the format YYYY-MM-DD."]})
    return value


def apply_filters(qs, params):
    """status, employee, department (the task department), date_from / date_to (the IST date
    the case opened), cause, reason_category, task, priority."""
    status = _choice(params, "status", tuple(OverdueStatus.values))
    cause = _choice(params, "cause", tuple(OverdueCause.values))
    reason = _choice(params, "reason_category", tuple(OverdueCause.values))
    priority = _choice(params, "priority", tuple(TaskPriority.values))
    employee, department, task = (_int(params, n) for n in ("employee", "department", "task"))
    date_from, date_to = _date(params, "date_from"), _date(params, "date_to")
    if date_from and date_to and date_from > date_to:
        raise FieldValidationError(fields={"date_to": ["Must be on or after date_from."]})
    if status:
        qs = qs.filter(status=status)
    if cause:
        qs = qs.filter(cause=cause)
    if reason:
        qs = qs.filter(reason_category=reason)
    if priority:
        qs = qs.filter(priority=priority)
    if employee:
        qs = qs.filter(employee_id=employee)
    if department:
        qs = qs.filter(department_id=department)
    if task:
        qs = qs.filter(task_id=task)
    if date_from:
        qs = qs.filter(opened_at__gte=ist_datetime(date_from, time(0, 0)))
    if date_to:
        qs = qs.filter(opened_at__lt=ist_datetime(date_to + timedelta(days=1), time(0, 0)))
    return qs
