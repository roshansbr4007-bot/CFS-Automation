"""Scoped reads. Views never build employee querysets themselves.

Scope (approved Q7/Q8):
- org.view_all_employees: every employee.
- org.view_team_employees: employees in the department of the viewer's own ACTIVE employee
  record (active and inactive members), plus the viewer's own record.
- everyone else: only their own employee record.
Records outside scope are simply not in the queryset, so the API answers 404.
"""

from django.db.models import Q
from django.utils.dateparse import parse_date

from apps.core.errors import FieldValidationError

from . import perms
from .models import Employee, EmployeeDailyLogin

_BOOL = {"true": True, "1": True, "false": False, "0": False}


def own_employee(user) -> Employee | None:
    if not getattr(user, "is_authenticated", False):
        return None
    return Employee.objects.select_related("department").filter(user=user).first()


def team_department_id(user) -> int | None:
    employee = own_employee(user)
    return employee.department_id if employee is not None and employee.is_active else None


def visible_employees(user):
    qs = Employee.objects.select_related("department", "reporting_manager", "user")
    if not getattr(user, "is_authenticated", False):
        return qs.none()
    if user.has_perm(perms.VIEW_ALL_EMPLOYEES):
        return qs
    scope = Q(user=user)
    if user.has_perm(perms.VIEW_TEAM_EMPLOYEES):
        department_id = team_department_id(user)
        if department_id is not None:
            scope |= Q(department_id=department_id)
    return qs.filter(scope)


def _parse_bool(params, name):
    raw = params.get(name)
    if raw is None or raw == "":
        return None
    if raw.lower() not in _BOOL:
        raise FieldValidationError(fields={name: ["Use true or false."]})
    return _BOOL[raw.lower()]


def list_employees(user, params):
    qs = visible_employees(user)
    if search := (params.get("search") or "").strip():
        qs = qs.filter(
            Q(full_name__icontains=search)
            | Q(email__icontains=search)
            | Q(employee_code__icontains=search)
        )
    if department := params.get("department"):
        if not str(department).isdigit():
            raise FieldValidationError(fields={"department": ["Must be a department id."]})
        qs = qs.filter(department_id=int(department))
    if (is_active := _parse_bool(params, "is_active")) is not None:
        qs = qs.filter(is_active=is_active)
    if (has_login := _parse_bool(params, "has_login")) is not None:
        qs = qs.filter(user__isnull=not has_login)
    return qs.order_by("full_name", "id")


def login_facts(employee: Employee, params):
    qs = EmployeeDailyLogin.objects.filter(employee=employee)
    for name, lookup in (("from", "work_date__gte"), ("to", "work_date__lte")):
        raw = params.get(name)
        if not raw:
            continue
        try:
            day = parse_date(raw)
        except ValueError:  # well-formed but impossible, e.g. 2026-13-01
            day = None
        if day is None:
            raise FieldValidationError(fields={name: ["Use a real date as YYYY-MM-DD."]})
        qs = qs.filter(**{lookup: day})
    return qs.order_by("-work_date")
