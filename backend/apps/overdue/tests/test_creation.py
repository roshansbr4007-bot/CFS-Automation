"""Phase 9: when exactly one overdue case opens (SLA integration), and what it records."""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.overdue import receivers, reports, services
from apps.overdue.models import OverdueCase
from apps.sla import services as sla_services
from apps.sla.models import TaskSla
from apps.tasks.models import TaskTemplate

pytestmark = pytest.mark.django_db


def _resolution(task):
    return TaskSla.objects.get(task=task, kind="RESOLUTION")


def test_only_the_overdue_transition_opens_one_case_with_locked_facts(ops, work, ist):
    task = work.raise_task(ops["rahul_emp"], 2026, 10, 5, 10, 0)
    for minute in (20, 35, 50):  # on track, warning, critical: no case
        work.tick(2026, 10, 5, 10, minute)
        assert not OverdueCase.objects.exists()
    work.tick(2026, 10, 5, 11, 0)
    case = OverdueCase.objects.get()
    clock = _resolution(task)
    assert (case.clock, case.task, case.status, case.opened_via) == (clock, task, "OPEN", "TICK")
    assert case.employee == ops["rahul_emp"] and case.department.code == "OPS"
    assert case.task_creator == ops["manager"] and case.priority == "HIGH"
    assert (case.task_title, case.sla_rule_code) == ("Map RM codes", "OVERDUE_TEST_60M")
    assert case.task_assigned_at == ist(2026, 10, 5, 10, 0)
    assert case.sla_start_at == ist(2026, 10, 5, 10, 0)
    assert case.sla_due_at == ist(2026, 10, 5, 11, 0)
    assert case.overdue_at == clock.overdue_at == ist(2026, 10, 5, 11, 0)
    assert (case.reason_category, case.cause) == ("", "")  # no cause until a reviewer sets one


def test_repeated_checker_runs_and_restarts_never_duplicate(ops, work, ist):
    task = work.raise_task(ops["rahul_emp"], 2026, 10, 5, 10, 0)
    for minute in (0, 1, 2):
        work.tick(2026, 10, 5, 11, minute)
    sla_services.evaluate_clock(_resolution(task).pk, ist(2026, 10, 5, 11, 5))  # a re-run
    assert OverdueCase.objects.count() == 1
    assert services.open_case_for_clock(_resolution(task), source="TICK") is None  # idempotent
    assert OverdueCase.objects.count() == 1


def test_the_acknowledgement_clock_never_opens_a_case(ops, work, ist):
    broker = TaskTemplate.objects.get(code="BROKER_MAPPING")  # ACK 2 h, resolution 24 h
    task = work.raise_task(ops["rahul_emp"], 2026, 10, 5, 10, 0, template=broker,
                           priority="MEDIUM")
    work.tick(2026, 10, 5, 12, 30)
    assert TaskSla.objects.get(task=task, kind="ACK").overdue_at is not None
    assert not OverdueCase.objects.exists()


def test_completing_late_before_the_checker_saw_it_opens_a_case(ops, work, ist):
    task = work.raise_task(ops["rahul_emp"], 2026, 10, 5, 10, 0)
    work.complete(task, ops["rahul"], 2026, 10, 5, 11, 20)  # no checker run in between
    case = OverdueCase.objects.get()
    clock = _resolution(task)
    assert (clock.outcome, clock.overdue_at) == ("MISSED", None)  # SLA behaviour unchanged
    assert (case.opened_via, case.overdue_at) == ("COMPLETION", ist(2026, 10, 5, 11, 0))
    assert reports.overdue_minutes(case, now=ist(2026, 10, 9, 0, 0)) == 20  # frozen at completion
    work.tick(2026, 10, 5, 11, 30)
    assert OverdueCase.objects.count() == 1


def test_completing_on_time_opens_nothing(ops, work):
    task = work.raise_task(ops["rahul_emp"], 2026, 10, 5, 10, 0)
    work.complete(task, ops["rahul"], 2026, 10, 5, 10, 50)
    work.tick(2026, 10, 5, 11, 30)
    assert not OverdueCase.objects.exists()


def test_cancellation_before_the_case_opens_none_and_an_open_case_remains(ops, work):
    late = work.raise_task(ops["rahul_emp"], 2026, 10, 5, 10, 0)
    work.cancel(late, 2026, 10, 5, 11, 10)  # past the deadline, before any checker run
    work.tick(2026, 10, 5, 11, 15)
    assert not OverdueCase.objects.exists()
    other = work.raise_task(ops["rahul_emp"], 2026, 10, 5, 12, 0)
    work.tick(2026, 10, 5, 13, 0)
    work.cancel(other, 2026, 10, 5, 13, 10)
    assert OverdueCase.objects.get().task == other  # stays after cancellation


def test_clocks_overdue_before_phase_9_are_not_backfilled(ops, work, ist):
    task = work.raise_task(ops["rahul_emp"], 2026, 10, 5, 10, 0)
    # A clock the checker had already recorded as overdue before this feature existed.
    TaskSla.objects.filter(task=task, kind="RESOLUTION").update(
        overdue_at=ist(2026, 10, 5, 11, 0), state="OVERDUE"
    )
    work.tick(2026, 10, 5, 11, 30)
    work.complete(task, ops["rahul"], 2026, 10, 5, 12, 0)  # late, but already recorded
    assert not OverdueCase.objects.exists()


def test_a_failure_never_breaks_the_sla_transition(ops, work, monkeypatch, ist):
    def broken(clock, *, source):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(services, "open_case_for_clock", broken)
    task = work.raise_task(ops["rahul_emp"], 2026, 10, 5, 10, 0)
    result = work.tick(2026, 10, 5, 11, 0)
    clock = _resolution(task)
    assert clock.overdue_at == ist(2026, 10, 5, 11, 0) and clock.state == "OVERDUE"
    assert result["thresholds"] == 3  # warning, critical, overdue all recorded as before
    assert Notification.objects.filter(kind="SLA_OVERDUE").exists()  # existing alerts unchanged
    assert not OverdueCase.objects.exists()
    failure = AuditLog.objects.get(action="overdue_case.open_failed")
    assert failure.new_value == {"clock_id": clock.pk, "source": "TICK"}
    assert receivers.on_resolution_clock_overdue  # the listener stays connected


def test_reassignment_after_the_breach_keeps_the_case_employee(ops, work):
    task = work.raise_task(ops["rahul_emp"], 2026, 10, 5, 10, 0)
    work.tick(2026, 10, 5, 11, 0)
    work.reassign(task, ops["amit_emp"], 2026, 10, 5, 11, 30)
    assert OverdueCase.objects.get().employee == ops["rahul_emp"]


# --- repair command (manual; never a backfill) --------------------------------------------------


@pytest.fixture
def missing_case(ops, work, monkeypatch, ist):
    """A clock that became overdue on 5 Oct 11:00 whose case failed to open."""
    monkeypatch.setattr(services, "phase9_deployed_at", lambda: ist(2026, 10, 1, 0, 0))
    original = services.open_case_for_clock

    def broken(clock, *, source):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(services, "open_case_for_clock", broken)
    task = work.raise_task(ops["rahul_emp"], 2026, 10, 5, 10, 0)
    work.tick(2026, 10, 5, 11, 0)
    monkeypatch.setattr(services, "open_case_for_clock", original)
    assert not OverdueCase.objects.exists()
    return task


def _repair(*args):
    out = StringIO()
    call_command("open_missing_overdue_cases", *args, stdout=out)
    return out.getvalue()


def test_repair_recovers_missing_cases_inside_the_window(missing_case):
    assert "1 missing case(s)" in _repair("--since", "2026-10-05", "--dry-run")
    assert not OverdueCase.objects.exists()  # dry run changes nothing
    assert "1 missing case(s) opened" in _repair("--since", "2026-10-05")
    assert OverdueCase.objects.get().opened_via == "REPAIR"
    assert "0 missing case(s) opened" in _repair("--since", "2026-10-05")  # idempotent


def test_repair_never_reaches_outside_its_window(missing_case):
    assert "0 missing case(s) opened" in _repair("--since", "2026-10-06")  # breach was 5 Oct
    with pytest.raises(CommandError):  # before Phase 9 was deployed (1 Oct in this test)
        _repair("--since", "2026-09-30")
    with pytest.raises(CommandError):
        _repair("--since", "yesterday")
    assert not OverdueCase.objects.exists()


def test_deployment_time_is_read_from_the_migration_record():
    assert services.phase9_deployed_at() is not None
