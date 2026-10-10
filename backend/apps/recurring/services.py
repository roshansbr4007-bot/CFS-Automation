"""Responsibilities, their ownership history and their schedules (all writes audited).

Scope: Admin and HR manage everything (organisation-wide); an Operations Manager manages
responsibilities of the department of their own ACTIVE employee record. Schedules follow the
same scope (Phase A); Admin's global `manage_schedules` authority is unchanged.

Owners (locked rules A/B/E): only HR and Admin (`manage_all_responsibilities`) assign, change or
end an owner; an Operations Manager keeps every other management action of their department. An
owner always belongs to the responsibility's department. When a new owner starts TODAY, today's
open generated task of the previous owner moves to them through the existing task reassignment
(clocks, deadline, status, comments, attachments and history kept; the previous owner is
notified). Tasks already completed or cancelled never move.

Responsibility deadline (SLA): only HR / Admin (`manage_all_responsibilities`) define, change
or clear it. It is a versioned DURATION SlaRule "RESP_<id>": a change supersedes the rule (tasks
already generated keep their deadline), clearing unlinks it (the rule and its history remain).
"""

from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.calendars.services import resolve_scheduled_date
from apps.core.errors import AppError, ConflictError, FieldValidationError
from apps.core.timeutils import to_ist
from apps.notifications.services import notify_task_transferred
from apps.org.selectors import team_department_id
from apps.sla import services as sla_services
from apps.tasks import services as task_services
from apps.tasks.models import Task, TaskSource, TaskStatus

from . import perms
from .models import (
    Frequency,
    NonWorkingDayPolicy,
    RecurringSchedule,
    Responsibility,
    ResponsibilityOwner,
)


class RecurringPermissionDenied(AppError):
    status_code = 403
    code = "permission_denied"
    message = "You do not have permission to do this."


class RecurringVersionConflict(ConflictError):
    code = "version_conflict"
    message = "This record was changed by someone else. Reload and try again."


class ResponsibilityInactive(ConflictError):
    """Change Set 1 (D6): a deactivated (archived) responsibility is kept with all its history
    but no longer changes: no edits, owners or schedules."""

    code = "responsibility_inactive"
    message = "This responsibility is deactivated (archived) and can no longer be changed."


def _require_active(responsibility_id: int) -> None:
    if not Responsibility.objects.filter(pk=responsibility_id, is_active=True).exists():
        raise ResponsibilityInactive()


def today_ist() -> date:
    return to_ist(timezone.now()).date()


# --- scope ------------------------------------------------------------------------------------


def can_manage(user, responsibility_or_department_id) -> bool:
    department_id = getattr(responsibility_or_department_id, "department_id", None)
    if department_id is None:
        department_id = responsibility_or_department_id
    if user.has_perm(perms.MANAGE_ALL_RESPONSIBILITIES):
        return True
    return user.has_perm(perms.MANAGE_TEAM_RESPONSIBILITIES) and (
        team_department_id(user) == department_id
    )


def _require_manage(user, target) -> None:
    if not can_manage(user, target):
        raise RecurringPermissionDenied()


def can_manage_owner(user) -> bool:
    """Locked rule A: HR and Admin (the organisation-wide responsibility authority; there is no
    separate Boss role) assign, change and end owners. Not an Operations Manager."""
    return user.has_perm(perms.MANAGE_ALL_RESPONSIBILITIES)


def _require_owner_authority(user) -> None:
    if not can_manage_owner(user):
        raise RecurringPermissionDenied(
            "Only HR or Admin can assign or change a responsibility owner."
        )


OWNER_DEPARTMENT_MESSAGE = "The owner must belong to the responsibility's department ({code})."


def _check_owner_department(responsibility, employee) -> None:
    """Locked rule B: an owner always belongs to the responsibility's department."""
    if employee.department_id != responsibility.department_id:
        raise FieldValidationError(fields={"employee": [
            OWNER_DEPARTMENT_MESSAGE.format(code=responsibility.department.code)
        ]})


def _audit(action, entity_type, entity_id, actor, *, old=None, new=None, extra=None):
    record(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        old=old,
        new=new,
        extra=extra or {},
    )


# --- responsibility deadline (SLA) ------------------------------------------------------------

_UNSET = object()


def can_manage_deadline(user) -> bool:
    """HR and Admin only. An Operations Manager manages team responsibilities but not deadlines."""
    return user.has_perm(perms.MANAGE_ALL_RESPONSIBILITIES)


def deadline_minutes(responsibility) -> int | None:
    """The responsibility deadline in minutes, or None when none is configured."""
    if not responsibility.deadline_rule_code:
        return None
    rule = sla_services.active_rule(responsibility.deadline_rule_code)
    return rule.duration_minutes if rule is not None else None


def _check_deadline(actor, minutes) -> None:
    if not can_manage_deadline(actor):
        raise RecurringPermissionDenied("Only HR or Admin can configure a responsibility deadline.")
    if minutes is not None and (
        isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1
    ):
        raise FieldValidationError(fields={"deadline_minutes": ["Use a whole number of minutes."]})


def _apply_deadline(actor, r: Responsibility, minutes: int | None) -> dict | None:
    """Link, change or clear r's deadline (caller holds the row lock and saves r). Returns the
    audit payload, or None when nothing changes."""
    before = deadline_minutes(r)
    if minutes == before:
        return None
    if minutes is None:
        r.deadline_rule_code = ""
        return {"old": {"deadline_minutes": before}, "new": {"deadline_minutes": None}}
    code = f"RESP_{r.pk}"
    rule = sla_services.active_rule(code)
    if rule is None:
        rule = sla_services.create_rule(
            actor=actor, code=code, name=f"{r.code} deadline", duration_minutes=minutes
        )
    elif rule.duration_minutes != minutes:
        rule = sla_services.supersede_rule(actor=actor, rule=rule, duration_minutes=minutes)
    r.deadline_rule_code = code
    return {
        "old": {"deadline_minutes": before},
        "new": {"deadline_minutes": minutes, "rule_code": code, "rule_version": rule.version},
    }


# --- responsibilities -------------------------------------------------------------------------

RESPONSIBILITY_FIELDS = ("name", "description", "category", "template", "priority")


def _responsibility_snapshot(r: Responsibility) -> dict:
    return {
        "code": r.code,
        "name": r.name,
        "department_id": r.department_id,
        "category_id": r.category_id,
        "template_id": r.template_id,
        "priority": r.priority,
        "is_active": r.is_active,
    }


def _text(value, field):
    value = (value or "").strip()
    if not value:
        raise FieldValidationError(fields={field: ["This field is required."]})
    return value


def create_responsibility(
    *, actor, code, name, department, category, description="", template=None, priority="MEDIUM"
) -> Responsibility:
    _require_manage(actor, department.pk)
    code = _text(code, "code").upper()
    if Responsibility.objects.filter(code=code).exists():
        raise ConflictError(
            "A responsibility with this code exists.", code="responsibility_code_taken"
        )
    if not category.is_active:
        raise FieldValidationError(fields={"category": ["This category is not active."]})
    with transaction.atomic():
        r = Responsibility.objects.create(
            code=code,
            name=_text(name, "name"),
            description=(description or "").strip(),
            department=department,
            category=category,
            template=template,
            priority=priority,
        )
        _audit(
            "responsibility.created", "responsibility", r.pk, actor, new=_responsibility_snapshot(r)
        )
    return r


def update_responsibility(
    *, actor, responsibility, version, deadline_minutes=_UNSET, **changes
) -> Responsibility:
    allowed = {*RESPONSIBILITY_FIELDS, "is_active", "department"}
    if set(changes) - allowed:  # programming error, never user input
        raise TypeError(f"Unknown fields: {sorted(set(changes) - allowed)}")
    with transaction.atomic():
        r = Responsibility.objects.select_for_update().get(pk=responsibility.pk)
        _require_manage(actor, r)
        if r.version != version:
            raise RecurringVersionConflict()
        if "department" in changes and changes["department"].pk != r.department_id:
            if not actor.has_perm(perms.MANAGE_ALL_RESPONSIBILITIES):
                raise RecurringPermissionDenied("Only Admin may move a responsibility.")
            # Locked rule B: a move may not leave a current or future owner in another department.
            other = (
                r.owners.filter(superseded_at__isnull=True)
                .exclude(effective_to__lt=today_ist())
                .exclude(employee__department_id=changes["department"].pk)
            )
            if other.exists():
                raise FieldValidationError(fields={"department": [
                    "Its current or a planned owner belongs to another department. End that "
                    "ownership first, move the responsibility, then assign an owner of the new "
                    "department."
                ]})
        if "category" in changes and not changes["category"].is_active:
            raise FieldValidationError(fields={"category": ["This category is not active."]})
        if "name" in changes:
            changes["name"] = _text(changes["name"], "name")
        old, new = {}, {}
        for field in (*RESPONSIBILITY_FIELDS, "department"):
            if field not in changes:
                continue
            value = changes[field]
            if field in ("category", "template", "department"):
                current, wanted = getattr(r, f"{field}_id"), (value.pk if value else None)
                if current != wanted:
                    old[f"{field}_id"], new[f"{field}_id"] = current, wanted
                    setattr(r, field, value)
            elif value != getattr(r, field):
                old[field], new[field] = getattr(r, field), value
                setattr(r, field, value)
        status_changed = "is_active" in changes and changes["is_active"] != r.is_active
        deadline_change = None
        if deadline_minutes is not _UNSET:  # only HR / Admin may even send it
            _check_deadline(actor, deadline_minutes)
            if not r.is_active:
                raise ResponsibilityInactive()
            deadline_change = _apply_deadline(actor, r, deadline_minutes)
        if new and not r.is_active:
            raise ResponsibilityInactive()
        if not new and not status_changed and deadline_change is None:
            return r
        if status_changed:
            r.is_active = changes["is_active"]
        r.version += 1
        r.save()
        if new:
            _audit("responsibility.updated", "responsibility", r.pk, actor, old=old, new=new)
        if deadline_change is not None:
            _audit(
                "responsibility.deadline_cleared"
                if deadline_minutes is None
                else "responsibility.deadline_set",
                "responsibility",
                r.pk,
                actor,
                **deadline_change,
            )
        if status_changed:
            _audit(
                "responsibility.activated" if r.is_active else "responsibility.deactivated",
                "responsibility",
                r.pk,
                actor,
                old={"is_active": not r.is_active},
                new={"is_active": r.is_active},
            )
    return r


# --- ownership --------------------------------------------------------------------------------


def current_owner_row(responsibility, day: date | None = None) -> ResponsibilityOwner | None:
    day = day or today_ist()
    return (
        responsibility.owners.filter(effective_from__lte=day, superseded_at__isnull=True)
        .exclude(effective_to__lt=day)
        .select_related("employee")
        .order_by("-effective_from", "-id")  # a same-day correction wins over its predecessor
        .first()
    )


NO_OWNER_REASON = "No responsible employee is configured for this date."


def resolve_owner(responsibility, day: date):
    """(employee, None) or (None, reason) for the owner of `responsibility` on `day`."""
    row = current_owner_row(responsibility, day)
    if row is None:
        return None, NO_OWNER_REASON
    if not row.employee.is_active:
        return None, f"The responsible employee ({row.employee.full_name}) is inactive."
    return row.employee, None


def assign_owner(
    *, actor, responsibility, employee, effective_from: date, note=""
) -> ResponsibilityOwner:
    """Make `employee` the owner from `effective_from` on. The current period is closed the day
    before; history is never rewritten, and tasks of other days keep their assignee.

    Phase 5.2 same-day correction: when the new owner starts TODAY and the current period started
    today or later (a mistaken or future-dated configuration), that period is marked superseded
    (kept for history and audit, never resolved as owner) and the new owner applies from today.

    Locked rules A/B/E: HR / Admin only; the owner belongs to the responsibility's department;
    when the new owner starts today, today's OPEN generated tasks of today's previous owner move
    to them through tasks.services.reassign_task in this same transaction (all or nothing), and
    the previous owner is notified. Completed or cancelled tasks never move; tasks of other days
    keep their assignee. The moved tasks are returned on the row as `transferred_tasks`."""
    _require_owner_authority(actor)
    if not employee.is_active:
        raise FieldValidationError(fields={"employee": ["The employee is not active."]})
    if effective_from < today_ist():
        raise FieldValidationError(
            fields={"effective_from": ["Ownership cannot start in the past."]}
        )
    with transaction.atomic():
        r = Responsibility.objects.select_for_update().get(pk=responsibility.pk)
        _require_manage(actor, r)
        _require_active(r.pk)
        _check_owner_department(r, employee)
        today = today_ist()
        before_today = current_owner_row(r, today) if effective_from == today else None
        open_row = r.owners.filter(effective_to__isnull=True, superseded_at__isnull=True).first()
        previous = None
        corrected = False
        if open_row is not None:
            if open_row.employee_id == employee.pk:
                raise ConflictError(
                    "This employee already owns the responsibility.", code="already_owner"
                )
            if effective_from <= open_row.effective_from:
                if effective_from != today_ist():
                    raise FieldValidationError(
                        fields={"effective_from": ["Must be after the current owner's start date."]}
                    )
                open_row.superseded_at = timezone.now()
                open_row.superseded_by = actor
                open_row.save(update_fields=["superseded_at", "superseded_by"])
                corrected = True
            else:
                open_row.effective_to = effective_from - timedelta(days=1)
                open_row.save(update_fields=["effective_to"])
            previous = open_row
        row = ResponsibilityOwner.objects.create(
            responsibility=r,
            employee=employee,
            effective_from=effective_from,
            note=(note or "").strip(),
            assigned_by=actor,
        )
        transferred = []
        if before_today is not None and before_today.employee_id != employee.pk:
            transferred = _transfer_todays_tasks(
                actor, r, today, before_today.employee, employee, row.note
            )
        if corrected:
            action = "responsibility.owner_corrected"
        else:
            action = "responsibility.owner_changed" if previous else "responsibility.owner_assigned"
        _audit(
            action,
            "responsibility",
            r.pk,
            actor,
            old=(
                {
                    "employee_id": previous.employee_id,
                    "effective_from": previous.effective_from.isoformat(),
                    "effective_to": (
                        previous.effective_to.isoformat() if previous.effective_to else None
                    ),
                    "superseded": corrected,
                }
                if previous
                else None
            ),
            new={"employee_id": employee.pk, "effective_from": effective_from.isoformat()},
            extra={
                "department_id": r.department_id,
                "note": row.note,
                "transferred_task_ids": [t.pk for t in transferred],
            },
        )
    row.transferred_tasks = transferred
    return row


def _transfer_todays_tasks(actor, responsibility, today, previous, new_owner, note) -> list:
    """Locked rule E: move today's OPEN generated tasks of `previous` to `new_owner` with the
    existing reassignment (its own permission checks, TaskAssignment history, audit and SLA rule:
    the resolution clock is never reset; an acknowledgment, when required, restarts for the new
    assignee as on any reassignment). Rows are locked first; a concurrent change of one of these
    tasks makes the whole owner change fail with a conflict instead of half-applying."""
    reason = f"Responsibility owner changed to {new_owner.full_name}"
    if note:
        reason = f"{reason}: {note}"
    tasks = list(
        Task.objects.select_for_update()
        .filter(
            responsibility=responsibility,
            source=TaskSource.SCHEDULED,
            occurrence_date=today,
            assigned_to=previous,
            status__in=(TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED),
        )
        .order_by("pk")
    )
    moved = []
    for task in tasks:
        moved_task = task_services.reassign_task(
            actor=actor, task=task, version=task.version, assigned_to=new_owner, note=reason
        )
        assignment = moved_task.assignments.order_by("-assigned_at", "-id").first()
        notify_task_transferred(moved_task, previous, new_owner, assignment)
        moved.append(moved_task)
    return moved


def end_ownership(*, actor, responsibility, last_day: date, note="") -> ResponsibilityOwner:
    """The current owner stops after `last_day`; from then on nobody owns it (occurrences are
    SKIPPED and reported until a new owner is assigned)."""
    _require_owner_authority(actor)
    if last_day < today_ist():
        raise FieldValidationError(fields={"last_day": ["Cannot end ownership in the past."]})
    with transaction.atomic():
        r = Responsibility.objects.select_for_update().get(pk=responsibility.pk)
        _require_manage(actor, r)
        _require_active(r.pk)
        open_row = r.owners.filter(effective_to__isnull=True, superseded_at__isnull=True).first()
        if open_row is None:
            raise ConflictError("This responsibility has no current owner.", code="no_owner")
        if last_day < open_row.effective_from:
            raise FieldValidationError(
                fields={"last_day": ["Cannot end before the owner's start date."]}
            )
        open_row.effective_to = last_day
        open_row.save(update_fields=["effective_to"])
        _audit(
            "responsibility.owner_ended",
            "responsibility",
            r.pk,
            actor,
            old={"employee_id": open_row.employee_id, "effective_to": None},
            new={"employee_id": open_row.employee_id, "effective_to": last_day.isoformat()},
            extra={"department_id": r.department_id, "note": (note or "").strip()},
        )
    return open_row


# --- schedules (Admin) ------------------------------------------------------------------------

SCHEDULE_FIELDS = (
    "title",
    "description",
    "frequency",
    "run_time",
    "day_of_month",
    "weekdays",
    "run_date",
    "non_working_day_policy",
    "effective_from",
    "effective_to",
)


def _schedule_snapshot(s: RecurringSchedule) -> dict:
    return {
        "responsibility_id": s.responsibility_id,
        "title": s.title,
        "frequency": s.frequency,
        "run_time": s.run_time.strftime("%H:%M"),
        "day_of_month": s.day_of_month,
        "weekdays": list(s.weekdays or []),
        "run_date": s.run_date.isoformat() if s.run_date else None,
        "non_working_day_policy": s.non_working_day_policy,
        "effective_from": s.effective_from.isoformat(),
        "effective_to": s.effective_to.isoformat() if s.effective_to else None,
        "is_active": s.is_active,
    }


def _check_weekdays(raw) -> list[int]:
    """Phase B: WEEKLY weekdays are 0=Monday .. 6=Sunday, at least one, each once; stored sorted."""
    if not isinstance(raw, list) or not raw:
        raise FieldValidationError(fields={"weekdays": ["Choose at least one weekday."]})
    if any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in raw):
        raise FieldValidationError(
            fields={"weekdays": ["Use weekday numbers 0 (Monday) to 6 (Sunday)."]}
        )
    if len(set(raw)) != len(raw):
        raise FieldValidationError(fields={"weekdays": ["Choose each weekday only once."]})
    return sorted(raw)


def _derive_once_window(values: dict) -> None:
    """Phase B (B-D4): a one-off date is today or later for every role; on a non-working day it
    must move (NEXT / PREVIOUS) rather than be skipped. Its effective window is derived so it
    always covers both the chosen date and the business date it resolves to."""
    run_date, today = values["run_date"], today_ist()
    if run_date < today:
        raise FieldValidationError(fields={"run_date": ["Choose today or a later date."]})
    resolved = resolve_scheduled_date(run_date, values["non_working_day_policy"])
    if resolved is None:
        raise FieldValidationError(fields={"run_date": [
            "This date is not a working day. Choose a working day, or move it to the next "
            "or previous working day."
        ]})
    if resolved < today:
        raise FieldValidationError(fields={"run_date": [
            "The previous working day has already passed. Choose another date."
        ]})
    values["effective_from"] = min(run_date, resolved)
    values["effective_to"] = max(run_date, resolved)


def _check_schedule(values: dict) -> None:
    frequency = values["frequency"]
    day = values.get("day_of_month")
    if frequency == Frequency.DAILY and day is not None:
        raise FieldValidationError(fields={"day_of_month": ["Daily schedules have no day."]})
    if frequency == Frequency.MONTHLY and not (day and 1 <= day <= 28):
        raise FieldValidationError(fields={"day_of_month": ["Use a day from 1 to 28."]})
    # Phase B: each frequency carries only its own fields.
    if frequency in (Frequency.WEEKLY, Frequency.ONCE) and day is not None:
        raise FieldValidationError(
            fields={"day_of_month": ["Only monthly schedules have a day of the month."]}
        )
    if frequency == Frequency.WEEKLY:
        values["weekdays"] = _check_weekdays(values.get("weekdays"))
    elif values.get("weekdays"):
        raise FieldValidationError(fields={"weekdays": ["Only weekly schedules have weekdays."]})
    else:
        values["weekdays"] = []
    if frequency == Frequency.ONCE:
        if values.get("run_date") is None:
            raise FieldValidationError(fields={"run_date": ["Choose the date."]})
    elif values.get("run_date") is not None:
        raise FieldValidationError(
            fields={"run_date": ["Only specific-date schedules have a date."]}
        )
    end = values.get("effective_to")
    if end is not None and end < values["effective_from"]:
        raise FieldValidationError(fields={"effective_to": ["Must be on or after the start."]})


def can_manage_schedule(user, responsibility) -> bool:
    """Admin's global authority, or whoever manages the responsibility (Phase A: HR
    organisation-wide, an Operations Manager in their own department)."""
    return user.has_perm(perms.MANAGE_SCHEDULES) or can_manage(user, responsibility)


def _require_schedule_manage(actor, responsibility) -> None:
    if not can_manage_schedule(actor, responsibility):
        raise RecurringPermissionDenied()


def _check_start(actor, effective_from: date) -> None:
    """Phase A: only Admin may start a schedule in the past (existing behaviour); HR and
    Operations Managers start today or later, so no past days are recorded as missed."""
    if not actor.has_perm(perms.MANAGE_SCHEDULES) and effective_from < today_ist():
        raise FieldValidationError(
            fields={"effective_from": ["A schedule cannot start in the past."]}
        )


def create_schedule(*, actor, responsibility, **values) -> RecurringSchedule:
    _require_schedule_manage(actor, responsibility)
    _require_active(responsibility.pk)
    values.setdefault("non_working_day_policy", NonWorkingDayPolicy.SKIP)
    values["title"] = _text(values.get("title"), "title")
    if values.get("frequency") == Frequency.ONCE and values.get("run_date") is not None:
        _derive_once_window(values)  # Phase B: the window comes from the date
    _check_schedule(values)
    _check_start(actor, values["effective_from"])
    with transaction.atomic():
        s = RecurringSchedule.objects.create(
            responsibility=responsibility, created_by=actor, updated_by=actor, **values
        )
        _audit("schedule.created", "recurring_schedule", s.pk, actor, new=_schedule_snapshot(s))
    return s


def _check_frequency_change(s: RecurringSchedule, values: dict) -> None:
    """Phase B (B-D5): DAILY <-> MONTHLY stays allowed; WEEKLY / ONCE are never converted."""
    new = values["frequency"]
    if new != s.frequency and {s.frequency, new} & {Frequency.WEEKLY, Frequency.ONCE}:
        raise FieldValidationError(fields={"frequency": [
            "Weekly and specific-date schedules cannot change frequency. Create a new schedule."
        ]})


def _update_once_window(s: RecurringSchedule, values: dict) -> None:
    """Phase B (B-D4/B-D5): the window always follows the date. The date (or how a non-working
    date moves) can change only until the occurrence has been processed."""
    date_changed = values["run_date"] != s.run_date
    policy_changed = values["non_working_day_policy"] != s.non_working_day_policy
    values["effective_from"], values["effective_to"] = s.effective_from, s.effective_to
    if not (date_changed or policy_changed):
        return
    if s.occurrences.exists():
        raise FieldValidationError(fields={"run_date": [
            "This date has already been processed. Create a new schedule instead."
        ]})
    if values["run_date"] is not None:
        _derive_once_window(values)


def update_schedule(*, actor, schedule, version, **changes) -> RecurringSchedule:
    """Changes apply to future occurrences only; generated tasks are never touched."""
    _require_schedule_manage(actor, schedule.responsibility)
    _require_active(schedule.responsibility_id)
    allowed = {*SCHEDULE_FIELDS, "is_active"}
    if set(changes) - allowed:
        raise TypeError(f"Unknown fields: {sorted(set(changes) - allowed)}")
    with transaction.atomic():
        s = RecurringSchedule.objects.select_for_update().get(pk=schedule.pk)
        if s.version != version:
            raise RecurringVersionConflict()
        before = _schedule_snapshot(s)
        values = {f: getattr(s, f) for f in SCHEDULE_FIELDS} | {
            k: v for k, v in changes.items() if k in SCHEDULE_FIELDS
        }
        if "title" in changes:
            values["title"] = _text(changes["title"], "title")
        _check_frequency_change(s, values)
        once = s.frequency == Frequency.ONCE
        if once:
            _update_once_window(s, values)
        _check_schedule(values)
        start_moved = "effective_from" in changes and changes["effective_from"] != s.effective_from
        if not once and start_moved:
            _check_start(actor, changes["effective_from"])
        for field, value in values.items():
            setattr(s, field, value)
        if "is_active" in changes:
            s.is_active = changes["is_active"]
        after = _schedule_snapshot(s)
        if after == before:
            return s
        s.version += 1
        s.updated_by = actor
        s.save()
        old = {k: v for k, v in before.items() if before[k] != after[k]}
        new = {k: after[k] for k in old}
        action = "schedule.updated"
        if set(old) == {"is_active"}:
            action = "schedule.activated" if s.is_active else "schedule.deactivated"
        _audit(action, "recurring_schedule", s.pk, actor, old=old, new=new)
    return s


# --- Phase A: one-step setup ------------------------------------------------------------------


def _prefixed(error: FieldValidationError, prefix: str) -> FieldValidationError:
    return FieldValidationError(
        error.message, fields={f"{prefix}.{key}": value for key, value in error.fields.items()}
    )


def setup_responsibility(
    *, actor, responsibility: dict, schedule: dict, owner: dict | None = None
) -> Responsibility:
    """Create a responsibility, optionally its owner, and its first schedule in ONE transaction.

    Reuses create_responsibility(), assign_owner() and create_schedule() unchanged: their own
    permission checks, validation and audit all run. Any failure rolls back everything (no
    responsibility without its schedule). Owner / schedule field errors are re-keyed
    ("owner.employee", "schedule.run_time") so the form can show them in the right section.
    An optional `deadline_minutes` (HR / Admin only) sets the responsibility deadline too."""
    responsibility = dict(responsibility)
    minutes = responsibility.pop("deadline_minutes", None)
    with transaction.atomic():
        r = create_responsibility(actor=actor, **responsibility)
        if minutes is not None:
            _check_deadline(actor, minutes)
            change = _apply_deadline(actor, r, minutes)
            r.save(update_fields=["deadline_rule_code", "updated_at"])
            _audit("responsibility.deadline_set", "responsibility", r.pk, actor, **change)
        if owner is not None:
            try:
                assign_owner(actor=actor, responsibility=r, **owner)
            except FieldValidationError as error:
                raise _prefixed(error, "owner") from error
        try:
            create_schedule(actor=actor, responsibility=r, **schedule)
        except FieldValidationError as error:
            raise _prefixed(error, "schedule") from error
    return r
