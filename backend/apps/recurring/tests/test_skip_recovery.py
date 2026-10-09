"""Same-day recovery of a SKIPPED daily activity after an owner correction (regression).

Production sequence that went wrong: 10:00 run -> no owner -> SKIPPED; owner set from today;
re-run -> the SKIPPED ledger row blocked recovery, so the day's activity never appeared."""

from datetime import date

import pytest
import time_machine

from apps.audit.models import AuditLog
from apps.recurring import generator
from apps.recurring.models import RecurringSchedule, ScheduleOccurrence
from apps.recurring.services import NO_OWNER_REASON, assign_owner
from apps.sla.models import TaskSla
from apps.tasks.models import Task

pytestmark = pytest.mark.django_db


def _run(ist, *at):
    with time_machine.travel(ist(*at), tick=False):
        return generator.generate_due_occurrences()


def _feed_occurrence(day=date(2026, 10, 5)):
    return ScheduleOccurrence.objects.get(
        schedule__responsibility__code="FEED_UPLOAD", occurrence_date=day
    )


def _make_owner(admin_user, ops, resp, ist, *at, start=date(2026, 10, 5)):
    with time_machine.travel(ist(*at), tick=False):
        assign_owner(actor=admin_user, responsibility=resp("FEED_UPLOAD"),
                     employee=ops["rahul_emp"], effective_from=start)


def test_same_day_owner_correction_recovers_the_skipped_activity(admin_user, ops, resp, ist):
    _run(ist, 2026, 10, 5, 10, 0)  # nobody owns Feed Upload yet
    skipped = _feed_occurrence()
    assert skipped.status == "SKIPPED" and skipped.detail == NO_OWNER_REASON
    _make_owner(admin_user, ops, resp, ist, 2026, 10, 5, 10, 20)

    assert _run(ist, 2026, 10, 5, 16, 50)["generated"] == 1
    recovered = _feed_occurrence()
    assert recovered.pk == skipped.pk  # the same ledger row, not a new occurrence
    assert recovered.status == "GENERATED" and recovered.assignee == ops["rahul_emp"]
    task = Task.objects.get(responsibility__code="FEED_UPLOAD")
    assert recovered.task == task and task.assigned_to == ops["rahul_emp"]
    assert task.source == "SCHEDULED" and task.occurrence_date == date(2026, 10, 5)
    assert ScheduleOccurrence.objects.filter(schedule=skipped.schedule).count() == 1
    row = AuditLog.objects.get(action="recurring.occurrence_recovered", entity_id=str(skipped.pk))
    assert row.context["previous_status"] == "SKIPPED"
    assert row.context["reason"] == NO_OWNER_REASON
    assert row.new_value == {"task_id": task.pk, "assigned_to_id": ops["rahul_emp"].pk}


def test_recovery_keeps_the_10_oclock_start_and_deadline(admin_user, ops, resp, ist):
    _run(ist, 2026, 10, 5, 10, 0)
    _make_owner(admin_user, ops, resp, ist, 2026, 10, 5, 10, 20)
    _run(ist, 2026, 10, 5, 16, 50)
    task = Task.objects.get(responsibility__code="FEED_UPLOAD")
    clock = TaskSla.objects.get(task=task, kind="RESOLUTION")
    assert clock.trigger == "FIXED_TIME"
    assert clock.start_at == ist(2026, 10, 5, 10, 0)  # not 16:50
    assert clock.due_at == ist(2026, 10, 5, 12, 0)
    assert _feed_occurrence().generated_at == ist(2026, 10, 5, 16, 50)  # when it was recovered


def test_a_previous_days_skip_is_never_recovered(admin_user, ops, resp, ist):
    _run(ist, 2026, 10, 5, 10, 0)  # Monday skipped
    _make_owner(admin_user, ops, resp, ist, 2026, 10, 6, 9, 0, start=date(2026, 10, 6))
    _run(ist, 2026, 10, 6, 10, 0)  # Tuesday
    monday = _feed_occurrence(date(2026, 10, 5))
    assert monday.status == "SKIPPED" and monday.task is None
    assert _feed_occurrence(date(2026, 10, 6)).status == "GENERATED"
    assert Task.objects.filter(responsibility__code="FEED_UPLOAD").count() == 1


def test_a_skip_for_another_reason_is_not_recovered(admin_user, ops, resp, ist):
    feed = RecurringSchedule.objects.get(responsibility__code="FEED_UPLOAD")
    ScheduleOccurrence.objects.create(
        schedule=feed, occurrence_date=date(2026, 10, 5), status="SKIPPED",
        detail="The responsible employee (Old Owner) is inactive.",
    )
    _make_owner(admin_user, ops, resp, ist, 2026, 10, 5, 9, 0)
    _run(ist, 2026, 10, 5, 16, 50)
    occurrence = _feed_occurrence()
    assert occurrence.status == "SKIPPED" and occurrence.task is None
    assert not Task.objects.filter(responsibility__code="FEED_UPLOAD").exists()


def test_still_no_owner_stays_skipped(ist):
    _run(ist, 2026, 10, 5, 10, 0)
    assert _run(ist, 2026, 10, 5, 16, 50)["generated"] == 0
    assert _feed_occurrence().status == "SKIPPED"
    assert not AuditLog.objects.filter(action="recurring.occurrence_recovered").exists()


def test_a_generated_activity_stays_idempotent(admin_user, ops, resp, ist):
    _make_owner(admin_user, ops, resp, ist, 2026, 10, 5, 9, 0)
    _run(ist, 2026, 10, 5, 10, 0)
    first = _feed_occurrence()
    for minute in (1, 2):
        _run(ist, 2026, 10, 5, 16, 50 + minute)
    assert ScheduleOccurrence.objects.filter(schedule=first.schedule).count() == 1
    assert Task.objects.filter(responsibility__code="FEED_UPLOAD").count() == 1
    assert _feed_occurrence().task_id == first.task_id
