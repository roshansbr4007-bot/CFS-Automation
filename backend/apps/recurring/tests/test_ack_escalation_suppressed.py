"""Approved C1/C2: a scheduled task's ACK clock that was ALREADY overdue when the task arrived
notifies the employee only (in-app + email) at its OVERDUE level. HR and the reporting manager
are not notified for that one level, no missing-recipient gap is reported, and
task.sla_escalation_suppressed is recorded exactly once. Everything else escalates as before.

Task type: Broker Mapping (resolution 24 h from the scheduled time, acknowledgment 2 h).
"""

from datetime import date, datetime

import pytest
from django.core import mail

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.overdue.models import OverdueCase
from apps.sla import services as sla_services
from apps.tasks import services as task_services
from apps.tasks.models import TaskTemplate

from .scheduled_helpers import at, clock, duty, ist, only, run, task_of, tick

pytestmark = pytest.mark.django_db


@pytest.fixture
def people(make_user, ops):
    """Active HR, an Admin (never the Boss) and Rahul's reporting manager (the Boss)."""
    hr = make_user(roles.HR, email="hr@example.com")
    make_user(roles.ADMIN, email="admin.person@example.com")
    for name in ("rahul_emp", "amit_emp"):
        ops[name].reporting_manager = ops["manager_emp"]
        ops[name].save()
    return {"hr": hr, "boss": ops["manager"], "rahul": ops["rahul"], "amit": ops["amit"]}


def _broker_duty(admin_user, ops, **kw):
    return only(duty(ops["rahul_emp"], admin_user,
                     template=TaskTemplate.objects.get(code="BROKER_MAPPING"), **kw))


def _overdue_recipients(task, kind):
    return set(
        Notification.objects.filter(task=task, clock__kind=kind, kind="SLA_OVERDUE")
        .values_list("recipient__email", flat=True)
    )


def _suppressed(task=None):
    rows = AuditLog.objects.filter(action="task.sla_escalation_suppressed")
    return rows.filter(entity_id=str(task.pk)) if task else rows


def test_late_arrival_notifies_only_the_employee_and_records_it_once(admin_user, ops, people):
    schedule = _broker_duty(admin_user, ops)
    run(2026, 10, 5, 12, 30)  # ACK 10:00-12:00: overdue on arrival; resolution until 6 Oct
    task = task_of(schedule)
    ack = clock(task, "ACK")
    assert sla_services.overdue_on_arrival(ack) is True
    result = tick(2026, 10, 5, 12, 31)
    assert result["thresholds"] == 3 and result["escalation_gaps"] == 0
    assert _overdue_recipients(task, "ACK") == {people["rahul"].email}
    # The employee still gets the acknowledgment-overdue email (and the critical one).
    assert sorted(m.to[0] for m in mail.outbox) == [people["rahul"].email] * 2
    row = _suppressed(task).get()
    assert row.actor_user is None
    stored = dict(row.new_value)
    times = (stored.pop("clock_created_at"), stored.pop("overdue_threshold_at"))
    assert stored == {
        "clock": "ACK", "clock_id": ack.pk, "level": "OVERDUE",
        "reason": "ACK_OVERDUE_ON_ARRIVAL", "suppressed_recipients": ["HR", "BOSS"],
    }
    assert tuple(datetime.fromisoformat(t) for t in times) == (
        ist(2026, 10, 5, 12, 30), ist(2026, 10, 5, 12, 0),
    )
    assert row.context["department_id"] == task.department_id
    assert not AuditLog.objects.filter(action="task.sla_escalation_recipient_missing").exists()
    levels = AuditLog.objects.filter(action="task.sla_threshold_reached", entity_id=str(task.pk))
    assert sorted(r.new_value["level"] for r in levels) == ["CRITICAL", "OVERDUE", "WARNING"]
    # The stored arrival fact and the suppression decision agree.
    generated = AuditLog.objects.get(action="recurring.task_generated")
    assert generated.context["ack_overdue_on_arrival"] is True

    # Idempotent: later passes add nothing.
    notifications = Notification.objects.count()
    for minute in (32, 40):
        assert tick(2026, 10, 5, 12, minute)["thresholds"] == 0
    assert Notification.objects.count() == notifications
    assert _suppressed().count() == 1


def test_no_reporting_manager_gives_no_false_missing_recipient_audit(admin_user, ops, make_user):
    make_user(roles.HR, email="hr@example.com")  # Rahul has no reporting manager at all
    schedule = _broker_duty(admin_user, ops)
    run(2026, 10, 5, 12, 30)
    assert tick(2026, 10, 5, 12, 31)["escalation_gaps"] == 0
    assert not AuditLog.objects.filter(action="task.sla_escalation_recipient_missing").exists()
    assert _overdue_recipients(task_of(schedule), "ACK") == {ops["rahul"].email}
    assert _suppressed().count() == 1


def test_the_resolution_overdue_of_the_same_task_still_escalates(admin_user, ops, people):
    schedule = _broker_duty(admin_user, ops)
    run(2026, 10, 5, 12, 30)
    task = task_of(schedule)
    tick(2026, 10, 5, 12, 31)
    tick(2026, 10, 6, 10, 0)  # resolution: 10:00 on 5 Oct + 24 h
    assert _overdue_recipients(task, "RESOLUTION") == {
        people["rahul"].email, people["hr"].email, people["boss"].email,
    }
    assert OverdueCase.objects.filter(task=task).count() == 1
    assert _suppressed().count() == 1  # only the acknowledgment level was ever suppressed


def test_an_acknowledgment_that_becomes_overdue_after_arrival_escalates(admin_user, ops, people):
    schedule = _broker_duty(admin_user, ops)
    run(2026, 10, 5, 10, 0)  # on time
    task = task_of(schedule)
    assert sla_services.overdue_on_arrival(clock(task, "ACK")) is False
    tick(2026, 10, 5, 12, 0)
    assert _overdue_recipients(task, "ACK") == {
        people["rahul"].email, people["hr"].email, people["boss"].email,
    }
    assert not _suppressed().exists()


def test_late_but_not_yet_overdue_on_arrival_escalates_normally(admin_user, ops, people):
    schedule = _broker_duty(admin_user, ops)
    run(2026, 10, 5, 11, 45)  # ACK 10:00-12:00 is at 87.5% (critical), not overdue
    task = task_of(schedule)
    tick(2026, 10, 5, 11, 46)
    tick(2026, 10, 5, 12, 0)
    assert _overdue_recipients(task, "ACK") == {
        people["rahul"].email, people["hr"].email, people["boss"].email,
    }
    assert not _suppressed().exists()


def test_manual_task_acknowledgment_escalates_normally(ops, people, new_task):
    with at(2026, 10, 5, 9, 0):
        task = new_task(ops["manager"], ops["rahul_emp"],
                        template=TaskTemplate.objects.get(code="BROKER_MAPPING"))
    tick(2026, 10, 5, 11, 0)
    assert _overdue_recipients(task, "ACK") == {
        people["rahul"].email, people["hr"].email, people["boss"].email,
    }
    assert not _suppressed().exists()


def test_a_reassigned_acknowledgment_clock_escalates_normally(admin_user, ops, people):
    schedule = _broker_duty(admin_user, ops)
    run(2026, 10, 5, 12, 30)
    task = task_of(schedule)
    with at(2026, 10, 5, 12, 35):  # reassigned before any monitor pass
        task_services.reassign_task(actor=ops["manager"], task=task, version=task.version,
                                    assigned_to=ops["amit_emp"])
    tick(2026, 10, 5, 14, 35)  # the new ACK clock (12:35-14:35) is overdue now
    assert _overdue_recipients(task, "ACK") == {
        people["amit"].email, people["hr"].email, people["boss"].email,
    }
    assert not _suppressed().exists()


def test_completion_before_the_first_pass_sends_nothing(admin_user, ops, people):
    schedule = _broker_duty(admin_user, ops)
    run(2026, 10, 5, 12, 30)
    task = task_of(schedule)
    with at(2026, 10, 5, 12, 33):
        task_services.acknowledge_task(actor=ops["rahul"], task=task, version=task.version)
    tick(2026, 10, 5, 12, 34)
    assert not Notification.objects.filter(task=task, clock__kind="ACK").exists()
    assert not _suppressed().exists()


def test_a_weekly_catch_up_suppresses_only_its_own_acknowledgment(admin_user, ops, people):
    schedule = _broker_duty(admin_user, ops, frequency="WEEKLY", weekdays=[2],
                            start=date(2026, 10, 1))
    run(2026, 10, 9, 11, 0)  # Wednesday's occurrence arrives two days late
    task = task_of(schedule, date(2026, 10, 7))
    tick(2026, 10, 9, 11, 1)
    assert _overdue_recipients(task, "ACK") == {people["rahul"].email}
    # Its resolution (7 Oct 10:00 + 24 h) arrived overdue too: that escalation is kept.
    assert _overdue_recipients(task, "RESOLUTION") == {
        people["rahul"].email, people["hr"].email, people["boss"].email,
    }
    assert _suppressed().count() == 1


def test_every_guard_of_the_suppression_rule_is_needed(admin_user, ops):
    """Each condition alone stops the suppression: only the OVERDUE level, only an ACK clock,
    only the scheduled task's own clock (FIXED_TIME; a reassignment's is ASSIGNMENT), only a
    scheduled task, only when already overdue at creation."""
    schedule = _broker_duty(admin_user, ops)
    run(2026, 10, 5, 12, 30)
    task = task_of(schedule)
    ack = clock(task, "ACK")
    assert sla_services.ack_escalation_suppressed(ack, "OVERDUE") is True
    assert sla_services.ack_escalation_suppressed(ack, "CRITICAL") is False
    ack.trigger = "ASSIGNMENT"
    assert sla_services.ack_escalation_suppressed(ack, "OVERDUE") is False
    ack.trigger = "FIXED_TIME"
    ack.task.source = "MANUAL"
    assert sla_services.ack_escalation_suppressed(ack, "OVERDUE") is False
    ack.task.source = "SCHEDULED"
    ack.created_at = ack.start_at  # not yet overdue when created
    assert sla_services.ack_escalation_suppressed(ack, "OVERDUE") is False
    resolution = clock(task)
    resolution.start_at, resolution.due_at = ack.start_at, ack.due_at
    resolution.trigger = "FIXED_TIME"
    assert sla_services.ack_escalation_suppressed(resolution, "OVERDUE") is False


def test_the_acknowledgment_of_a_dependency_type_starts_at_the_scheduled_time(admin_user, ops):
    """Only the clock that waits on the dependency keeps the dependency engine's start; the
    acknowledgment clock is not a dependency clock (approved scope: ACK uses the scheduled time)."""
    waits = TaskTemplate.objects.get(code="RECONCILIATION")
    waits.acknowledgment_required = True
    waits.save(update_fields=["acknowledgment_required"])
    schedule = only(duty(ops["rahul_emp"], admin_user, template=waits))
    run(2026, 10, 5, 12, 30)
    task = task_of(schedule)
    assert (clock(task).trigger, clock(task).start_at) == ("DEPENDENCY", None)
    ack = clock(task, "ACK")
    assert (ack.trigger, ack.start_at) == ("FIXED_TIME", ist(2026, 10, 5, 10, 0))
    assert sla_services.ack_escalation_suppressed(ack, "OVERDUE") is True
