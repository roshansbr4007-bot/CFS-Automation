"""Task Dependency Engine with scheduled responsibilities: the generator creates both tasks (in
either order), the engine links them by occurrence date, Brokerage Calculation's completion starts
Reconciliation's 24 h SLA, and a responsibility deadline still wins (frozen R-D2)."""

from datetime import date, datetime, time

import pytest
import time_machine

from apps.core.timeutils import IST
from apps.org.models import Department
from apps.overdue.models import OverdueCase
from apps.recurring import generator
from apps.recurring import services as recurring
from apps.recurring.tests.conftest import own  # noqa: F401  (shared fixture)
from apps.sla import services
from apps.sla.models import TaskSla
from apps.tasks import services as task_services
from apps.tasks.models import Task, TaskCategory, TaskDependency, TaskTemplate

pytestmark = pytest.mark.django_db


def _at(*parts):
    return time_machine.travel(datetime(*parts, tzinfo=IST), tick=False)


def _clock(task):
    return TaskSla.objects.get(task=task, kind="RESOLUTION", is_current=True)


def _complete(task, user):
    task.refresh_from_db()
    task_services.start_task(actor=user, task=task, version=task.version)
    task.refresh_from_db()
    return task_services.complete_task(actor=user, task=task, version=task.version)


def _task(responsibility, day):
    return Task.objects.get(responsibility=responsibility, occurrence_date=day)


@pytest.fixture
def duty(admin_user, ops, own):  # noqa: F811
    """duty(code, template_code, start=5 Oct 2026): a daily 10:00 OPS duty owned by Rahul."""

    def _duty(code, template_code, start=date(2026, 10, 5)):
        r = recurring.create_responsibility(
            actor=admin_user, code=code, name=code.title(),
            department=Department.objects.get(code="OPS"),
            category=TaskCategory.objects.get(code="OPERATIONS"),
            template=TaskTemplate.objects.get(code=template_code), priority="LOW",
        )
        own(r, ops["rahul_emp"])
        recurring.create_schedule(
            actor=admin_user, responsibility=r, title=code.title(), frequency="DAILY",
            run_time=time(10, 0), effective_from=start,
        )
        return r

    return _duty


@pytest.mark.parametrize("reconciliation_first", [False, True])
def test_reconciliation_waits_for_brokerage_calculation_end_to_end(
    duty, ops, ist, reconciliation_first
):
    if reconciliation_first:  # the generator then creates Reconciliation first
        recon_duty = duty("RECON_DUTY", "RECONCILIATION")
        brokerage_duty = duty("BROKERAGE_DUTY", "BROKERAGE_CALCULATION")
    else:
        brokerage_duty = duty("BROKERAGE_DUTY", "BROKERAGE_CALCULATION")
        recon_duty = duty("RECON_DUTY", "RECONCILIATION")
    with _at(2026, 10, 5, 10, 0):
        generator.generate_due_occurrences()
    brokerage = _task(brokerage_duty, date(2026, 10, 5))
    recon = _task(recon_duty, date(2026, 10, 5))
    link = TaskDependency.objects.get()
    assert (link.prerequisite, link.dependent, link.required_state) == (
        brokerage, recon, "COMPLETED"
    )
    waiting = _clock(recon)
    assert (waiting.trigger, waiting.start_at, waiting.rule_snapshot["code"]) == (
        "DEPENDENCY", None, "RECON_24H"
    )

    with _at(2026, 10, 5, 11, 30):
        _complete(brokerage, ops["rahul"])
    clock = _clock(recon)
    assert clock.pk == waiting.pk and clock.trigger == "DEPENDENCY"
    assert (clock.start_at, clock.due_at) == (ist(2026, 10, 5, 11, 30), ist(2026, 10, 6, 11, 30))

    with _at(2026, 10, 6, 11, 31):  # the existing checker and Phase 9 take over unchanged
        services.evaluate_clocks()
    clock.refresh_from_db()
    assert clock.state == "OVERDUE"
    assert OverdueCase.objects.filter(clock=clock, status="OPEN").count() == 1


def test_scheduled_tasks_match_on_their_occurrence_date(duty, ops, ist):
    brokerage_duty = duty("BROKERAGE_DUTY", "BROKERAGE_CALCULATION")
    recon_duty = duty("RECON_DUTY", "RECONCILIATION", start=date(2026, 10, 6))
    with _at(2026, 10, 5, 10, 0):
        generator.generate_due_occurrences()
    with _at(2026, 10, 5, 12, 0):
        _complete(_task(brokerage_duty, date(2026, 10, 5)), ops["rahul"])  # yesterday's work
    with _at(2026, 10, 6, 10, 0):
        generator.generate_due_occurrences()
    recon = _task(recon_duty, date(2026, 10, 6))
    link = TaskDependency.objects.get()
    assert (link.prerequisite, link.dependent) == (_task(brokerage_duty, date(2026, 10, 6)), recon)
    assert link.satisfied_at is None and _clock(recon).start_at is None


def test_a_manual_prerequisite_on_the_same_business_date_also_counts(
    duty, ops, new_task, ist
):
    recon_duty = duty("RECON_DUTY", "RECONCILIATION")
    with _at(2026, 10, 5, 10, 0):
        generator.generate_due_occurrences()
    recon = _task(recon_duty, date(2026, 10, 5))
    with _at(2026, 10, 5, 14, 0):
        brokerage = new_task(ops["manager"], ops["rahul_emp"],
                             template=TaskTemplate.objects.get(code="BROKERAGE_CALCULATION"))
    with _at(2026, 10, 5, 15, 0):
        _complete(brokerage, ops["rahul"])
    assert TaskDependency.objects.get().prerequisite == brokerage
    assert _clock(recon).start_at == ist(2026, 10, 5, 15, 0)


def test_a_responsibility_deadline_still_wins_over_the_dependency(duty, admin_user, ops, ist):
    brokerage_duty = duty("BROKERAGE_DUTY", "BROKERAGE_CALCULATION")
    recon_duty = duty("RECON_DUTY", "RECONCILIATION")
    recon_duty.refresh_from_db()
    recurring.update_responsibility(
        actor=admin_user, responsibility=recon_duty, version=recon_duty.version,
        deadline_minutes=120,
    )
    with _at(2026, 10, 5, 10, 0):
        generator.generate_due_occurrences()
    recon = _task(recon_duty, date(2026, 10, 5))
    clock = _clock(recon)
    assert (clock.trigger, clock.rule_snapshot["code"]) == ("ASSIGNMENT", f"RESP_{recon_duty.pk}")
    assert (clock.start_at, clock.due_at) == (ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 12, 0))
    assert not TaskDependency.objects.exists()
    before = list(TaskSla.objects.filter(task=recon).values())
    with _at(2026, 10, 5, 11, 0):
        _complete(_task(brokerage_duty, date(2026, 10, 5)), ops["rahul"])
    assert list(TaskSla.objects.filter(task=recon).values()) == before
