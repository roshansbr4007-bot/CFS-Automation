"""All task writes. Each public function runs in one transaction, locks the task row,
checks the client's version, enforces the workflow and policy, and writes its audit row(s)
through apps.audit.services.record() inside the same transaction.

Row locks never use select_related on nullable relations (PostgreSQL rejects FOR UPDATE on
the nullable side of an outer join); related rows are read with separate queries.
"""

import logging
import re

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.core.errors import ConflictError, FieldValidationError
from apps.notifications.models import Notification
from apps.recurring.models import ScheduleOccurrence
from apps.sla import services as sla
from apps.sla.models import TaskSla

from . import dependencies, policy
from .errors import (
    AcknowledgmentRequired,
    AssignmentNotAllowed,
    InvalidTransition,
    TaskPermissionDenied,
    TaskVersionConflict,
)
from .models import (
    CommentKind,
    CompletionSource,
    DependencyState,
    Task,
    TaskAssignment,
    TaskAttachment,
    TaskCategory,
    TaskComment,
    TaskSource,
    TaskStatus,
    TaskTemplate,
    TaskType,
    TaskVerification,
    VerificationDecision,
    VerificationStatus,
)

EDITABLE_FIELDS = (
    "title",
    "description",
    "task_type",
    "priority",
    "acknowledgment_required",
    "verification_required",
)
logger = logging.getLogger(__name__)

RECEIVED_FIELDS = ("received_at", "received_at_source")
CLASSIFICATION_FIELDS = ("department", "category")


def _iso(value):
    return value.isoformat() if value is not None else None


def _audit(action, task, actor, *, old=None, new=None, extra=None):
    record(
        action=action,
        entity_type="task",
        entity_id=task.pk,
        actor=actor,
        old=old,
        new=new,
        extra={"department_id": task.department_id, **(extra or {})},
    )


def _lock(task: Task, version: int) -> Task:
    locked = Task.objects.select_for_update().get(pk=task.pk)
    if locked.version != version:
        raise TaskVersionConflict()
    return locked


def _save(task: Task, fields) -> None:
    task.version += 1
    task.save(update_fields=[*fields, "version", "updated_at"])


def _require_text(value, field: str) -> str:
    value = (value or "").strip()
    if not value:
        raise FieldValidationError(fields={field: ["This field is required."]})
    return value


def _check_received(received_at, received_at_source) -> None:
    if (received_at is None) != (received_at_source is None):
        raise FieldValidationError(
            fields={
                "received_at_source": ["Give both the received time and its source, or neither."]
            }
        )
    if received_at is not None and received_at > timezone.now():
        raise FieldValidationError(fields={"received_at": ["Cannot be in the future."]})


def _check_task_type(task_type) -> None:
    if task_type == TaskType.RECURRING:
        raise FieldValidationError(
            fields={"task_type": ["Recurring tasks are created only by the scheduler."]}
        )


def _check_template(template, task_type, trigger_at) -> None:
    if template is None:
        if trigger_at is not None:
            raise FieldValidationError(
                fields={"trigger_at": ["Only event-triggered task types take an event time."]}
            )
        return
    if not template.is_active:
        raise FieldValidationError(fields={"template": ["This task type is not active."]})
    if task_type not in (None, TaskType.REGULAR):
        raise FieldValidationError(
            fields={"task_type": ["Tasks created from a task type are Regular tasks."]}
        )
    if template.trigger == "EVENT":
        if trigger_at is None:
            raise FieldValidationError(fields={"trigger_at": ["Enter when the event happened."]})
        if trigger_at > timezone.now():
            raise FieldValidationError(fields={"trigger_at": ["Cannot be in the future."]})
    elif trigger_at is not None:
        raise FieldValidationError(
            fields={"trigger_at": ["Only event-triggered task types take an event time."]}
        )


def _check_category(category) -> None:
    if category is None:
        raise FieldValidationError(fields={"category": ["This field is required."]})
    if not category.is_active:
        raise FieldValidationError(fields={"category": ["This category is not active."]})


def _snapshot(task: Task) -> dict:
    return {
        "title": task.title,
        "source": task.source,
        "responsibility_id": task.responsibility_id,
        "schedule_id": task.schedule_id,
        "occurrence_date": task.occurrence_date.isoformat() if task.occurrence_date else None,
        "department_id": task.department_id,
        "category_id": task.category_id,
        "template_id": task.template_id,
        "trigger_at": _iso(task.trigger_at),
        "task_type": task.task_type,
        "priority": task.priority,
        "status": task.status,
        "assigned_to_id": task.assigned_to_id,
        "received_at": _iso(task.received_at),
        "received_at_source": task.received_at_source,
        "acknowledgment_required": task.acknowledgment_required,
        "verification_required": task.verification_required,
    }


# --- Create, edit, reassign -------------------------------------------------------------------


def create_task(
    *,
    actor,
    title: str,
    assigned_to,
    department,
    category: TaskCategory,
    description: str = "",
    task_type: str | None = None,
    priority: str = "MEDIUM",
    received_at=None,
    received_at_source=None,
    acknowledgment_required: bool | None = None,
    verification_required: bool | None = None,
    template: TaskTemplate | None = None,
    trigger_at=None,
) -> Task:
    if not policy.can_create(actor):
        raise TaskPermissionDenied()
    _check_task_type(task_type)
    _check_template(template, task_type, trigger_at)
    if template is not None:
        task_type = TaskType.REGULAR
        # Locked rule: a task type's acknowledgment / verification settings come from its
        # template. A different value from the client is refused, never silently applied.
        for name, sent in (
            ("acknowledgment_required", acknowledgment_required),
            ("verification_required", verification_required),
        ):
            if sent is not None and sent != getattr(template, name):
                raise FieldValidationError(fields={name: ["Set by the selected task type."]})
        acknowledgment_required = template.acknowledgment_required
        verification_required = template.verification_required
    task_type = task_type or TaskType.ADHOC
    if not policy.can_assign_to(actor, assigned_to):
        raise AssignmentNotAllowed()
    _check_received(received_at, received_at_source)
    _check_category(category)
    title = _require_text(title, "title")

    return _create_task_record(
        actor=actor,
        fields={
            "title": title,
            "description": (description or "").strip(),
            "task_type": task_type,
            "priority": priority,
            "department": department,  # chosen by the creator, never derived from the assignee
            "category": category,
            "assigned_to": assigned_to,
            "received_at": received_at,
            "received_at_source": received_at_source,
            "acknowledgment_required": bool(acknowledgment_required),
            "verification_required": bool(verification_required),
            "template": template,
            "trigger_at": trigger_at,
        },
        audit_extra={"received_at_entered": received_at is not None},
    )


def _create_task_record(*, actor, fields: dict, audit_extra: dict) -> Task:
    """The one place a task row is written: task, first assignment, SLA clocks, audit.
    Callers (manual creation, scheduled generation) do their own rule checks first."""
    now = timezone.now()
    with transaction.atomic():
        task = Task.objects.create(
            **fields,
            created_by=actor,
            assigned_by=actor,
            assigned_at=now,
        )
        assignment = TaskAssignment.objects.create(
            task=task, to_employee=task.assigned_to, assigned_by=actor, assigned_at=now
        )
        sla.on_task_created(task, assignment, now)
        _audit("task.created", task, actor, new=_snapshot(task), extra=audit_extra)
        _audit("task.assigned", task, actor, new={"assigned_to_id": task.assigned_to_id})
        dependencies.link_new_task(task, actor)  # Task Dependency Engine (configuration only)
    return task


def create_scheduled_task(*, scheduler, schedule, occurrence_date, assignee, generated_at) -> Task:
    """A task generated by the system from a responsibility schedule (Phase 5).

    The scheduler identity is the creator; the responsibility's owner for that date is the
    assignee. Department, category, priority and task type come from the responsibility; the
    task type (template) chooses the SLA rule, and the SLA engine starts every clock of the task
    at its scheduled time (apps.sla.services.on_task_created), except a clock that waits on the
    dependency engine.
    """
    responsibility = schedule.responsibility
    template = responsibility.template
    if template is not None and not template.is_active:
        raise FieldValidationError(
            "The responsibility's task type is not active.",
            fields={"template": ["This task type is not active."]},
        )
    if not assignee.is_active:
        raise AssignmentNotAllowed("The responsible employee is not active.")
    return _create_task_record(
        actor=scheduler,
        fields={
            "title": f"{schedule.title} — {occurrence_date:%d %b %Y}",
            "description": schedule.description or responsibility.description,
            "task_type": TaskType.REGULAR if template else TaskType.ADHOC,
            "priority": responsibility.priority,
            "department": responsibility.department,
            "category": responsibility.category,
            "assigned_to": assignee,
            "acknowledgment_required": bool(template and template.acknowledgment_required),
            "verification_required": bool(template and template.verification_required),
            "template": template,
            "source": TaskSource.SCHEDULED,
            "responsibility": responsibility,
            "schedule": schedule,
            "occurrence_date": occurrence_date,
            "generated_at": generated_at,
        },
        audit_extra={
            "source": "scheduler",
            "responsibility_id": responsibility.pk,
            "schedule_id": schedule.pk,
            "occurrence_date": occurrence_date.isoformat(),
            "owner_employee_id": assignee.pk,
        },
    )


def update_task(*, actor, task: Task, version: int, received_at_reason=None, **changes) -> Task:
    allowed = {*EDITABLE_FIELDS, *RECEIVED_FIELDS, *CLASSIFICATION_FIELDS}
    unknown = set(changes) - allowed
    if unknown:  # programming error, never user input
        raise TypeError(f"Unknown fields: {sorted(unknown)}")
    with transaction.atomic():
        task = _lock(task, version)
        if "task_type" in changes:
            _check_task_type(changes["task_type"])
        if "title" in changes:
            changes["title"] = _require_text(changes["title"], "title")
        if "description" in changes:
            changes["description"] = (changes["description"] or "").strip()

        field_old, field_new = {}, {}
        for field in EDITABLE_FIELDS:
            if field in changes and changes[field] != getattr(task, field):
                field_old[field], field_new[field] = getattr(task, field), changes[field]

        department = changes.get("department", task.department)
        department_changed = department.pk != task.department_id
        category = changes.get("category", task.category)
        category_changed = (category.pk if category else None) != task.category_id
        if department_changed and not policy.can_change_department(actor, task):
            raise TaskPermissionDenied("You may not change this task's department.")
        if category_changed:
            if not policy.can_edit(actor, task):
                raise TaskPermissionDenied()
            _check_category(category)

        received_changed = any(
            field in changes and changes[field] != getattr(task, field)
            for field in RECEIVED_FIELDS
        )
        if field_new and not policy.can_edit(actor, task):
            raise TaskPermissionDenied()
        if task.status not in policy.OPEN_STATUSES and (
            {"acknowledgment_required", "verification_required"} & set(field_new)
        ):
            # These drive the SLA clocks and the verification workflow, which are finished
            # for a closed task; changing them now would start clocks or contradict history.
            raise InvalidTransition(
                "Acknowledgement and verification settings cannot change once a task is "
                "completed or cancelled."
            )
        if received_changed:
            if not policy.can_change_received_at(actor, task):
                raise TaskPermissionDenied()
            reason = _require_text(received_at_reason, "received_at_reason")
            new_received = changes.get("received_at", task.received_at)
            new_source = changes.get("received_at_source", task.received_at_source)
            _check_received(new_received, new_source)
        if not (field_new or received_changed or department_changed or category_changed):
            return task

        received_old = {"received_at": _iso(task.received_at), "source": task.received_at_source}
        old_department_id, old_category_id = task.department_id, task.category_id
        task.department = department
        task.category = category
        for field, value in field_new.items():
            setattr(task, field, value)
        if received_changed:
            task.received_at, task.received_at_source = new_received, new_source
        _save(task, [*field_new.keys(), *RECEIVED_FIELDS, *CLASSIFICATION_FIELDS])
        if "acknowledgment_required" in field_new:
            current = task.assignments.order_by("-assigned_at", "-id").first()
            sla.on_acknowledgment_requirement_changed(task, current, timezone.now())

        priority_old = field_old.pop("priority", None)
        priority_new = field_new.pop("priority", None)
        if priority_new is not None:
            _audit(
                "task.priority_changed",
                task,
                actor,
                old={"priority": priority_old},
                new={"priority": priority_new},
            )
        if field_new:
            _audit("task.updated", task, actor, old=field_old, new=field_new)
        if department_changed:
            _audit(
                "task.department_changed",
                task,
                actor,
                old={"department_id": old_department_id},
                new={"department_id": task.department_id},
                extra={"old_department_id": old_department_id},
            )
        if category_changed:
            _audit(
                "task.category_changed",
                task,
                actor,
                old={"category_id": old_category_id},
                new={"category_id": task.category_id},
            )
        if received_changed:
            _audit(
                "task.received_at_changed",
                task,
                actor,
                old=received_old,
                new={"received_at": _iso(task.received_at), "source": task.received_at_source},
                extra={"reason": reason},
            )
    return task


def reassign_task(*, actor, task: Task, version: int, assigned_to, note: str = "") -> Task:
    with transaction.atomic():
        task = _lock(task, version)
        if task.status not in policy.OPEN_STATUSES:
            raise InvalidTransition()
        if not policy.can_reassign(actor, task):
            raise TaskPermissionDenied()
        if not policy.can_assign_to(actor, assigned_to):
            raise AssignmentNotAllowed()
        if assigned_to.pk == task.assigned_to_id:
            raise FieldValidationError(
                fields={"assigned_to": ["The task is already assigned to this employee."]}
            )
        now = timezone.now()
        old = {
            "assigned_to_id": task.assigned_to_id,
            "acknowledged_at": _iso(task.acknowledged_at),
        }
        assignment = TaskAssignment.objects.create(
            task=task,
            from_employee_id=task.assigned_to_id,
            to_employee=assigned_to,
            assigned_by=actor,
            assigned_at=now,
            note=(note or "").strip(),
        )
        task.assigned_to = assigned_to  # task department and category stay as they are
        task.assigned_by = actor
        task.assigned_at = now
        if task.acknowledgment_required:
            task.acknowledged_at = None  # the new assignee must acknowledge
        _save(task, ["assigned_to", "assigned_by", "assigned_at", "acknowledged_at"])
        sla.on_reassigned(task, assignment, now)
        _audit(
            "task.reassigned",
            task,
            actor,
            old=old,
            new={
                "assigned_to_id": task.assigned_to_id,
                "acknowledged_at": _iso(task.acknowledged_at),
            },
            extra={
                "from_employee_id": old["assigned_to_id"],
                "to_employee_id": task.assigned_to_id,
                "assignment_id": assignment.pk,
                "note": assignment.note,
            },
        )
    return task


# --- Workflow actions -------------------------------------------------------------------------


def acknowledge_task(*, actor, task: Task, version: int) -> Task:
    with transaction.atomic():
        task = _lock(task, version)
        if task.status not in policy.OPEN_STATUSES or not policy.acknowledgment_outstanding(task):
            raise InvalidTransition("There is nothing to acknowledge on this task.")
        if not policy.is_assignee(actor, task):
            raise TaskPermissionDenied("Only the assignee can acknowledge this task.")
        task.acknowledged_at = timezone.now()
        _save(task, ["acknowledged_at"])
        sla.on_acknowledged(task, task.acknowledged_at)
        _audit(
            "task.acknowledged", task, actor, new={"acknowledged_at": _iso(task.acknowledged_at)}
        )
    return task


def start_task(*, actor, task: Task, version: int) -> Task:
    with transaction.atomic():
        task = _lock(task, version)
        if task.status != TaskStatus.PENDING:
            raise InvalidTransition()
        if not policy.is_assignee(actor, task):
            raise TaskPermissionDenied("Only the assignee can start this task.")
        if policy.acknowledgment_outstanding(task):
            raise AcknowledgmentRequired()
        task.status = TaskStatus.IN_PROGRESS
        task.started_at = timezone.now()
        _save(task, ["status", "started_at"])
        _audit(
            "task.started",
            task,
            actor,
            old={"status": TaskStatus.PENDING},
            new={"status": task.status, "started_at": _iso(task.started_at)},
        )
    return task


WORK_RESPONSE_MAX_LENGTH = 5000


def _require_work_response(value) -> str:
    """The assignee's report of the work performed: required, trimmed, never blank."""
    value = (value or "").strip()
    if not value:
        raise FieldValidationError(
            fields={"work_response": ["Describe the work you did before completing the task."]}
        )
    if len(value) > WORK_RESPONSE_MAX_LENGTH:
        raise FieldValidationError(
            fields={
                "work_response": [
                    f"Ensure this field has no more than {WORK_RESPONSE_MAX_LENGTH} characters."
                ]
            }
        )
    return value


def complete_task(*, actor, task: Task, version: int, work_response: str) -> Task:
    """Submit the work response and complete the task, in one transaction (no approval step).

    The checks run in their existing order (version, status, assignee, acknowledgment) so an
    invalid transition or a non-assignee is still refused as before; the response is validated
    last, and nothing is written unless every check passes. The response is stored as a
    WORK_RESPONSE comment (append-only) authored by the assignee, created at the completion
    instant, and the task.completed audit event references it."""
    with transaction.atomic():
        task = _lock(task, version)
        if task.status != TaskStatus.IN_PROGRESS:
            raise InvalidTransition()
        if not policy.is_assignee(actor, task):
            raise TaskPermissionDenied("Only the assignee can complete this task.")
        if policy.acknowledgment_outstanding(task):
            raise AcknowledgmentRequired()
        work_response = _require_work_response(work_response)
        now = timezone.now()
        response = TaskComment.objects.create(
            task=task, author=actor, body=work_response, kind=CommentKind.WORK_RESPONSE
        )
        # created_at is auto_now_add; align it with the completion instant so both are one fact.
        TaskComment.objects.filter(pk=response.pk).update(created_at=now)
        response.created_at = now
        task.status = TaskStatus.COMPLETED
        task.completed_at = now
        task.completed_by = actor
        task.completion_recorded_at = now
        task.completion_source = CompletionSource.DIRECT
        task.verification_status = (
            VerificationStatus.PENDING
            if task.verification_required
            else VerificationStatus.NOT_REQUIRED
        )
        _save(
            task,
            [
                "status",
                "completed_at",
                "completed_by",
                "completion_recorded_at",
                "completion_source",
                "verification_status",
            ],
        )
        sla.on_completed(task)  # stops RESOLUTION at the server-recorded completed_at
        rework_seconds = _close_open_rework(task, now)
        _audit(
            "task.completed",
            task,
            actor,
            old={"status": TaskStatus.IN_PROGRESS},
            new={
                "status": task.status,
                "completed_at": _iso(now),
                "verification_status": task.verification_status,
                "work_response_id": response.pk,
                "work_response_length": len(work_response),
            },
            extra={"rework_seconds": rework_seconds} if rework_seconds is not None else None,
        )
        dependencies.on_prerequisite_reached(task, DependencyState.COMPLETED, now, actor)
    return task


def _close_open_rework(task: Task, now) -> int | None:
    """After a rejected verification, record how long the rework took (R19)."""
    last = task.verifications.order_by("-cycle_no").first()
    if (
        last is None
        or last.decision != VerificationDecision.REJECTED
        or last.rework_seconds is not None
    ):
        return None
    seconds = max(0, int((now - last.decided_at).total_seconds()))
    TaskVerification.objects.filter(pk=last.pk).update(rework_seconds=seconds)
    return seconds


def block_task(*, actor, task: Task, version: int, reason: str) -> Task:
    with transaction.atomic():
        task = _lock(task, version)
        if task.status not in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
            raise InvalidTransition()
        if not (policy.is_assignee(actor, task) or policy.manages(actor, task)):
            raise TaskPermissionDenied()
        reason = _require_text(reason, "reason")
        old_status = task.status
        task.status = TaskStatus.BLOCKED
        task.blocked_reason = reason
        task.blocked_at = timezone.now()
        _save(task, ["status", "blocked_reason", "blocked_at"])
        _audit(
            "task.blocked",
            task,
            actor,
            old={"status": old_status},
            new={"status": task.status, "reason": reason},
        )
    return task


def unblock_task(*, actor, task: Task, version: int) -> Task:
    with transaction.atomic():
        task = _lock(task, version)
        if task.status != TaskStatus.BLOCKED:
            raise InvalidTransition()
        if not (policy.is_assignee(actor, task) or policy.manages(actor, task)):
            raise TaskPermissionDenied()
        # Back to where the work was: started tasks resume, unstarted ones wait again.
        task.status = TaskStatus.IN_PROGRESS if task.started_at else TaskStatus.PENDING
        old_reason = task.blocked_reason
        held_from = task.blocked_at  # read before it is cleared (HOLD rule: the SLA resumes)
        task.blocked_reason = ""
        task.blocked_at = None
        _save(task, ["status", "blocked_reason", "blocked_at"])
        _audit(
            "task.unblocked",
            task,
            actor,
            old={"status": TaskStatus.BLOCKED, "reason": old_reason},
            new={"status": task.status},
        )
        sla.on_unblocked(task, held_from, timezone.now(), actor=actor)
    return task


def cancel_task(*, actor, task: Task, version: int, reason: str) -> Task:
    with transaction.atomic():
        task = _lock(task, version)
        if task.status not in policy.OPEN_STATUSES:
            raise InvalidTransition()
        if not policy.can_cancel(actor, task):
            raise TaskPermissionDenied()
        reason = _require_text(reason, "reason")
        old_status = task.status
        task.status = TaskStatus.CANCELLED
        task.cancelled_reason = reason
        task.cancelled_at = timezone.now()
        _save(task, ["status", "cancelled_reason", "cancelled_at"])
        sla.on_cancelled(task, task.cancelled_at)
        _audit(
            "task.cancelled",
            task,
            actor,
            old={"status": old_status},
            new={"status": task.status, "reason": reason},
        )
    return task


def _verification_guard(actor, task: Task) -> None:
    waiting = (
        task.status == TaskStatus.COMPLETED
        and task.verification_status == VerificationStatus.PENDING
    )
    if not waiting:
        raise InvalidTransition("This task is not waiting for verification.")
    if not policy.manages(actor, task):
        raise TaskPermissionDenied()


def _next_cycle(task: Task) -> int:
    last = task.verifications.order_by("-cycle_no").first()
    return (last.cycle_no if last else 0) + 1


def verify_task(*, actor, task: Task, version: int, remarks: str = "") -> Task:
    with transaction.atomic():
        task = _lock(task, version)
        _verification_guard(actor, task)
        now = timezone.now()
        cycle = TaskVerification.objects.create(
            task=task,
            cycle_no=_next_cycle(task),
            submitted_at=task.completed_at,
            decision=VerificationDecision.VERIFIED,
            remarks=(remarks or "").strip(),
            decided_by=actor,
            decided_at=now,
        )
        task.verification_status = VerificationStatus.VERIFIED
        _save(task, ["verification_status"])
        _audit(
            "task.verified",
            task,
            actor,
            old={"verification_status": VerificationStatus.PENDING},
            new={"verification_status": task.verification_status, "cycle_no": cycle.cycle_no},
        )
        dependencies.on_prerequisite_reached(task, DependencyState.VERIFIED, now, actor)
    return task


def reject_verification(*, actor, task: Task, version: int, reason: str, remarks: str) -> Task:
    """Rejected work goes back to the SAME task (In Progress); no new task is created."""
    with transaction.atomic():
        task = _lock(task, version)
        _verification_guard(actor, task)
        reason = _require_text(reason, "reason")
        remarks = _require_text(remarks, "remarks")
        now = timezone.now()
        cycle = TaskVerification.objects.create(
            task=task,
            cycle_no=_next_cycle(task),
            submitted_at=task.completed_at,
            decision=VerificationDecision.REJECTED,
            rejection_reason=reason,
            remarks=remarks,
            decided_by=actor,
            decided_at=now,
        )
        old = {
            "status": task.status,
            "verification_status": task.verification_status,
            "completed_at": _iso(task.completed_at),
            "rework_count": task.rework_count,
        }
        task.status = TaskStatus.IN_PROGRESS
        task.verification_status = VerificationStatus.REJECTED
        task.rework_count += 1
        task.completed_at = None
        task.completed_by = None
        task.completion_recorded_at = None
        task.completion_source = None
        _save(
            task,
            [
                "status",
                "verification_status",
                "rework_count",
                "completed_at",
                "completed_by",
                "completion_recorded_at",
                "completion_source",
            ],
        )
        _audit(
            "task.verification_rejected",
            task,
            actor,
            old=old,
            new={
                "status": task.status,
                "verification_status": task.verification_status,
                "rework_count": task.rework_count,
                "cycle_no": cycle.cycle_no,
                "reason": reason,
                "remarks": remarks,
            },
        )
    return task


# --- Comments and attachments -----------------------------------------------------------------


def add_comment(*, actor, task: Task, body: str) -> TaskComment:
    if not policy.can_comment(actor, task):
        raise TaskPermissionDenied()
    body = _require_text(body, "body")
    with transaction.atomic():
        comment = TaskComment.objects.create(task=task, author=actor, body=body)
        _audit(
            "task.comment_added",
            task,
            actor,
            new={"comment_id": comment.pk, "length": len(body)},
        )
    return comment


def add_attachment(*, actor, task: Task, upload) -> TaskAttachment:
    from .attachments import inspect_upload  # local import keeps file handling isolated

    if not policy.can_comment(actor, task):
        raise TaskPermissionDenied()
    checked = inspect_upload(upload)
    with transaction.atomic():
        attachment = TaskAttachment.objects.create(
            task=task,
            uploaded_by=actor,
            file=upload,
            original_filename=checked.original_filename,
            size_bytes=checked.size_bytes,
            sha256=checked.sha256,
        )
        _audit(
            "task.attachment_added",
            task,
            actor,
            new={
                "attachment_id": attachment.pk,
                "original_filename": attachment.original_filename,
                "size_bytes": attachment.size_bytes,
                "sha256": attachment.sha256,
            },
        )
    return attachment


# --- Physical delete (Phase 4) ----------------------------------------------------------------


def _deletion_snapshot(task: Task) -> dict:
    return {
        "reference": task.reference,
        "title": task.title,
        "status": task.status,
        "source": task.source,
        "responsibility_id": task.responsibility_id,
        "schedule_id": task.schedule_id,
        "occurrence_date": task.occurrence_date.isoformat() if task.occurrence_date else None,
        "task_type": task.task_type,
        "priority": task.priority,
        "department_id": task.department_id,
        "category_id": task.category_id,
        "template_id": task.template_id,
        "created_by_id": task.created_by_id,
        "assigned_to_id": task.assigned_to_id,
        "assigned_by_id": task.assigned_by_id,
        "created_at": _iso(task.created_at),
        "received_at": _iso(task.received_at),
        "completed_at": _iso(task.completed_at),
        "verification_status": task.verification_status,
        "rework_count": task.rework_count,
        "version": task.version,
    }


def _remove_files(names: list[str], storage) -> None:
    for name in names:
        try:
            storage.delete(name)
        except Exception:  # a missing file must not hide that the task itself is gone
            logger.warning("Could not delete attachment file %s", name, exc_info=True)


def delete_task(*, actor, task: Task, version: int) -> None:
    """Physically delete a task and the records it owns (HR / Admin, any status).

    One transaction: the audit row (with a full snapshot, because the task row will be gone)
    is written first, notifications keep their text but lose their task/clock links, and the
    task-owned rows are deleted children-first. Every relation stays PROTECT, so a reference
    that was not handled makes the database refuse and the whole delete rolls back.
    Attachment files are removed from storage only after the transaction has committed.
    """
    with transaction.atomic():
        task = _lock(task, version)
        if not policy.can_delete(actor):
            raise TaskPermissionDenied("Only HR or Admin may delete tasks.")
        clocks = TaskSla.objects.filter(task=task)
        attachments = list(task.attachments.all())
        owned = {
            "assignments": task.assignments.count(),
            "verifications": task.verifications.count(),
            "comments": task.comments.count(),
            "attachments": len(attachments),
            "sla_clocks": clocks.count(),
        }
        # Phase 5: the occurrence ledger keeps the occurrence (so it is never regenerated) but
        # loses its link to the deleted task.
        occurrences = ScheduleOccurrence.objects.filter(task=task).update(task=None)
        unlinked = Notification.objects.filter(task=task).update(task=None, clock=None)
        unlinked += Notification.objects.filter(clock__in=clocks).update(task=None, clock=None)
        # Task Dependency Engine: links go with the task; its dependents keep waiting (D4).
        dependencies_unlinked = dependencies.unlink_for_delete(task)
        _audit(
            "task.deleted",
            task,
            actor,
            old=_deletion_snapshot(task),
            extra={
                "deleted_records": owned,
                "notifications_unlinked": unlinked,
                "occurrences_unlinked": occurrences,
                "dependencies_unlinked": dependencies_unlinked,
            },
        )
        clocks.delete()  # before assignments: an ACK clock references its assignment
        task.verifications.all().delete()
        task.comments.all().delete()
        storage = attachments[0].file.storage if attachments else None
        files = [a.file.name for a in attachments if a.file]
        task.attachments.all().delete()
        task.assignments.all().delete()
        task.delete()
        if files:
            transaction.on_commit(lambda: _remove_files(files, storage))


# --- Task categories (Admin-managed list, Phase 4) --------------------------------------------

CATEGORY_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")


def create_category(*, actor, code: str, name: str, is_active: bool = True) -> TaskCategory:
    code = (code or "").strip().upper()
    if not CATEGORY_CODE_RE.match(code):
        raise FieldValidationError(
            fields={"code": ["Use capital letters, digits or _, starting with a letter."]}
        )
    if TaskCategory.objects.filter(code=code).exists():
        raise ConflictError("A category with this code already exists.", code="category_code_taken")
    with transaction.atomic():
        category = TaskCategory.objects.create(
            code=code, name=_require_text(name, "name"), is_active=is_active
        )
        record(
            action="task_category.created",
            entity_type="task_category",
            entity_id=category.pk,
            actor=actor,
            new={"code": category.code, "name": category.name, "is_active": category.is_active},
        )
    return category


def update_category(*, actor, category: TaskCategory, **changes) -> TaskCategory:
    """Name and active flag only; codes never change. Categories are deactivated, not deleted."""
    if "code" in changes and (changes["code"] or "").strip().upper() != category.code:
        raise FieldValidationError(
            "Category codes cannot be changed.",
            code="field_immutable",
            fields={"code": ["Category codes cannot be changed."]},
        )
    with transaction.atomic():
        category = TaskCategory.objects.select_for_update().get(pk=category.pk)
        old, new = {}, {}
        for field in ("name", "is_active"):
            if field not in changes:
                continue
            value = _require_text(changes[field], "name") if field == "name" else changes[field]
            if value != getattr(category, field):
                old[field], new[field] = getattr(category, field), value
                setattr(category, field, value)
        if new:
            category.save(update_fields=[*new.keys(), "updated_at"])
            record(
                action="task_category.updated",
                entity_type="task_category",
                entity_id=category.pk,
                actor=actor,
                old=old,
                new=new,
            )
    return category
