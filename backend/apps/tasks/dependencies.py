"""Task Dependency Engine: a dependent task's SLA starts when its prerequisite task(s) reach the
required state. Reusable by every department; configured on task types, never linked by hand.

Configuration (TaskTemplate): `prerequisite_template` + `prerequisite_state` (COMPLETED or
VERIFIED). Approved decisions D1-D7:

Linking (link_new_task, called once for every new task, in either creation order)
- A task is linked to the tasks of the configured prerequisite type in the SAME DEPARTMENT on
  the SAME BUSINESS DATE: a scheduled task's occurrence date, otherwise the IST date it was
  created (the day the SLA engine already uses for a task).
- Only a dependent whose current RESOLUTION clock is waiting on the DEPENDENCY trigger is linked.
  Tasks whose SLA comes from elsewhere (the priority SLA of a manual task, a responsibility
  deadline) are never linked or changed (D7).
- Cancelled prerequisites are not linked. A prerequisite cancelled or deleted after linking never
  starts its dependent: the dependent keeps waiting (D4).
- A link is made once (unique pair); the reverse of an existing link is never made.

Satisfying (on_prerequisite_reached, called after completion and after verification)
- COMPLETED is satisfied only by completion, VERIFIED only by verification (D5).
- satisfied_at is written once and never changed: completing again after a rejected
  verification does nothing, and nothing is rolled back (D6).

Starting
- When EVERY link of a dependent is satisfied, its waiting clock starts at the later of the last
  satisfaction time and the dependent's original assignment time (D3), through
  sla.services.start_dependency_clock. An already started or stopped clock is never touched.

Concurrency: creations that could link to the same dependent task type serialise on that task
type's row; the dependent task row is locked before its links are satisfied, checked or extended;
the prerequisite row before its state is read. A task deleted meanwhile is skipped.
"""

from datetime import date, datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q

from apps.audit.services import record
from apps.core.timeutils import ist_datetime, to_ist
from apps.sla import services as sla
from apps.sla.models import ClockKind, TaskSla, Trigger

from .models import (
    DependencyState,
    Task,
    TaskDependency,
    TaskStatus,
    TaskTemplate,
    VerificationDecision,
    VerificationStatus,
)

OPEN = (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED)


def business_date(task: Task) -> date:
    """A scheduled task's occurrence date; otherwise the IST date the task was created."""
    if task.occurrence_date is not None:
        return task.occurrence_date
    return to_ist(task.created_at).date()


def _on_business_date(day: date) -> Q:
    start = ist_datetime(day, time(0, 0))
    end = start + timedelta(days=1)
    return Q(occurrence_date=day) | Q(
        occurrence_date__isnull=True, created_at__gte=start, created_at__lt=end
    )


def _waiting_clocks():
    """Current RESOLUTION clocks still waiting on the DEPENDENCY trigger."""
    return TaskSla.objects.filter(
        kind=ClockKind.RESOLUTION,
        is_current=True,
        trigger=Trigger.DEPENDENCY,
        start_at__isnull=True,
        stopped_at__isnull=True,
    )


def is_waiting(task: Task) -> bool:
    return _waiting_clocks().filter(task=task).exists()


def _audit(action: str, dependent: Task, actor, new: dict) -> None:
    record(
        action=action,
        entity_type="task",
        entity_id=dependent.pk,
        actor=actor,
        new=new,
        use_request_user=False,
        extra={"department_id": dependent.department_id, "source": "dependency_engine"},
    )


def _satisfied_at(prerequisite: Task, state: str) -> datetime | None:
    """When an existing prerequisite already reached `state`, else None."""
    if prerequisite.status != TaskStatus.COMPLETED:
        return None
    if state == DependencyState.COMPLETED:
        return prerequisite.completed_at
    if prerequisite.verification_status != VerificationStatus.VERIFIED:
        return None
    verified = (
        prerequisite.verifications.filter(decision=VerificationDecision.VERIFIED)
        .order_by("-cycle_no")
        .first()
    )
    return verified.decided_at if verified is not None else None


def _link(prerequisite: Task, dependent: Task, state: str, actor) -> TaskDependency | None:
    """Make one link (None if it exists already, or its reverse does)."""
    if TaskDependency.objects.filter(prerequisite=dependent, dependent=prerequisite).exists():
        return None
    satisfied_at = _satisfied_at(prerequisite, state)
    try:
        with transaction.atomic():
            link = TaskDependency.objects.create(
                prerequisite=prerequisite,
                dependent=dependent,
                required_state=state,
                satisfied_at=satisfied_at,
                created_by=actor,
            )
    except IntegrityError:  # the pair exists already (another run made it first)
        return None
    _audit(
        "task.dependency_linked",
        dependent,
        actor,
        {
            "prerequisite_id": prerequisite.pk,
            "required_state": state,
            "satisfied_at": satisfied_at.isoformat() if satisfied_at else None,
        },
    )
    return link


def _original_assignment_time(task: Task) -> datetime:
    first = task.assignments.order_by("assigned_at", "id").first()
    return first.assigned_at if first is not None else task.assigned_at


def _start_if_ready(dependent: Task, actor) -> TaskSla | None:
    """Start the dependent's waiting clock once every one of its links is satisfied (D3)."""
    times = list(
        TaskDependency.objects.filter(dependent=dependent).values_list("satisfied_at", flat=True)
    )
    if not times or any(at is None for at in times):
        return None
    start_at = max(max(times), _original_assignment_time(dependent))
    clock = sla.start_dependency_clock(dependent, start_at)
    if clock is not None:
        _audit(
            "task.dependency_sla_started",
            dependent,
            actor,
            {
                "clock_id": clock.pk,
                "rule_code": clock.rule_snapshot["code"],
                "start_at": clock.start_at.isoformat(),
                "due_at": clock.due_at.isoformat(),
            },
        )
    return clock


def _lock_task(pk: int) -> Task | None:
    """Lock a task row (None if it was deleted meanwhile)."""
    return Task.objects.select_for_update(no_key=True).filter(pk=pk).first()


def link_new_task(task: Task, actor) -> int:
    """Link a newly created task to its prerequisites and/or its waiting dependents, by
    task-type configuration. Returns the number of links made. A task without a task type costs
    no query; a task type in no dependency configuration costs one indexed query."""
    template = task.template
    if template is None:
        return 0
    # Task types that wait for this one, with the state each requires.
    dependent_states = dict(
        TaskTemplate.objects.filter(prerequisite_template_id=template.pk).values_list(
            "pk", "prerequisite_state"
        )
    )
    to_lock = set(dependent_states)
    if template.prerequisite_template_id is not None:
        to_lock.add(template.pk)
    if not to_lock:
        return 0
    # Serialise every creation that could link to the same dependent task type, so a
    # prerequisite and a dependent created at the same moment still see each other. NO KEY
    # UPDATE never blocks inserting tasks of that type (their foreign-key check), only this lock.
    list(
        TaskTemplate.objects.select_for_update(no_key=True)
        .filter(pk__in=to_lock)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    day = business_date(task)
    made = 0

    # The new task as a DEPENDENT of prerequisite tasks created before it.
    if template.prerequisite_template_id is not None and is_waiting(task):
        candidates = (
            Task.objects.filter(
                template_id=template.prerequisite_template_id, department_id=task.department_id
            )
            .filter(_on_business_date(day))
            .exclude(status=TaskStatus.CANCELLED)
            .exclude(pk=task.pk)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        for pk in list(candidates):
            prerequisite = _lock_task(pk)  # its state is read next
            if prerequisite is None or prerequisite.status == TaskStatus.CANCELLED:
                continue  # deleted or cancelled meanwhile
            if _link(prerequisite, task, template.prerequisite_state, actor) is not None:
                made += 1
        if made:
            _start_if_ready(task, actor)

    # The new task as a PREREQUISITE of dependents created before it and still waiting.
    if dependent_states:
        dependents = (
            Task.objects.filter(
                template_id__in=list(dependent_states),
                department_id=task.department_id,
                status__in=OPEN,
            )
            .filter(_on_business_date(day))
            .filter(Exists(_waiting_clocks().filter(task=OuterRef("pk"))))
            .exclude(pk=task.pk)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        for pk in list(dependents):
            dependent = _lock_task(pk)  # serialise with starters
            if dependent is None or not is_waiting(dependent):
                continue  # deleted, or started meanwhile: it no longer waits for anything
            if _link(task, dependent, dependent_states[dependent.template_id], actor) is not None:
                made += 1
    return made


def on_prerequisite_reached(task: Task, state: str, at: datetime, actor) -> int:
    """`task` reached `state` at `at`: satisfy its open links that require exactly this state
    (once each) and start every dependent whose links are now all satisfied. Returns the number
    of dependent clocks started. Calling it again changes nothing."""
    dependent_ids = sorted(
        set(
            TaskDependency.objects.filter(
                prerequisite=task, required_state=state, satisfied_at__isnull=True
            ).values_list("dependent_id", flat=True)
        )
    )
    started = 0
    for dependent_id in dependent_ids:
        dependent = _lock_task(dependent_id)
        if dependent is None:
            continue  # deleted meanwhile (its links went with it)
        link = (
            TaskDependency.objects.select_for_update()
            .filter(
                prerequisite=task,
                dependent=dependent,
                required_state=state,
                satisfied_at__isnull=True,
            )
            .first()
        )
        if link is None:
            continue  # satisfied by a concurrent call
        link.satisfied_at = at
        link.save(update_fields=["satisfied_at"])
        _audit(
            "task.dependency_satisfied",
            dependent,
            actor,
            {"prerequisite_id": task.pk, "required_state": state, "satisfied_at": at.isoformat()},
        )
        if _start_if_ready(dependent, actor) is not None:
            started += 1
    return started


def unlink_for_delete(task: Task) -> int:
    """Remove every link of a task that is being deleted (both directions). Its dependents keep
    waiting: nothing is started (D4). Returns the number of links removed."""
    deleted, _ = TaskDependency.objects.filter(Q(prerequisite=task) | Q(dependent=task)).delete()
    return deleted
