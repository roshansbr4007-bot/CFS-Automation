"""Automatic generation of responsibility work (Phase 5).

Called every minute by Celery Beat (recurring.generate_recurring_tasks) and by the
`generate_recurring_tasks` management command. It is idempotent: the occurrence ledger row
(unique schedule + business date) is written in the same transaction as the task, so any number
of runs, retries or concurrent workers produce at most one occurrence and one task.

Approved recovery rules:
- Daily: if the run time has passed today and today's occurrence does not exist yet, it is
  generated now (a late run still creates it once; the SLA engine starts its clocks at the
  scheduled time, so a late run never gives a fresh window). Fully missed earlier working days are
  recorded as MISSED, not generated (bounded look-back).
- Monthly: a missed occurrence is still generated when the scheduler resumes, for its resolved
  business date (the 20th, moved to the next working day when needed).
- Weekly (Phase B): each selected weekday's date (moved by the non-working-day policy) is
  generated on time; when the scheduler was down it is still generated late within one weekly
  cycle (7 days); older ones are recorded as MISSED, never backfilled. Two weekdays that move to
  the same business date give ONE occurrence.
- Specific date (ONCE, Phase B): generated once, on its business date after the run time, or
  late when the scheduler was down; never again once processed.
- No responsible employee: SKIPPED, audited and reported; never given to a guessed employee.
  If a valid owner is set later the SAME day (same-day correction), today's SKIPPED occurrence
  is recovered on its existing ledger row; earlier days and other skip reasons are not.
Manager login plays no part in any of this.

Arrival facts (approved S5-S7): the recurring.task_generated audit entry of every generated task
also records its scheduled time, when it arrived, whether its resolution and acknowledgment clocks
were already overdue on arrival, and the task type's own trigger. Written once, in the same
transaction as the task; the audit log is append-only, so they never change afterwards.
"""

import logging
from datetime import date, datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record
from apps.calendars.services import is_working_day, resolve_scheduled_date
from apps.core.timeutils import ist_datetime, to_ist
from apps.notifications.services import notify_schedule_warning
from apps.sla import services as sla
from apps.tasks.services import create_scheduled_task

from .models import Frequency, OccurrenceStatus, RecurringSchedule, ScheduleOccurrence
from .services import NO_OWNER_REASON, resolve_owner

logger = logging.getLogger(__name__)

SCHEDULER_EMAIL = "scheduler@cfs.system"
DAILY_LOOKBACK_DAYS = 31
MONTHLY_LOOKBACK_MONTHS = 12
WEEKLY_LOOKBACK_DAYS = 31  # Phase B: older weekly dates are neither generated nor recorded
WEEKLY_CATCH_UP_DAYS = 7  # Phase B (B-D3): generated late within one weekly cycle, else MISSED
WEEKLY_LOOKAHEAD_DAYS = 7  # Phase B: a later nominal date can move back to today (PREVIOUS)


def scheduler_user() -> User:
    return User.objects.get(email=SCHEDULER_EMAIL)


def _in_effect(schedule, day: date) -> bool:
    return schedule.effective_from <= day and (
        schedule.effective_to is None or day <= schedule.effective_to
    )


def _months_back(day: date, months: int) -> tuple[int, int]:
    index = day.year * 12 + (day.month - 1) - months
    return index // 12, index % 12 + 1


def due_occurrences(schedule, now: datetime) -> list[tuple[date, bool]]:
    """(business date, generate?) pairs not yet in the ledger and due by `now`.
    generate=False means: record as MISSED (a daily occurrence of an earlier day)."""
    if schedule.frequency == Frequency.WEEKLY:  # Phase B
        return _weekly_due(schedule, now)
    if schedule.frequency == Frequency.ONCE:  # Phase B
        return _once_due(schedule, now)
    local = to_ist(now)
    today = local.date()
    done = set(schedule.occurrences.values_list("occurrence_date", flat=True))
    due = []
    if schedule.frequency == Frequency.DAILY:
        start = max(schedule.effective_from, today - timedelta(days=DAILY_LOOKBACK_DAYS))
        day = start
        while day <= today:
            if day not in done and _in_effect(schedule, day) and is_working_day(day):
                if day < today:
                    due.append((day, False))
                elif local.time() >= schedule.run_time:
                    due.append((day, True))
            day += timedelta(days=1)
        return due
    year, month = _months_back(today, MONTHLY_LOOKBACK_MONTHS)
    while (year, month) <= (today.year, today.month):
        target = date(year, month, schedule.day_of_month)
        resolved = (
            resolve_scheduled_date(target, schedule.non_working_day_policy)
            if _in_effect(schedule, target)
            else None
        )
        on_time = resolved is not None and (
            resolved < today or (resolved == today and local.time() >= schedule.run_time)
        )
        if on_time and resolved not in done:
            due.append((resolved, True))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return due


def _weekly_due(schedule, now: datetime) -> list[tuple[date, bool]]:
    """Phase B. Nominal dates are the selected weekdays inside the effective window (checked on
    the nominal date, as for monthly). Each moves by the non-working-day policy; dates already in
    the ledger are skipped; two nominal dates that resolve to the same business date count once.
    Due when the business date is today (after the run time) or earlier; generated if within one
    weekly cycle, otherwise recorded as MISSED."""
    local = to_ist(now)
    today = local.date()
    done = set(schedule.occurrences.values_list("occurrence_date", flat=True))
    weekdays = set(schedule.weekdays or [])
    found: dict[date, bool] = {}
    day = today - timedelta(days=WEEKLY_LOOKBACK_DAYS)
    while day <= today + timedelta(days=WEEKLY_LOOKAHEAD_DAYS):
        if day.weekday() in weekdays and _in_effect(schedule, day):
            resolved = resolve_scheduled_date(day, schedule.non_working_day_policy)
            if resolved is not None and resolved not in done and resolved not in found:
                if resolved < today or (resolved == today and local.time() >= schedule.run_time):
                    if (today - resolved).days <= WEEKLY_LOOKBACK_DAYS:
                        found[resolved] = (today - resolved).days < WEEKLY_CATCH_UP_DAYS
        day += timedelta(days=1)
    return sorted(found.items())


def _once_due(schedule, now: datetime) -> list[tuple[date, bool]]:
    """Phase B. Only the chosen date, moved by the non-working-day policy. Due from its business
    date and run time onward (late if the scheduler was down) and never again once the ledger
    holds any occurrence for this schedule."""
    if schedule.run_date is None or schedule.occurrences.exists():
        return []
    local = to_ist(now)
    today = local.date()
    resolved = resolve_scheduled_date(schedule.run_date, schedule.non_working_day_policy)
    if resolved is None:
        return []
    if resolved < today or (resolved == today and local.time() >= schedule.run_time):
        return [(resolved, True)]
    return []


def _audit(action, occurrence, actor, *, new, extra=None):
    record(
        action=action,
        entity_type="schedule_occurrence",
        entity_id=occurrence.pk,
        actor=actor,
        new=new,
        use_request_user=False,
        extra={
            "source": "scheduler",
            "schedule_id": occurrence.schedule_id,
            "responsibility_id": occurrence.schedule.responsibility_id,
            "occurrence_date": occurrence.occurrence_date.isoformat(),
            **(extra or {}),
        },
    )


def _claim(schedule, day: date, status: str, **fields) -> ScheduleOccurrence | None:
    """Insert the ledger row; None if another run already holds this occurrence."""
    try:
        with transaction.atomic():
            return ScheduleOccurrence.objects.create(
                schedule=schedule, occurrence_date=day, status=status, **fields
            )
    except IntegrityError:
        return None


def _record_missed(schedule, day: date, scheduler) -> str:
    detail = "The scheduler did not run on this business day; not generated (daily backlog rule)."
    if schedule.frequency == Frequency.WEEKLY:  # Phase B
        detail = ("The scheduler did not run on this business day; not generated (older than one "
                  "weekly cycle).")
    with transaction.atomic():
        occurrence = _claim(schedule, day, OccurrenceStatus.MISSED, detail=detail)
        if occurrence is None:
            return "existing"
        _audit("recurring.occurrence_missed", occurrence, scheduler, new={"status": "MISSED"},
               extra={"detail": detail})
        notify_schedule_warning(occurrence, detail)
    return "missed"


def _generate(schedule, day: date, now: datetime, scheduler) -> str:
    owner, reason = resolve_owner(schedule.responsibility, day)
    scheduled_at = ist_datetime(day, schedule.run_time)
    delay_seconds = max(0, int((now - scheduled_at).total_seconds()))
    with transaction.atomic():
        if owner is None:
            occurrence = _claim(schedule, day, OccurrenceStatus.SKIPPED, detail=reason)
            if occurrence is None:
                return "existing"
            _audit("recurring.occurrence_skipped", occurrence, scheduler,
                   new={"status": "SKIPPED"}, extra={"detail": reason})
            notify_schedule_warning(occurrence, reason)
            return "skipped"
        occurrence = _claim(
            schedule, day, OccurrenceStatus.GENERATED, assignee=owner, generated_at=now
        )
        if occurrence is None:
            return "existing"
        task = create_scheduled_task(
            scheduler=scheduler,
            schedule=schedule,
            occurrence_date=day,
            assignee=owner,
            generated_at=now,
        )
        occurrence.task = task
        occurrence.save(update_fields=["task"])
        recovered = day < to_ist(now).date() or delay_seconds > 60
        _audit(
            "recurring.task_generated",
            occurrence,
            scheduler,
            new={"status": "GENERATED", "task_id": task.pk, "assigned_to_id": owner.pk},
            extra={"recovered": recovered, "delay_seconds": delay_seconds,
                   **sla.arrival_facts(task)},
        )
        if recovered:
            _audit(
                "recurring.occurrence_recovered",
                occurrence,
                scheduler,
                new={"task_id": task.pk},
                extra={"scheduled_at": scheduled_at.isoformat(), "delay_seconds": delay_seconds},
            )
    return "generated"


def _recover_skipped(occurrence, now: datetime, scheduler) -> bool:
    """Today's occurrence SKIPPED because nobody owned it: if an owner now resolves (a same-day
    correction), generate it on the SAME ledger row (no duplicate, history kept in the audit).
    The task's clocks start at the scheduled time (S1), so recovering late does not move the
    start or the deadline."""
    day = occurrence.occurrence_date
    if day != to_ist(now).date():
        return False  # only the current business date is ever recovered
    schedule = occurrence.schedule
    owner, _ = resolve_owner(schedule.responsibility, day)
    if owner is None:
        return False  # still nobody: stays SKIPPED
    with transaction.atomic():
        row = ScheduleOccurrence.objects.select_for_update().get(pk=occurrence.pk)
        if row.status != OccurrenceStatus.SKIPPED or row.detail != NO_OWNER_REASON:
            return False  # another run recovered it first
        task = create_scheduled_task(
            scheduler=scheduler,
            schedule=schedule,
            occurrence_date=day,
            assignee=owner,
            generated_at=now,
        )
        previous_reason = row.detail
        row.status = OccurrenceStatus.GENERATED
        row.task = task
        row.assignee = owner
        row.generated_at = now
        row.detail = f"Recovered after a same-day owner correction (was skipped: {previous_reason})"
        row.save(update_fields=["status", "task", "assignee", "generated_at", "detail"])
        scheduled_at = ist_datetime(day, schedule.run_time)
        delay_seconds = max(0, int((now - scheduled_at).total_seconds()))
        _audit(
            "recurring.task_generated",
            row,
            scheduler,
            new={"status": "GENERATED", "task_id": task.pk, "assigned_to_id": owner.pk},
            extra={"recovered": True, "delay_seconds": delay_seconds, **sla.arrival_facts(task)},
        )
        _audit(
            "recurring.occurrence_recovered",
            row,
            scheduler,
            new={"task_id": task.pk, "assigned_to_id": owner.pk},
            extra={
                "previous_status": "SKIPPED",
                "reason": previous_reason,
                "scheduled_at": scheduled_at.isoformat(),
                "delay_seconds": delay_seconds,
            },
        )
    return True


def _record_failure(schedule, day: date, scheduler, error: Exception) -> str:
    detail = f"Generation failed: {getattr(error, 'message', None) or error}"[:1000]
    with transaction.atomic():
        occurrence = _claim(schedule, day, OccurrenceStatus.FAILED, detail=detail)
        if occurrence is None:
            return "existing"
        _audit("recurring.occurrence_failed", occurrence, scheduler, new={"status": "FAILED"},
               extra={"detail": detail})
        notify_schedule_warning(occurrence, detail)
    return "failed"


def generate_due_occurrences(now: datetime | None = None) -> dict:
    """One pass of the generator. Safe to run any number of times."""
    now = now or timezone.now()
    today = to_ist(now).date()
    scheduler = scheduler_user()
    summary = {"generated": 0, "skipped": 0, "missed": 0, "failed": 0, "existing": 0}
    schedules = (
        RecurringSchedule.objects.filter(
            is_active=True, responsibility__is_active=True, effective_from__lte=today
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=today - timedelta(days=400)))
        .select_related("responsibility")
        .order_by("id")
    )
    for schedule in schedules:
        for day, generate in due_occurrences(schedule, now):
            if not generate:
                summary[_record_missed(schedule, day, scheduler)] += 1
                continue
            try:
                summary[_generate(schedule, day, now, scheduler)] += 1
            except Exception as error:  # recorded, audited and reported; never silently lost
                logger.exception("Generating %s for %s failed", schedule, day)
                summary[_record_failure(schedule, day, scheduler, error)] += 1
        skipped_today = schedule.occurrences.filter(
            occurrence_date=today, status=OccurrenceStatus.SKIPPED, detail=NO_OWNER_REASON
        )
        for occurrence in skipped_today:
            try:
                if _recover_skipped(occurrence, now, scheduler):
                    summary["generated"] += 1
            except Exception:  # the row stays SKIPPED (rolled back); retried on the next run
                logger.exception(
                    "Recovering %s for %s failed", schedule, occurrence.occurrence_date
                )
                summary["failed"] += 1
    return summary
