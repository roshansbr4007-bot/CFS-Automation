"""Overdue case workflow (Phase 9): open (system), submit reason (employee), review (reviewer).

- Opening is idempotent: one case per RESOLUTION clock (database-unique), whatever the number
  of checker runs, restarts or concurrent workers.
- Notifications use the existing in-app Notification model (dedup_key; no email: the default
  email_status is NOT_REQUIRED). Realtime signals use Phase 8's publish_to_user AFTER commit.
- Every step writes the existing append-only audit log.
"""

import logging
from datetime import datetime

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts import roles
from apps.audit.services import record
from apps.core.errors import FieldValidationError
from apps.notifications.models import Notification, NotificationKind
from apps.realtime.events import publish_to_user
from apps.sla import engine
from apps.sla.models import ClockKind, TaskSla
from apps.tasks.models import Task

from . import selectors
from .errors import OverduePermissionDenied, OverdueStateConflict, OverdueVersionConflict
from .models import OverdueCase, OverdueCause, OverdueStatus

logger = logging.getLogger(__name__)
User = get_user_model()
MAX_TEXT = 4000


# --- helpers ----------------------------------------------------------------------------------


def _audit(action, case, actor, *, old=None, new=None):
    record(
        action=action,
        entity_type="overdue_case",
        entity_id=case.pk,
        actor=actor,
        use_request_user=actor is not None,
        old=old,
        new=new,
        extra={
            "task_id": case.task_id,
            "employee_id": case.employee_id,
            "department_id": case.department_id,
        },
    )


def _publish_after_commit(user_ids, event: str, case: OverdueCase) -> None:
    data = {"case_id": case.pk, "task_id": case.task_id, "status": case.status}
    for user_id in sorted(set(user_ids)):
        transaction.on_commit(lambda uid=user_id: publish_to_user(uid, event, data))


def _notify(users, case: OverdueCase, kind: str, title: str, body: str, tag: str) -> None:
    Notification.objects.bulk_create(
        [
            Notification(
                recipient=user, task_id=case.task_id, clock_id=case.clock_id, kind=kind,
                title=title[:200], body=body, dedup_key=f"overdue:{case.pk}:{tag}:{user.pk}",
            )
            for user in users
        ],
        ignore_conflicts=True,
    )


def _employee_user(case: OverdueCase):
    user = case.employee.user
    return user if user is not None and user.is_active else None


def reviewer_recipients(case: OverdueCase) -> list:
    """Approved Q9: active Operations Managers of the case's TASK department and active HR
    users. Admin is not notified. The case's own employee is never a recipient."""
    managers = User.objects.filter(
        is_active=True, groups__name=roles.OPERATIONS_MANAGER,
        employee__is_active=True, employee__department_id=case.department_id,
    )
    hr = User.objects.filter(is_active=True, groups__name=roles.HR)
    users = {u.pk: u for u in [*managers, *hr]}
    users.pop(case.employee.user_id, None)
    return [users[pk] for pk in sorted(users)]


def _lock(case: OverdueCase, version: int) -> OverdueCase:
    locked = (
        OverdueCase.objects.select_for_update(of=("self",))
        .select_related("employee", "employee__user")
        .get(pk=case.pk)
    )
    if locked.status == OverdueStatus.REVIEWED:
        raise OverdueStateConflict("This case has been reviewed and can no longer change.")
    if locked.version != version:
        raise OverdueVersionConflict()
    return locked


def _text(value, field: str) -> str:
    text = (value or "").strip() if isinstance(value, str) else ""
    if not text:
        raise FieldValidationError(fields={field: ["This field is required."]})
    if len(text) > MAX_TEXT:
        raise FieldValidationError(fields={field: [f"Use at most {MAX_TEXT} characters."]})
    return text


def _cause(value, field: str) -> str:
    if value not in OverdueCause.values:
        raise FieldValidationError(
            fields={field: [f"Choose one of: {', '.join(OverdueCause.values)}."]}
        )
    return value


def overdue_instant(clock: TaskSla) -> datetime:
    """When the clock counted as overdue: the checker's record, or (completed late before the
    checker saw it) the rule's overdue threshold computed by the existing SLA engine."""
    if clock.overdue_at is not None:
        return clock.overdue_at
    return engine.threshold_instant(
        clock.start_at, clock.due_at, clock.rule_snapshot["overdue_pct"]
    )


# --- open (system) ----------------------------------------------------------------------------


def open_case_for_clock(clock: TaskSla, *, source: str) -> OverdueCase | None:
    """Open THE case for an overdue RESOLUTION clock. Returns None when it already exists (or
    the clock does not qualify). Runs inside the caller's transaction."""
    if clock.kind != ClockKind.RESOLUTION or clock.start_at is None or clock.due_at is None:
        return None
    if OverdueCase.objects.filter(clock_id=clock.pk).exists():
        return None
    task = Task.objects.select_related(
        "assigned_to__user", "created_by", "category", "department"
    ).get(pk=clock.task_id)
    snap = clock.rule_snapshot or {}
    try:
        with transaction.atomic():  # a concurrent duplicate loses cleanly on the unique key
            case = OverdueCase.objects.create(
                clock=clock,
                task=task,
                employee=task.assigned_to,
                department=task.department,
                task_title=task.title,
                task_creator=task.created_by,
                task_assigned_at=task.assigned_at,
                priority=task.priority,
                category_name=task.category.name if task.category_id else "",
                sla_start_at=clock.start_at,
                sla_due_at=clock.due_at,
                sla_rule_code=snap.get("code", ""),
                sla_rule_name=snap.get("name", ""),
                overdue_at=overdue_instant(clock),
                opened_via=source,
            )
    except IntegrityError:
        return None
    _audit(
        "overdue_case.opened", case, None,
        new={"clock_id": clock.pk, "opened_via": source, "overdue_at": case.overdue_at.isoformat(),
             "employee_id": case.employee_id},
    )
    user = _employee_user(case)
    if user is not None:
        _notify([user], case, NotificationKind.OVERDUE_REASON,
                f"Reason needed: {case.task_title}",
                "This task passed its deadline. Please submit the reason for the delay.",
                "reason")
        _publish_after_commit([user.pk], "overdue_case.opened", case)
    return case


def record_open_failure(clock: TaskSla, source: str) -> None:
    """Best-effort audit of a case that could not be opened (repair: open_missing_overdue_cases)."""
    try:
        with transaction.atomic():
            record(
                action="overdue_case.open_failed", entity_type="task", entity_id=clock.task_id,
                new={"clock_id": clock.pk, "source": source}, use_request_user=False,
            )
    except Exception:  # never break the SLA engine
        logger.exception("Could not audit the overdue-case failure for clock %s", clock.pk)


def phase9_deployed_at() -> datetime | None:
    """When this app's first migration was applied: the earliest moment cases can exist. The
    repair command never looks before it, so it cannot become a historical backfill."""
    from django.db.migrations.recorder import MigrationRecorder

    return (
        MigrationRecorder.Migration.objects.filter(app="overdue", name="0001_initial")
        .values_list("applied", flat=True)
        .first()
    )


def missing_case_clocks(window_start: datetime):
    """RESOLUTION clocks without a case that genuinely became overdue at or after
    `window_start`: the checker recorded the overdue threshold, or the task was completed
    after its deadline before the checker saw it. Cancelled-before-overdue clocks never match."""
    from django.db.models import Q

    from apps.sla.models import Outcome, StopReason

    return (
        TaskSla.objects.filter(kind=ClockKind.RESOLUTION, overdue_case__isnull=True)
        .filter(
            Q(overdue_at__gte=window_start)
            | Q(overdue_at__isnull=True, outcome=Outcome.MISSED,
                stop_reason=StopReason.COMPLETED, stopped_at__gte=window_start)
        )
        .order_by("id")
    )


# --- submit (employee) ------------------------------------------------------------------------


def submit_reason(*, actor, case: OverdueCase, version: int, reason_category, explanation):
    category = _cause(reason_category, "reason_category")
    text = _text(explanation, "explanation")
    with transaction.atomic():
        case = _lock(case, version)
        if not selectors.is_own(actor, case):
            raise OverduePermissionDenied("Only the employee on this case can submit its reason.")
        if case.status != OverdueStatus.OPEN:
            raise OverdueStateConflict("A reason has already been submitted for this case.")
        case.reason_category = category
        case.explanation = text
        case.submitted_at = timezone.now()
        case.submitted_by = actor
        case.status = OverdueStatus.REASON_SUBMITTED
        case.version += 1
        case.save()
        _audit("overdue_case.reason_submitted", case, actor,
               old={"status": OverdueStatus.OPEN},
               new={"status": case.status, "reason_category": category, "explanation": text})
        reviewers = reviewer_recipients(case)
        _notify(reviewers, case, NotificationKind.OVERDUE_REVIEW,
                f"Reason submitted — review needed: {case.task_title}",
                f"{case.employee.full_name} submitted the reason for an overdue task.", "review")
        _publish_after_commit([actor.pk, *(u.pk for u in reviewers)],
                              "overdue_case.submitted", case)
    return case


# --- review (reviewer) ------------------------------------------------------------------------


def review_case(*, actor, case: OverdueCase, version: int, cause, remark):
    final_cause = _cause(cause, "cause")
    text = _text(remark, "remark")
    with transaction.atomic():
        case = _lock(case, version)
        if selectors.is_own(actor, case):
            raise OverduePermissionDenied("You cannot review your own overdue case.")
        if not selectors.in_review_scope(actor, case):
            raise OverduePermissionDenied()
        if case.status != OverdueStatus.REASON_SUBMITTED:
            raise OverdueStateConflict("The employee's reason must be submitted before review.")
        case.cause = final_cause
        case.review_remark = text
        case.reviewed_at = timezone.now()
        case.reviewed_by = actor
        case.status = OverdueStatus.REVIEWED
        case.version += 1
        case.save()  # the last write: REVIEWED is terminal (model guard)
        _audit("overdue_case.reviewed", case, actor,
               old={"status": OverdueStatus.REASON_SUBMITTED},
               new={"status": case.status, "cause": final_cause, "remark": text,
                    "employee_reason": case.reason_category})
        employee = _employee_user(case)
        _publish_after_commit([actor.pk, *([employee.pk] if employee else [])],
                              "overdue_case.reviewed", case)
    return case
