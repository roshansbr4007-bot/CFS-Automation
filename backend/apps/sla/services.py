"""SLA clocks: start, stop, describe and evaluate. Backend-only; React never calculates SLA.

Approved rules implemented here:
- Rules are versioned; the latest active version is snapshotted onto each clock.
- RESOLUTION clocks start by the template's trigger. A trigger that already happened that day
  starts the clock at the ORIGINAL trigger time (late-created tasks get no fresh window).
- Ad-hoc tasks (no template) have no RESOLUTION clock: "No SLA configured".
- ACK clock (ACK_2H) starts at assignment; reassignment closes it and starts a new one.
- On hold (task BLOCKED) pauses the RESOLUTION clock only (approved HOLD rule; the ACK clock keeps
  running): the checker skips it, the API shows it frozen at the hold moment, and on resume
  (on_unblocked) its start and deadline move forward by the time it was actually held, so the
  hold never counts against the SLA and the elapsed share is kept. Each hold is shifted on its
  own. Cancel stops every clock. Complete stops RESOLUTION at the server-recorded completed_at
  (MET/MISSED). Verification rejection starts no new clock.
- evaluate_clocks() is what `sla_tick` (and later Celery beat) calls. Each threshold is
  recorded once under a row lock; notifications are deduplicated by a unique key.
- Scheduled tasks (approved S1-S3): every clock of a task generated from a responsibility
  schedule starts at its SCHEDULED time (occurrence date + the schedule's run time, IST), labelled
  FIXED_TIME, whenever the task is generated and whoever signs in when. Only a task type that waits
  on the DEPENDENCY trigger keeps the dependency engine's start. Manual tasks are unchanged.
- An ACK clock of a scheduled task that was already overdue when it was created escalates its
  OVERDUE level to the employee only (approved C1/C2): HR and the reporting manager are not
  notified, and task.sla_escalation_suppressed records it once.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import NamedTuple

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.core.errors import ConflictError, FieldValidationError
from apps.core.timeutils import ist_datetime, to_ist
from apps.notifications import services as notifications
from apps.org.models import EmployeeDailyLogin
from apps.tasks.models import Task, TaskPriority, TaskStatus

from . import dispatch, engine
from .models import (
    ClockKind,
    Outcome,
    PrioritySla,
    RuleType,
    SlaRule,
    SlaSetting,
    SlaState,
    StopReason,
    TaskSla,
    Trigger,
)

logger = logging.getLogger(__name__)
ACK_RULE_CODE = "ACK_2H"
NO_SLA = "No SLA configured"
WORK_END_MISSING = "SLA inactive until company work_end is configured."
WAITING_UPSTREAM = "Not started — waiting for upstream task"
LEVELS = (
    ("WARNING", "warning_pct", "warning_at"),
    ("CRITICAL", "critical_pct", "critical_at"),
    ("OVERDUE", "overdue_pct", "overdue_at"),
)


# --- rules ------------------------------------------------------------------------------------


def active_rule(code: str) -> SlaRule | None:
    if not code:
        return None
    return SlaRule.objects.filter(code=code, is_active=True).order_by("-version").first()


def _rule_snapshot(rule: SlaRule) -> tuple[dict | None, str | None]:
    """Snapshot of a rule, or the reason it cannot run yet."""
    work_end = None
    if rule.rule_type == RuleType.END_OF_DAY:
        work_end = SlaSetting.load().company_work_end
        if work_end is None:
            return None, WORK_END_MISSING
    elif rule.rule_type != RuleType.DURATION:
        return None, f"{rule.name}: this rule type arrives in a later phase."
    return engine.snapshot(rule, work_end), None


def priority_rule(priority: str | None) -> SlaRule | None:
    """The active rule mapped to a task priority (Phase 5.2), or None when not configured."""
    if not priority:
        return None
    mapping = PrioritySla.objects.filter(priority=priority, is_active=True).first()
    return active_rule(mapping.rule_code) if mapping else None


def priority_plan(*, source, priority, template=None):
    """(rule, snapshot, note) when a task's priority has an active SLA mapping, else None.

    Phase 5.2: a MANUAL task's resolution clock uses that rule and starts at assignment.
    Change Set 1 (D4): so does a SCHEDULED task WITHOUT a task type (it had no SLA before). A
    scheduled task with a task type keeps that task type's SLA unchanged. Without a mapping the
    existing behaviour applies unchanged.
    """
    if source == "SCHEDULED" and template is not None:
        return None
    if source not in ("MANUAL", "SCHEDULED"):
        return None
    rule = priority_rule(priority)
    if rule is None:
        return None
    snap, reason = _rule_snapshot(rule)
    return rule, snap, reason


def responsibility_plan(task):
    """(rule, snapshot, note) when a SCHEDULED task's responsibility has a deadline (SLA) set by
    HR / Admin, else None. It takes precedence over the task type's SLA and the priority SLA for
    that scheduled task only; manual tasks never use it. The clock starts at the task's
    scheduled time (scheduled_start), like every clock of a generated task."""
    if task.source != "SCHEDULED" or task.responsibility_id is None:
        return None
    code = task.responsibility.deadline_rule_code
    rule = active_rule(code) if code else None
    if rule is None:
        return None
    snap, reason = _rule_snapshot(rule)
    return rule, snap, reason


def resolution_plan(template) -> tuple[SlaRule | None, dict | None, str | None]:
    if template is None:
        return None, None, NO_SLA
    rule = active_rule(template.resolution_rule_code)
    if rule is None:
        return None, None, NO_SLA
    snap, reason = _rule_snapshot(rule)
    return rule, snap, reason


# --- trigger times ----------------------------------------------------------------------------


def _task_day(created_at: datetime):
    return to_ist(created_at).date()


def _login_time(employee_id: int, day) -> datetime | None:
    # Only a valid business login (on a Company Calendar working day) starts a LOGIN clock.
    fact = EmployeeDailyLogin.objects.filter(
        employee_id=employee_id, work_date=day, is_valid=True
    ).first()
    return fact.first_login_at if fact else None


def trigger_start(template, *, assignee_id, created_at, assigned_at, trigger_at, now):
    """(start_at or None, waiting message or None) for a RESOLUTION clock."""
    day = _task_day(created_at)
    trigger = template.trigger
    if trigger == Trigger.ASSIGNMENT:
        return assigned_at, None
    if trigger == Trigger.EVENT:
        return trigger_at, None
    if trigger == Trigger.FIXED_TIME:
        return ist_datetime(day, template.fixed_time), None  # may be in the future
    if trigger == Trigger.LOGIN:
        login = _login_time(assignee_id, day)
        if login is not None:
            return login, None
        fallback = SlaSetting.load().login_fallback_time
        if fallback is not None and now >= ist_datetime(day, fallback):
            return ist_datetime(day, fallback), None
        return None, login_waiting_message(fallback)
    return None, WAITING_UPSTREAM  # DEPENDENCY: the dependency engine starts it later


def login_waiting_message(fallback) -> str:
    if fallback is None:
        return "Not started — waiting for the assignee's login (no fallback time configured)"
    return f"Not started — waiting for the assignee's login or {fallback.strftime('%H:%M')} IST"


# --- creating and stopping clocks -------------------------------------------------------------


def _new_clock(task, kind, rule, snap, trigger, start_at, assignment=None) -> TaskSla:
    due_at = engine.compute_due(snap, start_at) if start_at is not None else None
    return TaskSla.objects.create(
        task=task,
        kind=kind,
        rule=rule,
        rule_snapshot=snap,
        trigger=trigger,
        assignment=assignment,
        start_at=start_at,
        due_at=due_at,
    )


def start_ack_clock(
    task, assignment, start_at: datetime, trigger: str = Trigger.ASSIGNMENT
) -> TaskSla | None:
    rule = active_rule(ACK_RULE_CODE)
    if rule is None:
        return None
    snap, _ = _rule_snapshot(rule)
    return _new_clock(task, ClockKind.ACK, rule, snap, trigger, start_at, assignment)


def scheduled_time(task) -> datetime | None:
    """A generated (SCHEDULED) task's scheduled occurrence time: its occurrence date at the
    schedule's run time, IST. None for any other task."""
    if task.source != "SCHEDULED" or task.schedule_id is None or task.occurrence_date is None:
        return None
    return ist_datetime(task.occurrence_date, task.schedule.run_time)


def scheduled_start(task) -> datetime | None:
    """Where a generated task's clocks start: its scheduled time, never later than its
    assignment (every generation path only creates an occurrence that is already due, so this
    only guards against a clock starting in the future). None for any other task."""
    scheduled = scheduled_time(task)
    if scheduled is None or task.assigned_at is None:
        return scheduled
    return min(scheduled, task.assigned_at)


def on_task_created(task, assignment, now: datetime) -> None:
    # Approved S1-S3: a generated task's clocks start at its scheduled time (FIXED_TIME), not at
    # generation or login; manual tasks keep their assignment / trigger start exactly as before.
    scheduled = scheduled_start(task)
    start, label = (task.assigned_at, Trigger.ASSIGNMENT) if scheduled is None else (
        scheduled, Trigger.FIXED_TIME
    )
    if task.acknowledgment_required:
        start_ack_clock(task, assignment, start, label)
    by_responsibility = responsibility_plan(task)
    if by_responsibility is not None and by_responsibility[1] is not None:
        rule, snap, _ = by_responsibility
        _new_clock(task, ClockKind.RESOLUTION, rule, snap, label, start)
        return
    by_priority = priority_plan(source=task.source, priority=task.priority, template=task.template)
    if by_priority is not None:
        rule, snap, _ = by_priority
        if snap is not None:
            _new_clock(task, ClockKind.RESOLUTION, rule, snap, label, start)
        return
    rule, snap, _ = resolution_plan(task.template)
    if rule is None or snap is None:
        return  # No SLA configured / rule inactive: the API explains why
    if scheduled is not None and task.template.trigger != Trigger.DEPENDENCY:
        # Assignment, Login, Fixed-time and Event task types alike (S2): the scheduled time. A
        # later login can no longer start it (start_login_clocks only takes unstarted clocks).
        _new_clock(task, ClockKind.RESOLUTION, rule, snap, Trigger.FIXED_TIME, scheduled)
        return
    start_at, _ = trigger_start(
        task.template,
        assignee_id=task.assigned_to_id,
        created_at=task.created_at,
        assigned_at=task.assigned_at,
        trigger_at=task.trigger_at,
        now=now,
    )
    _new_clock(task, ClockKind.RESOLUTION, rule, snap, task.template.trigger, start_at)


def overdue_on_arrival(clock: TaskSla | None) -> bool | None:
    """Was this clock already past its overdue threshold when it was created? None when there is
    no clock or it had not started (e.g. still waiting on its dependency)."""
    if clock is None or clock.start_at is None or clock.due_at is None:
        return None
    pct = engine.elapsed_pct(clock.start_at, clock.due_at, clock.created_at)
    return engine.state_for(pct, clock.rule_snapshot) == SlaState.OVERDUE


def arrival_facts(task) -> dict:
    """The facts recorded once, when a scheduled task is generated (approved S5-S7), in its
    recurring.task_generated audit entry. Computed from the clocks just created, so a later hold,
    resume, reassignment or completion can never change them."""
    clocks = {c.kind: c for c in TaskSla.objects.filter(task=task, is_current=True)}
    scheduled = scheduled_time(task)
    return {
        "scheduled_at": scheduled.isoformat() if scheduled else None,
        "arrived_at": task.assigned_at.isoformat() if task.assigned_at else None,
        "resolution_overdue_on_arrival": overdue_on_arrival(clocks.get(ClockKind.RESOLUTION)),
        "ack_overdue_on_arrival": overdue_on_arrival(clocks.get(ClockKind.ACK)),
        "template_trigger": task.template.trigger if task.template_id else None,
    }


def ack_escalation_suppressed(clock: TaskSla, level: str) -> bool:
    """Approved C1: the OVERDUE level of a scheduled task's own ACK clock (created with it, so
    labelled FIXED_TIME; a reassignment's or a manual task's ACK clock never is) that was ALREADY
    overdue when the clock was created. Only HR and the reporting manager are left out; the
    employee is still notified. An ACK clock that becomes overdue after arrival escalates."""
    return (
        level == "OVERDUE"
        and clock.kind == ClockKind.ACK
        and clock.trigger == Trigger.FIXED_TIME
        and clock.task.source == "SCHEDULED"
        and overdue_on_arrival(clock) is True
    )


def _current(task, kind) -> TaskSla | None:
    return (
        TaskSla.objects.select_for_update()
        .filter(task=task, kind=kind, is_current=True)
        .first()
    )


def _stop(clock: TaskSla, at: datetime, reason: str, *, judge: bool, keep_current=True) -> None:
    if clock.stopped_at is not None:
        return
    clock.stopped_at = at
    clock.stop_reason = reason
    clock.is_current = keep_current
    if clock.start_at is not None and at >= clock.start_at:
        pct = engine.elapsed_pct(clock.start_at, clock.due_at, at)
        clock.state = engine.state_for(pct, clock.rule_snapshot)
        if judge:
            clock.outcome = engine.outcome_for(at, clock.due_at)
    clock.save(update_fields=["stopped_at", "stop_reason", "is_current", "state", "outcome"])


def on_acknowledged(task, at: datetime) -> None:
    clock = _current(task, ClockKind.ACK)
    if clock is not None:
        # keep_current=False: an acknowledged clock is finished; a later reassignment must be
        # able to create the new assignee's current ACK clock (one current clock per kind).
        _stop(clock, at, StopReason.ACKNOWLEDGED, judge=True, keep_current=False)


def on_completed(task) -> None:
    clock = _current(task, ClockKind.RESOLUTION)
    if clock is not None:
        _stop(clock, task.completed_at, StopReason.COMPLETED, judge=True)
        if clock.outcome == Outcome.MISSED and clock.overdue_at is None:
            # Completed late before the checker recorded the overdue threshold (Phase 9).
            _announce_overdue(clock, "COMPLETION")


def on_cancelled(task, at: datetime) -> None:
    for kind in (ClockKind.ACK, ClockKind.RESOLUTION):
        clock = _current(task, kind)
        if clock is not None:
            _stop(clock, at, StopReason.CANCELLED, judge=False)


def on_reassigned(task, assignment, at: datetime) -> None:
    """ACK restarts for the new assignee; the RESOLUTION clock is never reset."""
    old = _current(task, ClockKind.ACK)
    if old is not None:
        _stop(old, at, StopReason.REASSIGNED, judge=False, keep_current=False)
    if task.acknowledgment_required:
        start_ack_clock(task, assignment, assignment.assigned_at)


def on_acknowledgment_requirement_changed(task, assignment, at: datetime) -> None:
    old = _current(task, ClockKind.ACK)
    if old is not None:
        _stop(old, at, StopReason.NOT_REQUIRED, judge=False, keep_current=False)
    if task.acknowledgment_required:
        start_ack_clock(task, assignment, at)


def on_unblocked(task, held_from: datetime | None, at: datetime, *, actor=None) -> TaskSla | None:
    """HOLD rule: the task was on hold from `held_from` until `at`. Move the current RESOLUTION
    clock's start and deadline forward by the part of that hold during which the clock was
    actually running (from the later of the hold start and the clock start), so its duration and
    elapsed share are unchanged and the hold never counts. A clock that is stopped, has not
    started, or only starts after `at` is left as it is. Audited as task.sla_resumed."""
    if held_from is None:
        return None
    clock = _current(task, ClockKind.RESOLUTION)
    if clock is None or clock.stopped_at is not None or clock.start_at is None:
        return None
    held = at - max(held_from, clock.start_at)
    if held <= timedelta(0):
        return None
    old = {"start_at": clock.start_at.isoformat(), "due_at": clock.due_at.isoformat()}
    clock.start_at += held
    clock.due_at += held
    clock.save(update_fields=["start_at", "due_at"])
    held_seconds = int(held.total_seconds())
    record(
        action="task.sla_resumed",
        entity_type="task",
        entity_id=task.pk,
        actor=actor,
        old=old,
        new={
            "start_at": clock.start_at.isoformat(),
            "due_at": clock.due_at.isoformat(),
            "held_seconds": held_seconds,
        },
        use_request_user=actor is None,
        extra={
            "department_id": task.department_id,
            "clock_id": clock.pk,
            "held_from": held_from.isoformat(),
            "held_seconds": held_seconds,
        },
    )
    return clock


def start_login_clocks(employee_id: int, login_at: datetime) -> int:
    """Called on the employee's login: start their waiting LOGIN clocks for that IST day."""
    day = to_ist(login_at).date()
    first_login = _login_time(employee_id, day)
    if first_login is None:  # not a valid business login (non-working day): nothing starts
        return 0
    started = 0
    with transaction.atomic():
        clocks = TaskSla.objects.select_for_update().filter(
            kind=ClockKind.RESOLUTION,
            trigger=Trigger.LOGIN,
            is_current=True,
            stopped_at__isnull=True,
            start_at__isnull=True,
            task__assigned_to_id=employee_id,
        )
        for clock in clocks:
            if _task_day(clock.task.created_at) != day:
                continue
            _begin(clock, first_login)
            started += 1
    return started


def _begin(clock: TaskSla, start_at: datetime) -> None:
    clock.start_at = start_at
    clock.due_at = engine.compute_due(clock.rule_snapshot, start_at)
    clock.save(update_fields=["start_at", "due_at"])


def start_dependency_clock(task, at: datetime) -> TaskSla | None:
    """Task Dependency Engine: start a dependent task's waiting RESOLUTION clock at `at` (the
    engine decides the time). Only the current clock that has the DEPENDENCY trigger, has not
    started and has not stopped is started, through _begin(), so its deadline comes from its own
    rule snapshot and its trigger stays DEPENDENCY; the existing checker takes it from there.
    Anything else is left exactly as it is and None is returned, so a repeated call never
    restarts or moves a clock."""
    with transaction.atomic():
        clock = (
            TaskSla.objects.select_for_update()
            .filter(
                task=task,
                kind=ClockKind.RESOLUTION,
                is_current=True,
                trigger=Trigger.DEPENDENCY,
                start_at__isnull=True,
                stopped_at__isnull=True,
            )
            .first()
        )
        if clock is None:
            return None
        _begin(clock, at)
    return clock


# --- describing clocks for the API ------------------------------------------------------------


def held_message(held_since: datetime) -> str:
    return f"On hold since {to_ist(held_since).strftime('%H:%M')} IST — SLA paused"


def describe(
    clock: TaskSla | None, now: datetime, held_since: datetime | None = None
) -> dict | None:
    """`held_since`: the task is on hold since then (RESOLUTION clocks only, see task_sla). A
    started, running clock is then shown paused: elapsed share and state frozen at the moment
    the hold reached it, no remaining time, and the deadline it would have if resumed now."""
    if clock is None:
        return None
    snap = clock.rule_snapshot
    data = {
        "kind": clock.kind,
        "rule_code": snap["code"],
        "rule_name": snap["name"],
        "rule_version": snap["version"],
        "rule_type": snap["rule_type"],
        "clock": snap["clock"],
        "trigger": clock.trigger,
        "duration_minutes": snap["duration_minutes"],
        "start_at": clock.start_at,
        "due_at": clock.due_at,
        "state": SlaState.NOT_STARTED,
        "elapsed_pct": None,
        "remaining_seconds": None,
        "warning_at": clock.warning_at,
        "critical_at": clock.critical_at,
        "overdue_at": clock.overdue_at,
        "stopped_at": clock.stopped_at,
        "stop_reason": clock.stop_reason or None,
        "outcome": clock.outcome,
        "waiting_for": None,
    }
    if clock.start_at is None:
        data["waiting_for"] = _waiting(clock)
        return data
    if clock.due_at and clock.start_at and clock.due_at > clock.start_at:
        data["duration_minutes"] = int((clock.due_at - clock.start_at).total_seconds() // 60)
    if held_since is not None and clock.stopped_at is None:
        frozen = max(held_since, clock.start_at)
        pct = engine.elapsed_pct(clock.start_at, clock.due_at, frozen)
        data["elapsed_pct"] = round(pct, 1)
        data["state"] = engine.state_for(pct, snap)
        data["due_at"] = clock.due_at + max(now - frozen, timedelta(0))
        data["waiting_for"] = held_message(held_since)
        return data
    at = clock.stopped_at or now
    if at < clock.start_at:
        if clock.stopped_at is None:
            data["waiting_for"] = f"Starts at {to_ist(clock.start_at).strftime('%H:%M')} IST"
        return data
    pct = engine.elapsed_pct(clock.start_at, clock.due_at, at)
    data["elapsed_pct"] = round(pct, 1)
    data["state"] = clock.state if clock.stopped_at else engine.state_for(pct, snap)
    data["remaining_seconds"] = int((clock.due_at - at).total_seconds())
    return data


def _waiting(clock: TaskSla) -> str:
    if clock.trigger == Trigger.LOGIN:
        return login_waiting_message(SlaSetting.load().login_fallback_time)
    return WAITING_UPSTREAM


def task_sla(task, now: datetime) -> dict:
    """The read-only `sla` block of the task API."""
    clocks: dict[str, TaskSla] = {}
    for clock in sorted(task.sla_clocks.all(), key=lambda c: (c.created_at, c.pk)):
        clocks[clock.kind] = clock  # the latest clock of each kind wins
    resolution = clocks.get(ClockKind.RESOLUTION)
    note = None
    if resolution is None:
        _, _, note = resolution_plan(task.template)
        note = note or NO_SLA
    held_since = task.blocked_at if task.status == TaskStatus.BLOCKED else None
    return {
        "resolution": describe(resolution, now, held_since),
        "resolution_note": note,
        "acknowledgment": describe(clocks.get(ClockKind.ACK), now),
    }


def preview(
    *, template, assignee, acknowledgment_required, trigger_at, now, priority=None
) -> dict:
    """What the clocks WOULD be if the task were created now (nothing is saved). The preview is
    for manually raised tasks, so a configured priority rule wins exactly as at creation."""
    result = {"resolution": None, "resolution_note": None, "acknowledgment": None}
    if acknowledgment_required:
        ack_rule = active_rule(ACK_RULE_CODE)
        if ack_rule is not None:
            snap, _ = _rule_snapshot(ack_rule)
            result["acknowledgment"] = _preview_clock(
                ClockKind.ACK, Trigger.ASSIGNMENT, snap, now, now
            )
    by_priority = priority_plan(source="MANUAL", priority=priority)
    if by_priority is not None:
        rule, snap, note = by_priority
        if snap is None:
            result["resolution_note"] = note or NO_SLA
        else:
            result["resolution"] = _preview_clock(
                ClockKind.RESOLUTION, Trigger.ASSIGNMENT, snap, now, now
            )
        return result
    rule, snap, note = resolution_plan(template)
    if rule is None or snap is None:
        result["resolution_note"] = note or NO_SLA
        return result
    if template.trigger == Trigger.EVENT and trigger_at is None:
        result["resolution_note"] = "Enter the event time to see the SLA."
        return result
    start_at, waiting = trigger_start(
        template,
        assignee_id=assignee.pk if assignee else None,
        created_at=now,
        assigned_at=now,
        trigger_at=trigger_at,
        now=now,
    )
    result["resolution"] = _preview_clock(
        ClockKind.RESOLUTION, template.trigger, snap, start_at, now
    )
    if waiting:
        result["resolution"]["waiting_for"] = waiting
    return result


def _preview_clock(kind, trigger, snap, start_at, now) -> dict:
    clock = TaskSla(kind=kind, trigger=trigger, rule_snapshot=snap, start_at=start_at)
    if start_at is not None:
        clock.due_at = engine.compute_due(snap, start_at)
    return describe(clock, now)


# --- the checker ------------------------------------------------------------------------------


def start_fallback_login_clocks(now: datetime) -> int:
    fallback = SlaSetting.load().login_fallback_time
    if fallback is None:
        return 0
    started = 0
    waiting = TaskSla.objects.filter(
        kind=ClockKind.RESOLUTION,
        trigger=Trigger.LOGIN,
        is_current=True,
        stopped_at__isnull=True,
        start_at__isnull=True,
    ).values_list("id", flat=True)
    for pk in list(waiting):
        with transaction.atomic():
            clock = (
                TaskSla.objects.select_for_update(skip_locked=True, of=("self",))
                .filter(pk=pk, start_at__isnull=True, stopped_at__isnull=True)
                .select_related("task")
                .first()
            )
            if clock is None:
                continue
            day = _task_day(clock.task.created_at)
            login = _login_time(clock.task.assigned_to_id, day)
            fallback_at = ist_datetime(day, fallback)
            if login is not None:
                _begin(clock, login)
            elif now >= fallback_at:
                _begin(clock, fallback_at)
            else:
                continue
            started += 1
    return started


def _announce_overdue(clock: TaskSla, source: str) -> None:
    """Phase 9: tell listeners a RESOLUTION clock is overdue. send_robust: a listener's error
    is logged and can never break, block or roll back the SLA transition."""
    for receiver, result in dispatch.resolution_clock_overdue.send_robust(
        sender=TaskSla, clock=clock, source=source
    ):
        if isinstance(result, Exception):
            logger.error("Overdue listener %r failed for clock %s: %r", receiver, clock.pk, result)


def _on_hold(task_id: int) -> bool:
    return Task.objects.filter(pk=task_id, status=TaskStatus.BLOCKED).exists()


class ClockResult(NamedTuple):
    levels: list[str]
    escalation_gaps: int


def evaluate_clock(pk: int, now: datetime) -> ClockResult:
    """Record every threshold this running clock has reached, each exactly once."""
    reached: list[str] = []
    gaps = 0
    with transaction.atomic():
        clock = (
            TaskSla.objects.select_for_update(skip_locked=True)
            .filter(pk=pk, stopped_at__isnull=True, start_at__lte=now)
            .first()
        )
        if clock is None:
            return ClockResult(reached, gaps)
        if clock.kind == ClockKind.RESOLUTION and _on_hold(clock.task_id):
            return ClockResult(reached, gaps)  # paused: no threshold while the task is on hold
        snap = clock.rule_snapshot
        pct = engine.elapsed_pct(clock.start_at, clock.due_at, now)
        fields = ["state"]
        for level, pct_key, field in LEVELS:
            if pct >= snap[pct_key] and getattr(clock, field) is None:
                at = engine.threshold_instant(clock.start_at, clock.due_at, snap[pct_key])
                setattr(clock, field, at)
                fields.append(field)
                reached.append(level)
        clock.state = engine.state_for(pct, snap)
        clock.save(update_fields=fields)
        for level in reached:
            suppressed = ack_escalation_suppressed(clock, level)
            # A suppressed escalation never looks up the reporting manager, so it can never
            # report a missing recipient either.
            boss_gap = notifications.notify_threshold(clock, level, escalate=not suppressed)
            if boss_gap:
                gaps += 1
                record(
                    action="task.sla_escalation_recipient_missing",
                    entity_type="task",
                    entity_id=clock.task_id,
                    new={"clock": clock.kind, "level": level, "reason": boss_gap},
                    use_request_user=False,
                    extra={"department_id": clock.task.department_id},
                )
            record(
                action="task.sla_threshold_reached",
                entity_type="task",
                entity_id=clock.task_id,
                new={"clock": clock.kind, "level": level, "clock_id": clock.pk},
                use_request_user=False,
                extra={"department_id": clock.task.department_id},
            )
            if suppressed:
                # Exactly once: this branch runs only when overdue_at was just set, under the
                # row lock taken above (approved C2). Ids and times only, no personal data.
                record(
                    action="task.sla_escalation_suppressed",
                    entity_type="task",
                    entity_id=clock.task_id,
                    new={
                        "clock": clock.kind,
                        "clock_id": clock.pk,
                        "level": level,
                        "reason": "ACK_OVERDUE_ON_ARRIVAL",
                        # BOSS = settings.SLA_BOSS_RESOLVER (default: the reporting manager).
                        "suppressed_recipients": ["HR", "BOSS"],
                        "clock_created_at": clock.created_at.isoformat(),
                        "overdue_threshold_at": clock.overdue_at.isoformat(),
                    },
                    use_request_user=False,
                    extra={"department_id": clock.task.department_id},
                )
        if "OVERDUE" in reached and clock.kind == ClockKind.RESOLUTION:
            _announce_overdue(clock, "TICK")  # Phase 9: exactly once per clock (overdue_at)
    return ClockResult(reached, gaps)


def evaluate_clocks(now: datetime | None = None) -> dict:
    """One pass of the SLA checker. Idempotent: safe to run any number of times."""
    now = now or timezone.now()
    started = start_fallback_login_clocks(now)
    running = list(
        TaskSla.objects.filter(
            stopped_at__isnull=True, start_at__lte=now, overdue_at__isnull=True
        )
        .exclude(kind=ClockKind.RESOLUTION, task__status=TaskStatus.BLOCKED)  # paused (HOLD)
        .order_by("id")
        .values_list("id", flat=True)
    )
    results = [evaluate_clock(pk, now) for pk in running]
    emails = notifications.send_pending_emails()
    return {
        "login_clocks_started": started,
        "thresholds": sum(len(r.levels) for r in results),
        "escalation_gaps": sum(r.escalation_gaps for r in results),
        "emails_sent": emails,
    }


# --- Admin configuration (audited) ------------------------------------------------------------

RULE_FIELDS = ("name", "clock", "duration_minutes", "warning_pct", "critical_pct", "overdue_pct")


def supersede_rule(*, actor, rule: SlaRule, **changes) -> SlaRule:
    """Rules are never edited: a change creates the next version and retires the old one.
    Clocks already running keep their snapshot, so existing deadlines never move."""
    with transaction.atomic():
        current = SlaRule.objects.select_for_update().get(pk=rule.pk)
        if not current.is_active:
            raise ConflictError(
                "Only the active version of a rule can be superseded.", code="rule_not_active"
            )
        values = {field: changes.get(field, getattr(current, field)) for field in RULE_FIELDS}
        if values["clock"] != "CALENDAR":
            raise FieldValidationError(
                fields={"clock": ["Business-hours clocks arrive with the calendar engine."]}
            )
        if current.rule_type == RuleType.DURATION and not values["duration_minutes"]:
            raise FieldValidationError(fields={"duration_minutes": ["Required for this rule."]})
        if not 0 < values["warning_pct"] < values["critical_pct"] < values["overdue_pct"]:
            raise FieldValidationError(
                fields={"warning_pct": ["Use 0 < warning < critical < overdue."]}
            )
        current.is_active = False
        current.save(update_fields=["is_active"])
        new = SlaRule.objects.create(
            code=current.code,
            version=current.version + 1,
            rule_type=current.rule_type,
            supersedes=current,
            created_by=actor,
            **values,
        )
        record(
            action="sla.rule_superseded",
            entity_type="sla_rule",
            entity_id=new.pk,
            actor=actor,
            old={"version": current.version, **{f: getattr(current, f) for f in RULE_FIELDS}},
            new={"version": new.version, **values},
            extra={"code": new.code},
        )
    return new


def update_settings(*, actor, **changes) -> SlaSetting:
    with transaction.atomic():
        setting = SlaSetting.objects.select_for_update().get(pk=SlaSetting.load().pk)
        old, new = {}, {}
        for field in ("company_work_end", "login_fallback_time"):
            if field in changes and changes[field] != getattr(setting, field):
                old[field] = _hhmm(getattr(setting, field))
                new[field] = _hhmm(changes[field])
                setattr(setting, field, changes[field])
        if new:
            setting.save()
            record(
                action="sla.settings_updated",
                entity_type="sla_setting",
                entity_id=setting.pk,
                actor=actor,
                old=old,
                new=new,
            )
    return setting


def _hhmm(value):
    return value.strftime("%H:%M") if value else None


# --- Phase 5.2: duration rules and priority mapping (Admin, sla.manage_sla_rules) ------------

RULE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")


def create_rule(
    *, actor, code, name, duration_minutes, warning_pct=50, critical_pct=75, overdue_pct=100
) -> SlaRule:
    """A new calendar-time DURATION rule (version 1). Later changes go through supersede_rule."""
    code = (code or "").strip().upper()
    if not RULE_CODE_RE.match(code):
        raise FieldValidationError(
            fields={"code": ["Use capital letters, digits or _, starting with a letter."]}
        )
    if SlaRule.objects.filter(code=code).exists():
        raise ConflictError("A rule with this code already exists.", code="rule_code_taken")
    if not duration_minutes or duration_minutes <= 0:
        raise FieldValidationError(fields={"duration_minutes": ["Must be more than 0."]})
    if not 0 < warning_pct < critical_pct < overdue_pct:
        raise FieldValidationError(
            fields={"warning_pct": ["Use 0 < warning < critical < overdue."]}
        )
    name = (name or "").strip()
    if not name:
        raise FieldValidationError(fields={"name": ["This field is required."]})
    with transaction.atomic():
        rule = SlaRule.objects.create(
            code=code,
            version=1,
            name=name,
            rule_type=RuleType.DURATION,
            clock="CALENDAR",
            duration_minutes=duration_minutes,
            warning_pct=warning_pct,
            critical_pct=critical_pct,
            overdue_pct=overdue_pct,
            created_by=actor,
        )
        record(
            action="sla.rule_created",
            entity_type="sla_rule",
            entity_id=rule.pk,
            actor=actor,
            new={"code": code, "name": name, "duration_minutes": duration_minutes},
        )
    return rule


def set_priority_rule(
    *, actor, priority: str, rule_code: str | None = None, is_active: bool = True
):
    """Map a task priority to a DURATION rule (or switch the mapping off). Existing clocks keep
    their snapshot: only tasks raised afterwards use the new mapping."""
    if priority not in TaskPriority.values:
        raise FieldValidationError(fields={"priority": ["Unknown priority."]})
    rule_code = (rule_code or "").strip().upper()
    if is_active:
        rule = active_rule(rule_code) if rule_code else None
        if rule is None or rule.rule_type != RuleType.DURATION:
            raise FieldValidationError(
                fields={"rule_code": ["Choose an active DURATION rule."]}
            )
    with transaction.atomic():
        mapping = PrioritySla.objects.select_for_update().filter(priority=priority).first()
        old = (
            {"rule_code": mapping.rule_code, "is_active": mapping.is_active} if mapping else None
        )
        if mapping is None:
            if not rule_code:
                raise FieldValidationError(fields={"rule_code": ["This field is required."]})
            mapping = PrioritySla(priority=priority)
        if rule_code:
            mapping.rule_code = rule_code
        mapping.is_active = is_active
        mapping.updated_by = actor
        mapping.save()
        record(
            action="sla.priority_rule_set",
            entity_type="sla_priority_rule",
            entity_id=mapping.pk,
            actor=actor,
            old=old,
            new={"rule_code": mapping.rule_code, "is_active": mapping.is_active},
            extra={"priority": priority},
        )
    return mapping
