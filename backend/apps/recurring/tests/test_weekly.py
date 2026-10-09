"""Phase B — WEEKLY schedules through the real generator (Celery path: generate_due_occurrences).

Seeded calendar: Sundays off; 1st and 3rd Saturdays working. 10 Oct 2026 is the 2nd Saturday
(off): NEXT -> Mon 12 Oct, PREVIOUS -> Fri 9 Oct.
"""

from datetime import date, datetime, time

import pytest
import time_machine

from apps.core.timeutils import IST
from apps.org.models import Department
from apps.recurring import generator
from apps.recurring.models import RecurringSchedule, Responsibility, ScheduleOccurrence
from apps.sla import services as sla_services
from apps.sla.models import TaskSla
from apps.tasks.models import Task, TaskAssignment, TaskCategory

pytestmark = pytest.mark.django_db


def D(day, month=10):  # noqa: N802
    return date(2026, month, day)


def _run(*at):
    with time_machine.travel(datetime(*at, tzinfo=IST), tick=False):
        return generator.generate_due_occurrences()


@pytest.fixture
def weekly(ops, own):
    """weekly(weekdays, start=..., end=None, policy="SKIP"): a weekly duty owned by Rahul."""
    counter = iter(range(1, 100))

    def _weekly(weekdays, *, start=D(1), end=None, policy="SKIP"):
        responsibility = Responsibility.objects.create(
            code=f"WEEKLY_{next(counter)}", name="Weekly report",
            department=Department.objects.get(code="OPS"),
            category=TaskCategory.objects.filter(is_active=True).first(), priority="LOW",
        )
        own(responsibility, ops["rahul_emp"])
        return RecurringSchedule.objects.create(
            responsibility=responsibility, title="Weekly report", frequency="WEEKLY",
            weekdays=weekdays, run_time=time(10, 0), non_working_day_policy=policy,
            effective_from=start, effective_to=end,
        )

    return _weekly


def _ledger(schedule):
    return list(schedule.occurrences.order_by("occurrence_date")
                .values_list("occurrence_date", "status"))


def test_one_weekday_generates_a_task_with_full_metadata(weekly, ops):
    schedule = weekly([2])  # Wednesday
    _run(2026, 10, 7, 10, 0)
    assert _ledger(schedule) == [(D(7), "GENERATED")]
    task = Task.objects.get(schedule=schedule)
    assert (task.source, task.responsibility_id, task.occurrence_date) == (
        "SCHEDULED", schedule.responsibility_id, D(7),
    )
    assert task.assigned_to == ops["rahul_emp"] and task.template_id is None
    assert TaskAssignment.objects.filter(task=task, to_employee=ops["rahul_emp"]).exists()


def test_multiple_weekdays_run_time_and_one_cycle_rule(weekly):
    schedule = weekly([0, 3])  # Monday + Thursday
    _run(2026, 10, 8, 9, 59)  # Thursday before the run time
    assert _ledger(schedule) == [(D(1), "MISSED"), (D(5), "GENERATED")]  # 7 days old -> MISSED
    _run(2026, 10, 8, 10, 0)
    assert _ledger(schedule)[-1] == (D(8), "GENERATED")
    assert Task.objects.filter(schedule=schedule).count() == 2


@pytest.mark.parametrize(("policy", "expected"), [
    ("SKIP", []),
    ("NEXT_WORKING_DAY", [(D(12), "GENERATED")]),
    ("PREVIOUS_WORKING_DAY", [(D(9), "GENERATED")]),
])
def test_non_working_day_policies(weekly, policy, expected):
    schedule = weekly([5], start=D(6), policy=policy)  # Saturday; 10 Oct is off
    _run(2026, 10, 12, 10, 0)
    assert _ledger(schedule) == expected


def test_weekdays_resolving_to_the_same_date_give_one_occurrence(weekly):
    schedule = weekly([0, 5, 6], start=D(6), policy="NEXT_WORKING_DAY")  # Sat, Sun, Mon -> Mon
    _run(2026, 10, 12, 10, 0)
    assert _ledger(schedule) == [(D(12), "GENERATED")]
    assert Task.objects.filter(schedule=schedule).count() == 1


def test_effective_window_is_inclusive(weekly):
    starts_monday = weekly([0], start=D(5))
    ends_monday = weekly([0], start=D(1), end=D(5))
    _run(2026, 10, 8, 10, 0)
    assert _ledger(starts_monday) == [(D(5), "GENERATED")]
    assert _ledger(ends_monday) == [(D(5), "GENERATED")]
    _run(2026, 10, 12, 10, 0)
    assert _ledger(ends_monday) == [(D(5), "GENERATED")]  # 12 Oct is after the end


def test_older_than_one_cycle_is_missed_never_backfilled(weekly):
    schedule = weekly([0], start=D(1, 9))
    _run(2026, 10, 12, 10, 0)
    assert _ledger(schedule) == [
        (D(14, 9), "MISSED"), (D(21, 9), "MISSED"), (D(28, 9), "MISSED"), (D(5), "MISSED"),
        (D(12), "GENERATED"),
    ]  # 31-day look-back: 7 Sep is neither generated nor recorded
    assert Task.objects.filter(schedule=schedule).count() == 1
    missed = ScheduleOccurrence.objects.filter(schedule=schedule, status="MISSED").first()
    assert "weekly cycle" in missed.detail


def test_idempotent_and_concurrent_claims_are_safe(weekly):
    schedule = weekly([0, 3])
    _run(2026, 10, 8, 10, 0)
    before = _ledger(schedule)
    _run(2026, 10, 8, 10, 0)
    _run(2026, 10, 8, 10, 5)
    assert _ledger(schedule) == before and Task.objects.filter(schedule=schedule).count() == 2
    assert generator._claim(schedule, D(8), "GENERATED") is None  # a second worker loses


def test_an_inactive_responsibility_generates_nothing(weekly):
    schedule = weekly([0, 3])
    Responsibility.objects.filter(pk=schedule.responsibility_id).update(is_active=False)
    _run(2026, 10, 8, 10, 0)
    assert _ledger(schedule) == []


def test_priority_sla_applies_when_there_is_no_task_type(weekly, admin_user):
    sla_services.create_rule(actor=admin_user, code="WEEKLY_LOW_72H", name="Low",
                             duration_minutes=72 * 60)
    sla_services.set_priority_rule(actor=admin_user, priority="LOW", rule_code="WEEKLY_LOW_72H")
    schedule = weekly([2])
    _run(2026, 10, 7, 10, 0)
    clock = TaskSla.objects.get(task__schedule=schedule, kind="RESOLUTION")
    assert clock.rule_snapshot["code"] == "WEEKLY_LOW_72H"
