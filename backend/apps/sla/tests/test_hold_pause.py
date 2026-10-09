"""Approved HOLD rule: while a task is on hold (BLOCKED) its RESOLUTION clock is paused; on resume
its start and deadline move forward by the time it was actually held. The ACK clock never pauses.
"""

from datetime import date, datetime, time, timedelta
from io import StringIO

import pytest
import time_machine
from django.core.management import call_command

from apps.audit.models import AuditLog
from apps.core.timeutils import IST
from apps.notifications.models import Notification
from apps.org.models import Department
from apps.overdue.models import OverdueCase
from apps.recurring import generator
from apps.recurring import services as recurring
from apps.recurring.tests.conftest import own  # noqa: F401  (shared fixture)
from apps.sla import services
from apps.sla.models import TaskSla
from apps.tasks import monitoring
from apps.tasks import services as task_services
from apps.tasks.models import Task, TaskCategory, TaskTemplate

pytestmark = pytest.mark.django_db
TASKS = "/api/v1/tasks/"


def _at(*parts):
    return time_machine.travel(datetime(*parts, tzinfo=IST), tick=False)


def _clock(task, kind="RESOLUTION"):
    return TaskSla.objects.get(task=task, kind=kind, is_current=True)


def _block(task, user, *at):
    with _at(*at):
        task.refresh_from_db()
        return task_services.block_task(
            actor=user, task=task, version=task.version, reason="Waiting for documents"
        )


def _unblock(task, user, *at):
    with _at(*at):
        task.refresh_from_db()
        return task_services.unblock_task(actor=user, task=task, version=task.version)


def _tick(*at):
    with _at(*at):
        return services.evaluate_clocks()


def _complete(task, user, *at):
    with _at(*at):
        task.refresh_from_db()
        if task.status == "PENDING":
            task_services.start_task(actor=user, task=task, version=task.version)
            task.refresh_from_db()
        return task_services.complete_task(actor=user, task=task, version=task.version)


def _resolution(client, task, *at):
    with _at(*at):
        return client.get(f"{TASKS}{task.pk}/").json()["sla"]["resolution"]


def _sla_notifications():
    return Notification.objects.filter(kind__startswith="SLA_").count()


@pytest.fixture
def feed(ops, new_task, template):
    """Feed Upload raised at 10:00 for Rahul: task-type SLA, fixed 10:00 start, 2 h (12:00)."""
    with _at(2026, 10, 5, 10, 0):
        return new_task(ops["manager"], ops["rahul_emp"], template=template("FEED_UPLOAD"))


# --- 1, 12, 15, 16: resume keeps the elapsed share and moves the deadline ----------------------


def test_a_task_type_sla_resumes_with_the_same_share_and_a_later_deadline(ops, feed, ist):
    _block(feed, ops["rahul"], 2026, 10, 5, 11, 0)  # 50% elapsed
    _unblock(feed, ops["rahul"], 2026, 10, 5, 14, 0)  # held 3 h
    clock = _clock(feed)
    assert (clock.start_at, clock.due_at) == (ist(2026, 10, 5, 13, 0), ist(2026, 10, 5, 15, 0))
    assert clock.due_at - clock.start_at == timedelta(hours=2)  # the SLA duration is kept
    with _at(2026, 10, 5, 14, 0):
        assert services.describe(clock, datetime.now(IST))["elapsed_pct"] == 50.0
    resumed = AuditLog.objects.get(action="task.sla_resumed")
    assert resumed.entity_id == str(feed.pk) and resumed.actor_user == ops["rahul"]
    def times(values):
        return {key: datetime.fromisoformat(values[key]) for key in ("start_at", "due_at")}

    assert times(resumed.old_value) == {
        "start_at": ist(2026, 10, 5, 10, 0), "due_at": ist(2026, 10, 5, 12, 0),
    }
    assert times(resumed.new_value) == {
        "start_at": ist(2026, 10, 5, 13, 0), "due_at": ist(2026, 10, 5, 15, 0),
    }
    assert resumed.new_value["held_seconds"] == 3 * 3600
    assert resumed.context["clock_id"] == clock.pk and resumed.context["held_seconds"] == 10800


def test_the_existing_block_and_unblock_audit_is_unchanged(ops, feed):
    _block(feed, ops["rahul"], 2026, 10, 5, 11, 0)
    _unblock(feed, ops["rahul"], 2026, 10, 5, 12, 0)
    blocked = AuditLog.objects.get(action="task.blocked")
    assert blocked.new_value == {"status": "BLOCKED", "reason": "Waiting for documents"}
    unblocked = AuditLog.objects.get(action="task.unblocked")
    assert unblocked.old_value == {"status": "BLOCKED", "reason": "Waiting for documents"}
    assert unblocked.new_value == {"status": "PENDING"}
    actions = list(AuditLog.objects.filter(entity_type="task", entity_id=str(feed.pk))
                   .order_by("id").values_list("action", flat=True))
    assert actions[-3:] == ["task.blocked", "task.unblocked", "task.sla_resumed"]


# --- 2, 3: nothing fires while paused ---------------------------------------------------------


def test_no_threshold_notification_or_overdue_case_while_on_hold(ops, feed, ist):
    _block(feed, ops["rahul"], 2026, 10, 5, 10, 30)
    assert _tick(2026, 10, 5, 12, 30)["thresholds"] == 0  # past the original 12:00 deadline
    clock = _clock(feed)
    assert (clock.warning_at, clock.critical_at, clock.overdue_at) == (None, None, None)
    assert _sla_notifications() == 0 and not OverdueCase.objects.exists()
    assert not AuditLog.objects.filter(action="task.sla_threshold_reached").exists()
    _unblock(feed, ops["rahul"], 2026, 10, 5, 13, 0)  # held 2.5 h: 12:30 - 14:30
    assert _tick(2026, 10, 5, 13, 0)["thresholds"] == 0  # 25%
    assert _tick(2026, 10, 5, 13, 31)["thresholds"] == 1  # 50.8%: WARNING, at its shifted time
    assert _clock(feed).warning_at == ist(2026, 10, 5, 13, 30)


# --- 4: how the API shows a paused clock ------------------------------------------------------


def test_the_api_shows_the_clock_paused_with_a_projected_deadline(client_for, ops, feed):
    _block(feed, ops["rahul"], 2026, 10, 5, 11, 0)
    sla = _resolution(client_for(ops["rahul"]), feed, 2026, 10, 5, 12, 30)
    assert (sla["state"], sla["elapsed_pct"]) == ("WARNING", 50.0)  # frozen at 11:00
    assert sla["remaining_seconds"] is None
    assert sla["waiting_for"] == "On hold since 11:00 IST — SLA paused"
    assert sla["due_at"] == "2026-10-05T13:30:00+05:30"  # 12:00 + 1.5 h held so far
    assert sla["start_at"] == "2026-10-05T10:00:00+05:30" and sla["stopped_at"] is None


# --- 5: every hold is shifted on its own ------------------------------------------------------


def test_two_hold_and_resume_cycles(ops, feed, ist):
    _block(feed, ops["rahul"], 2026, 10, 5, 10, 30)
    _unblock(feed, ops["rahul"], 2026, 10, 5, 11, 0)  # 30 min
    _block(feed, ops["rahul"], 2026, 10, 5, 11, 30)
    _unblock(feed, ops["rahul"], 2026, 10, 5, 12, 30)  # 1 h
    clock = _clock(feed)
    assert (clock.start_at, clock.due_at) == (ist(2026, 10, 5, 11, 30), ist(2026, 10, 5, 13, 30))
    held = list(AuditLog.objects.filter(action="task.sla_resumed").order_by("id")
                .values_list("context__held_seconds", flat=True))
    assert held == [1800, 3600]


# --- 6, 7, 8: holds that begin before the SLA has started -------------------------------------


def test_a_hold_before_the_sla_starts_moves_nothing(ops, new_task, template):
    with _at(2026, 10, 5, 9, 0):  # login-triggered: waits for the assignee's login
        task = new_task(ops["manager"], ops["rahul_emp"], template=template("BIRTHDAY_WISHES"))
    _block(task, ops["rahul"], 2026, 10, 5, 9, 30)
    _unblock(task, ops["rahul"], 2026, 10, 5, 10, 0)
    assert _clock(task).start_at is None
    assert not AuditLog.objects.filter(action="task.sla_resumed").exists()


def test_a_dependency_clock_that_starts_during_a_hold_excludes_only_the_overlap(
    client_for, ops, new_task, template, ist
):
    with _at(2026, 10, 5, 9, 0):
        brokerage = new_task(ops["manager"], ops["rahul_emp"],
                             template=template("BROKERAGE_CALCULATION"))
    with _at(2026, 10, 5, 9, 30):
        recon = new_task(ops["manager"], ops["rahul_emp"], template=template("RECONCILIATION"))
    _block(recon, ops["rahul"], 2026, 10, 5, 10, 0)
    _complete(brokerage, ops["rahul"], 2026, 10, 5, 11, 0)  # the engine starts it at 11:00
    assert _clock(recon).start_at == ist(2026, 10, 5, 11, 0)
    assert _tick(2026, 10, 5, 23, 0)["thresholds"] == 0  # 50% of 24 h would be 23:00
    sla = _resolution(client_for(ops["rahul"]), recon, 2026, 10, 5, 12, 0)
    assert (sla["elapsed_pct"], sla["waiting_for"]) == (0.0, "On hold since 10:00 IST — SLA paused")
    _unblock(recon, ops["rahul"], 2026, 10, 6, 1, 0)  # overlap 11:00 -> 01:00 = 14 h
    clock = _clock(recon)
    assert (clock.start_at, clock.due_at) == (ist(2026, 10, 6, 1, 0), ist(2026, 10, 7, 1, 0))


def test_a_future_fixed_time_start_during_a_hold_excludes_only_the_overlap(
    client_for, ops, new_task, template, ist
):
    with _at(2026, 10, 5, 9, 0):  # Feed Upload: fixed 10:00 start, 2 h
        task = new_task(ops["manager"], ops["rahul_emp"], template=template("FEED_UPLOAD"))
    _block(task, ops["rahul"], 2026, 10, 5, 9, 30)
    sla = _resolution(client_for(ops["rahul"]), task, 2026, 10, 5, 9, 45)
    assert sla["due_at"] == "2026-10-05T12:00:00+05:30" and sla["elapsed_pct"] == 0.0
    _unblock(task, ops["rahul"], 2026, 10, 5, 10, 45)  # overlap 10:00 -> 10:45
    clock = _clock(task)
    assert (clock.start_at, clock.due_at) == (ist(2026, 10, 5, 10, 45), ist(2026, 10, 5, 12, 45))


# --- 9: a task that was already overdue stays overdue -----------------------------------------


def test_an_overdue_task_held_and_resumed_stays_overdue(client_for, ops, feed, ist):
    assert _tick(2026, 10, 5, 12, 1)["thresholds"] == 3  # 50 / 75 / 100%
    assert OverdueCase.objects.count() == 1
    notified = _sla_notifications()
    _block(feed, ops["rahul"], 2026, 10, 5, 12, 30)
    assert _tick(2026, 10, 5, 14, 0)["thresholds"] == 0
    _unblock(feed, ops["rahul"], 2026, 10, 5, 15, 0)  # held 2.5 h
    clock = _clock(feed)
    assert (clock.start_at, clock.due_at) == (ist(2026, 10, 5, 12, 30), ist(2026, 10, 5, 14, 30))
    assert clock.overdue_at == ist(2026, 10, 5, 12, 0)  # history is kept
    assert _resolution(client_for(ops["rahul"]), feed, 2026, 10, 5, 15, 5)["state"] == "OVERDUE"
    _complete(feed, ops["rahul"], 2026, 10, 5, 15, 10)
    assert _clock(feed).outcome == "MISSED"
    assert OverdueCase.objects.count() == 1 and _sla_notifications() == notified


# --- 10, 11: priority SLA and responsibility deadline pause the same way ----------------------


def test_a_priority_sla_pauses(ops, new_task, ist):
    call_command("configure_priority_sla", stdout=StringIO())
    with _at(2026, 10, 5, 10, 0):
        task = new_task(ops["manager"], ops["rahul_emp"], priority="HIGH")  # 24 h
    _block(task, ops["rahul"], 2026, 10, 5, 11, 0)
    assert _tick(2026, 10, 6, 10, 30)["thresholds"] == 0  # past the original deadline
    _unblock(task, ops["rahul"], 2026, 10, 6, 11, 0)  # held 24 h
    clock = _clock(task)
    assert clock.rule_snapshot["code"] == "PRIORITY_HIGH_24H"
    assert (clock.start_at, clock.due_at) == (ist(2026, 10, 6, 10, 0), ist(2026, 10, 7, 10, 0))


def test_a_responsibility_deadline_pauses(admin_user, ops, own, ist):  # noqa: F811
    duty = recurring.create_responsibility(
        actor=admin_user, code="HOLD_DUTY", name="Hold Duty",
        department=Department.objects.get(code="OPS"),
        category=TaskCategory.objects.get(code="OPERATIONS"),
        template=TaskTemplate.objects.get(code="FEED_UPLOAD"), priority="LOW",
    )
    own(duty, ops["rahul_emp"])
    recurring.create_schedule(actor=admin_user, responsibility=duty, title="Hold Duty",
                              frequency="DAILY", run_time=time(10, 0),
                              effective_from=date(2026, 10, 5))
    duty.refresh_from_db()
    recurring.update_responsibility(actor=admin_user, responsibility=duty,
                                    version=duty.version, deadline_minutes=120)
    with _at(2026, 10, 5, 10, 0):
        generator.generate_due_occurrences()
    task = Task.objects.get(responsibility=duty)
    assert _clock(task).rule_snapshot["code"] == f"RESP_{duty.pk}"
    _block(task, ops["rahul"], 2026, 10, 5, 10, 30)
    _unblock(task, ops["rahul"], 2026, 10, 5, 11, 30)
    clock = _clock(task)
    assert (clock.start_at, clock.due_at) == (ist(2026, 10, 5, 11, 0), ist(2026, 10, 5, 13, 0))


# --- 13, 14: cancellation and the acknowledgment clock ----------------------------------------


def test_cancelling_a_held_task_stops_its_clocks_without_a_shift(ops, feed, ist):
    _block(feed, ops["rahul"], 2026, 10, 5, 10, 30)
    with _at(2026, 10, 5, 11, 0):
        feed.refresh_from_db()
        task_services.cancel_task(actor=ops["manager"], task=feed, version=feed.version,
                                  reason="Raised twice")
    clock = _clock(feed)
    assert (clock.stop_reason, clock.outcome) == ("CANCELLED", None)
    assert (clock.start_at, clock.due_at) == (ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 12, 0))
    assert not AuditLog.objects.filter(action="task.sla_resumed").exists()


def test_the_acknowledgment_clock_keeps_running_on_hold(ops, new_task, template, ist):
    with _at(2026, 10, 5, 10, 0):  # Broker Mapping: ACK 2 h + resolution 24 h
        task = new_task(ops["manager"], ops["rahul_emp"], template=template("BROKER_MAPPING"))
    _block(task, ops["rahul"], 2026, 10, 5, 10, 30)
    assert _tick(2026, 10, 5, 11, 1)["thresholds"] == 1  # the ACK clock's 50% (11:00)
    assert _clock(task, "ACK").warning_at == ist(2026, 10, 5, 11, 0)
    assert _clock(task).warning_at is None
    _unblock(task, ops["rahul"], 2026, 10, 5, 11, 30)
    ack = _clock(task, "ACK")
    assert (ack.start_at, ack.due_at) == (ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 12, 0))
    resolution = _clock(task)
    assert resolution.start_at == ist(2026, 10, 5, 11, 0)  # shifted by the 1 h hold
    assert resolution.due_at == ist(2026, 10, 6, 11, 0)


# --- 17: the operations monitoring rows (read through the unchanged monitoring module) --------


def test_monitoring_rows_of_a_held_task_are_not_overdue_and_show_no_countdown(ops, feed, ist):
    _block(feed, ops["rahul"], 2026, 10, 5, 10, 30)
    with _at(2026, 10, 5, 13, 0):  # an hour past the original deadline
        now = datetime.now(IST)
        task = Task.objects.get(pk=feed.pk)
        daily = monitoring.activity_row(task, now)
        assigned = monitoring.assigned_row(task, now.date(), now)
    assert (daily["is_overdue"], daily["remaining_seconds"]) == (False, None)
    assert daily["sla_state"] == "ON_TRACK" and daily["status"] == "BLOCKED"
    assert (assigned["is_overdue"], assigned["remaining_seconds"]) == (False, None)
    assert assigned["deadline"] == ist(2026, 10, 5, 14, 30)  # 12:00 + 2.5 h held so far
