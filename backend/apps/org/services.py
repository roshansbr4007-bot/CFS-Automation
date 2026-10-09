"""All writes to departments, employees and daily login facts.

Each public function runs in one transaction and writes its audit row(s) through
apps.audit.services.record() inside that transaction (Phase 1 audit rule).
"""

import re

from django.db import IntegrityError, transaction

from apps.audit.services import record
from apps.calendars.services import is_working_day
from apps.core.errors import ConflictError, FieldValidationError
from apps.core.timeutils import to_ist

from .models import Department, Employee, EmployeeDailyLogin

UNSET = object()
DEPARTMENT_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,15}$")
MAX_MANAGER_CHAIN = 1000


class VersionConflict(ConflictError):
    code = "version_conflict"
    message = "This record was changed by someone else. Reload and try again."


# --- Departments ------------------------------------------------------------------------------


def _department_snapshot(department: Department) -> dict:
    return {"code": department.code, "name": department.name, "is_live": department.is_live}


def create_department(*, actor, code: str, name: str, is_live: bool = False) -> Department:
    code = (code or "").strip().upper()
    if not DEPARTMENT_CODE_RE.match(code):
        raise FieldValidationError(
            fields={"code": ["Use 1-16 capital letters, digits or _, starting with a letter."]}
        )
    if Department.objects.filter(code=code).exists():
        raise ConflictError(
            "A department with this code already exists.", code="department_code_taken"
        )
    with transaction.atomic():
        department = Department.objects.create(code=code, name=name.strip(), is_live=is_live)
        record(
            action="department.created",
            entity_type="department",
            entity_id=department.pk,
            actor=actor,
            new=_department_snapshot(department),
        )
    return department


def update_department(*, actor, department: Department, code=UNSET, name=UNSET, is_live=UNSET):
    if code is not UNSET and (code or "").strip().upper() != department.code:
        raise FieldValidationError(
            "Department codes cannot be changed.",
            code="field_immutable",
            fields={"code": ["Department codes cannot be changed."]},
        )
    with transaction.atomic():
        department = Department.objects.select_for_update().get(pk=department.pk)
        old, new = {}, {}
        for field, value in (("name", name), ("is_live", is_live)):
            if value is UNSET:
                continue
            if field == "name":
                value = value.strip()
            if value != getattr(department, field):
                old[field], new[field] = getattr(department, field), value
                setattr(department, field, value)
        if new:
            department.save(update_fields=[*new.keys(), "updated_at"])
            record(
                action="department.updated",
                entity_type="department",
                entity_id=department.pk,
                actor=actor,
                old=old,
                new=new,
            )
    return department


# --- Employees --------------------------------------------------------------------------------


def _employee_snapshot(employee: Employee) -> dict:
    return {
        "employee_code": employee.employee_code,
        "full_name": employee.full_name,
        "email": employee.email,
        "department_id": employee.department_id,
        "reporting_manager_id": employee.reporting_manager_id,
        "designation": employee.designation,
        "date_of_joining": _iso(employee.date_of_joining),
        "is_active": employee.is_active,
        "user_id": employee.user_id,
    }


def _iso(value):
    return value.isoformat() if value is not None else None


def _normalise_code(code):
    code = (code or "").strip()
    return code or None


def _check_email_free(email: str, exclude_pk=None) -> None:
    taken = Employee.objects.filter(email__iexact=email)
    if exclude_pk is not None:
        taken = taken.exclude(pk=exclude_pk)
    if taken.exists():
        raise ConflictError("An employee with this email already exists.", code="email_taken")


def _check_code_free(code, exclude_pk=None) -> None:
    if code is None:
        return
    taken = Employee.objects.filter(employee_code=code)
    if exclude_pk is not None:
        taken = taken.exclude(pk=exclude_pk)
    if taken.exists():
        raise ConflictError(
            "An employee with this code already exists.", code="employee_code_taken"
        )


def _check_manager(manager, employee=None) -> None:
    """Manager must be active, not the employee, and must not create a reporting cycle."""
    if manager is None:
        return
    if employee is not None and manager.pk == employee.pk:
        raise FieldValidationError(
            fields={"reporting_manager": ["An employee cannot report to themselves."]}
        )
    if not manager.is_active:
        raise FieldValidationError(
            fields={"reporting_manager": ["The reporting manager must be an active employee."]}
        )
    if employee is None:
        return
    current, steps = manager, 0
    while current.reporting_manager_id is not None and steps < MAX_MANAGER_CHAIN:
        if current.reporting_manager_id == employee.pk:
            raise FieldValidationError(
                fields={"reporting_manager": ["This would create a reporting cycle."]}
            )
        current = Employee.objects.only("id", "reporting_manager_id").get(
            pk=current.reporting_manager_id
        )
        steps += 1


def create_employee(
    *,
    actor,
    full_name: str,
    email: str,
    department: Department,
    employee_code=None,
    reporting_manager=None,
    designation: str = "",
    date_of_joining=None,
) -> Employee:
    email = (email or "").strip().lower()
    code = _normalise_code(employee_code)
    _check_email_free(email)
    _check_code_free(code)
    _check_manager(reporting_manager)
    try:
        with transaction.atomic():
            employee = Employee.objects.create(
                full_name=full_name.strip(),
                email=email,
                department=department,
                employee_code=code,
                reporting_manager=reporting_manager,
                designation=(designation or "").strip(),
                date_of_joining=date_of_joining,
            )
            record(
                action="employee.created",
                entity_type="employee",
                entity_id=employee.pk,
                actor=actor,
                new=_employee_snapshot(employee),
                extra={"department_id": employee.department_id},
            )
    except IntegrityError as exc:  # two requests racing for the same email or code
        raise ConflictError(
            "An employee with this email or code already exists.", code="employee_conflict"
        ) from exc
    return employee


UPDATABLE_FIELDS = (
    "full_name",
    "email",
    "department",
    "employee_code",
    "reporting_manager",
    "designation",
    "date_of_joining",
)


def update_employee(*, actor, employee: Employee, version: int, **changes) -> Employee:
    unknown = set(changes) - set(UPDATABLE_FIELDS) - {"is_active"}
    if unknown:  # programming error, never user input
        raise TypeError(f"Unknown fields: {sorted(unknown)}")
    with transaction.atomic():
        employee = (
            Employee.objects.select_for_update().get(pk=employee.pk)
        )
        if employee.version != version:
            raise VersionConflict()

        old, new = {}, {}
        if "full_name" in changes:
            changes["full_name"] = changes["full_name"].strip()
        if "designation" in changes:
            changes["designation"] = (changes["designation"] or "").strip()
        if "employee_code" in changes:
            changes["employee_code"] = _normalise_code(changes["employee_code"])
            if changes["employee_code"] != employee.employee_code:
                _check_code_free(changes["employee_code"], exclude_pk=employee.pk)
        if "email" in changes:
            changes["email"] = (changes["email"] or "").strip().lower()
            if changes["email"] != employee.email:
                _check_email_free(changes["email"], exclude_pk=employee.pk)
                if employee.user_id and changes["email"] != employee.user.email.lower():
                    raise FieldValidationError(
                        "The email must match the linked login's email.",
                        code="login_email_mismatch",
                        fields={"email": ["Must match the linked login's email (Q3)."]},
                    )
        if "reporting_manager" in changes:
            _check_manager(changes["reporting_manager"], employee)

        for field in UPDATABLE_FIELDS:
            if field not in changes:
                continue
            value = changes[field]
            if field in ("department", "reporting_manager"):
                current_id = getattr(employee, f"{field}_id")
                value_id = value.pk if value is not None else None
                if value_id != current_id:
                    old[f"{field}_id"], new[f"{field}_id"] = current_id, value_id
                    setattr(employee, field, value)
            elif field == "date_of_joining":
                if value != employee.date_of_joining:
                    old[field], new[field] = _iso(employee.date_of_joining), _iso(value)
                    employee.date_of_joining = value
            elif value != getattr(employee, field):
                old[field], new[field] = getattr(employee, field), value
                setattr(employee, field, value)

        status_changed = "is_active" in changes and changes["is_active"] != employee.is_active
        if not new and not status_changed:
            return employee

        if status_changed:
            employee.is_active = changes["is_active"]
        employee.version += 1
        employee.save()

        context = {"department_id": employee.department_id}
        if "department_id" in old:
            context["old_department_id"] = old["department_id"]
        if new:
            record(
                action="employee.updated",
                entity_type="employee",
                entity_id=employee.pk,
                actor=actor,
                old=old,
                new=new,
                extra=context,
            )
        if status_changed:
            record(
                action="employee.reactivated" if employee.is_active else "employee.deactivated",
                entity_type="employee",
                entity_id=employee.pk,
                actor=actor,
                old={"is_active": not employee.is_active},
                new={"is_active": employee.is_active},
                extra=context,
            )
    return employee


def link_login(*, actor, employee: Employee, user, version: int) -> Employee:
    with transaction.atomic():
        employee = Employee.objects.select_for_update().get(pk=employee.pk)
        if employee.version != version:
            raise VersionConflict()
        if employee.user_id is not None:
            raise ConflictError(
                "This employee already has a login. Unlink it first.",
                code="employee_already_linked",
            )
        if Employee.objects.filter(user=user).exists():
            raise ConflictError(
                "This login is already linked to another employee.", code="login_already_linked"
            )
        if user.email.lower() != employee.email.lower():
            raise FieldValidationError(
                "The login's email must match the employee's email.",
                code="login_email_mismatch",
                fields={"user": ["The login's email must match the employee's email (Q3)."]},
            )
        employee.user = user
        employee.version += 1
        employee.save(update_fields=["user", "version", "updated_at"])
        record(
            action="employee.login_linked",
            entity_type="employee",
            entity_id=employee.pk,
            actor=actor,
            old={"user_id": None},
            new={"user_id": user.pk},
            extra={"department_id": employee.department_id},
        )
    return employee


def unlink_login(*, actor, employee: Employee, version: int) -> Employee:
    with transaction.atomic():
        employee = Employee.objects.select_for_update().get(pk=employee.pk)
        if employee.version != version:
            raise VersionConflict()
        if employee.user_id is None:
            raise ConflictError("This employee has no linked login.", code="no_login_linked")
        old_user_id = employee.user_id
        employee.user = None
        employee.version += 1
        employee.save(update_fields=["user", "version", "updated_at"])
        record(
            action="employee.login_unlinked",
            entity_type="employee",
            entity_id=employee.pk,
            actor=actor,
            old={"user_id": old_user_id},
            new={"user_id": None},
            extra={"department_id": employee.department_id},
        )
    return employee


# --- Daily login facts ------------------------------------------------------------------------


def record_daily_login(*, user, at) -> EmployeeDailyLogin | None:
    """Record the first successful login of the user's active employee on the IST date of `at`.

    Called from the user_logged_in signal, inside the login transaction. Later logins on the
    same IST day change nothing. Never raises into the login flow for a duplicate-day race.
    """
    employee = Employee.objects.filter(user=user, is_active=True).first()
    if employee is None:
        return None
    work_date = to_ist(at).date()
    try:
        with transaction.atomic():
            fact, created = EmployeeDailyLogin.objects.get_or_create(
                employee=employee,
                work_date=work_date,
                defaults={"first_login_at": at, "is_valid": is_working_day(work_date)},
            )
            if created:
                record(
                    action="employee.daily_login_recorded",
                    entity_type="employee",
                    entity_id=employee.pk,
                    actor=user,
                    new={
                        "work_date": work_date.isoformat(),
                        "first_login_at": at.isoformat(),
                        "is_valid": fact.is_valid,
                    },
                    extra={"department_id": employee.department_id},
                )
    except IntegrityError:  # another login of the same employee won the race for this day
        return None
    return fact if created else None

