"""Scoped reads for responsibilities, schedules and occurrences.

HR and Admin see everything; an Operations Manager sees their own department's (the department
of their own ACTIVE employee record); everyone else sees nothing here (their generated tasks
are in the normal task views). Records outside scope answer 404.
"""

from django.db.models import Prefetch, Q
from django.utils.dateparse import parse_date

from apps.core.errors import FieldValidationError
from apps.org.selectors import team_department_id

from . import perms
from .models import (
    OccurrenceStatus,
    RecurringSchedule,
    Responsibility,
    ResponsibilityOwner,
    ScheduleOccurrence,
)


def can_view_any(user) -> bool:
    return any(
        user.has_perm(p)
        for p in (
            perms.VIEW_ALL_RESPONSIBILITIES,
            perms.MANAGE_ALL_RESPONSIBILITIES,
            perms.MANAGE_TEAM_RESPONSIBILITIES,
        )
    )


def _department_scope(user, field: str):
    if user.has_perm(perms.VIEW_ALL_RESPONSIBILITIES) or user.has_perm(
        perms.MANAGE_ALL_RESPONSIBILITIES
    ):
        return Q()
    department_id = team_department_id(user)
    if user.has_perm(perms.MANAGE_TEAM_RESPONSIBILITIES) and department_id is not None:
        return Q(**{field: department_id})
    return None


def visible_responsibilities(user):
    owners = ResponsibilityOwner.objects.select_related("employee", "assigned_by")
    qs = Responsibility.objects.select_related(
        "department", "category", "template"
    ).prefetch_related(Prefetch("owners", queryset=owners), "schedules")
    scope = _department_scope(user, "department_id") if user.is_authenticated else None
    return qs.none() if scope is None else qs.filter(scope)


def visible_schedules(user):
    qs = RecurringSchedule.objects.select_related("responsibility")
    scope = (
        _department_scope(user, "responsibility__department_id")
        if user.is_authenticated
        else None
    )
    return qs.none() if scope is None else qs.filter(scope)


def _date(params, name):
    raw = params.get(name)
    if not raw:
        return None
    try:
        value = parse_date(raw)
    except ValueError:
        value = None
    if value is None:
        raise FieldValidationError(fields={name: ["Use a real date as YYYY-MM-DD."]})
    return value


def _id(params, name):
    raw = params.get(name)
    if not raw:
        return None
    if not str(raw).isdigit():
        raise FieldValidationError(fields={name: ["Must be a number."]})
    return int(raw)


def visible_occurrences(user, params=None):
    params = params or {}
    qs = ScheduleOccurrence.objects.select_related(
        "schedule", "schedule__responsibility", "task", "assignee"
    )
    scope = (
        _department_scope(user, "schedule__responsibility__department_id")
        if user.is_authenticated
        else None
    )
    if scope is None:
        return qs.none()
    qs = qs.filter(scope)
    status = params.get("status")
    if status:
        if status not in OccurrenceStatus.values:
            raise FieldValidationError(
                fields={"status": [f"Use one of: {', '.join(OccurrenceStatus.values)}."]}
            )
        qs = qs.filter(status=status)
    if (value := _date(params, "from")) is not None:
        qs = qs.filter(occurrence_date__gte=value)
    if (value := _date(params, "to")) is not None:
        qs = qs.filter(occurrence_date__lte=value)
    if (value := _id(params, "responsibility")) is not None:
        qs = qs.filter(schedule__responsibility_id=value)
    if (value := _id(params, "schedule")) is not None:
        qs = qs.filter(schedule_id=value)
    return qs.order_by("-occurrence_date", "-id")
