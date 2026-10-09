"""Edge cases of the clock lifecycle and the checker that the main tests do not reach."""

from datetime import time

import pytest
import time_machine

from apps.notifications import services as notification_services
from apps.notifications.models import Notification
from apps.org.models import EmployeeDailyLogin
from apps.sla import services
from apps.sla.models import SlaRule, SlaSetting, TaskSla
from apps.tasks.models import TaskTemplate

pytestmark = pytest.mark.django_db
TASKS = "/api/v1/tasks/"


def test_planned_rule_types_create_no_clock_and_say_why(ops, new_task, dept):
    SlaRule.objects.create(code="TXN_TEST", name="Transactions", rule_type="TRANSACTION_CUTOFF")
    txn = TaskTemplate.objects.create(
        code="TXN", name="Transaction", department=dept("OPS"), resolution_rule_code="TXN_TEST"
    )
    task = new_task(ops["manager"], ops["rahul_emp"], template=txn)
    assert not TaskSla.objects.filter(task=task).exists()
    note = services.resolution_plan(txn)[2]
    assert note == "Transactions: this rule type arrives in a later phase."


def test_cancelled_before_the_fixed_start_has_no_wait_message(
    client_for, ops, new_task, template, ist
):
    with time_machine.travel(ist(2026, 10, 5, 9, 0), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], template=template("FEED_UPLOAD"))
    with time_machine.travel(ist(2026, 10, 5, 9, 30), tick=False):
        task.refresh_from_db()
        client = client_for(ops["manager"])
        client.post(f"{TASKS}{task.pk}/cancel/", {"version": task.version, "reason": "x"})
        sla = client.get(f"{TASKS}{task.pk}/").json()["sla"]["resolution"]
    assert sla["state"] == "NOT_STARTED" and sla["waiting_for"] is None
    assert sla["stop_reason"] == "CANCELLED" and sla["outcome"] is None


def test_a_login_on_another_day_does_not_start_yesterdays_clock(ops, new_task, template, ist):
    with time_machine.travel(ist(2026, 10, 5, 8, 0), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], template=template("BIRTHDAY_WISHES"))
    assert services.start_login_clocks(ops["rahul_emp"].pk, ist(2026, 10, 6, 9, 0)) == 0
    assert TaskSla.objects.get(task=task).start_at is None


def test_fallback_checker_prefers_an_actual_login(ops, new_task, template, ist):
    SlaSetting.objects.update_or_create(pk=1, defaults={"login_fallback_time": time(10, 0)})
    with time_machine.travel(ist(2026, 10, 5, 8, 0), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], template=template("BIRTHDAY_WISHES"))
    EmployeeDailyLogin.objects.create(
        employee=ops["rahul_emp"], work_date="2026-10-05", first_login_at=ist(2026, 10, 5, 9, 10)
    )
    with time_machine.travel(ist(2026, 10, 5, 9, 20), tick=False):
        assert services.evaluate_clocks()["login_clocks_started"] == 1
    assert TaskSla.objects.get(task=task).start_at == ist(2026, 10, 5, 9, 10)


def test_checker_skips_a_clock_that_stopped_meanwhile(ops, new_task, template, ist):
    task = new_task(ops["manager"], ops["rahul_emp"], template=template("BROKER_MAPPING"))
    clock = TaskSla.objects.get(task=task, kind="RESOLUTION")
    TaskSla.objects.filter(pk=clock.pk).update(stopped_at=clock.start_at)
    assert services.evaluate_clock(clock.pk, ist(2030, 1, 1, 0, 0)).levels == []
    clock.refresh_from_db()
    services._stop(clock, ist(2030, 1, 1, 0, 0), "CANCELLED", judge=False)  # already stopped
    clock.refresh_from_db()
    assert clock.stopped_at == clock.start_at and clock.stop_reason == ""


def test_mark_read_twice_keeps_the_first_time(ops, new_task, template, ist):
    with time_machine.travel(ist(2026, 10, 5, 9, 0), tick=False):
        new_task(ops["manager"], ops["rahul_emp"], template=template("FEED_UPLOAD"))
    with time_machine.travel(ist(2026, 10, 5, 11, 0), tick=False):
        services.evaluate_clocks()
    row = Notification.objects.get(recipient=ops["rahul"])
    first = notification_services.mark_read(user=ops["rahul"], notification_id=row.pk).read_at
    again = notification_services.mark_read(user=ops["rahul"], notification_id=row.pk).read_at
    assert first == again is not None


def test_string_representations(ops, new_task, template, ist):
    task = new_task(ops["manager"], ops["rahul_emp"], template=template("BROKER_MAPPING"))
    clock = TaskSla.objects.get(task=task, kind="RESOLUTION")
    assert str(clock) == f"{task.pk} RESOLUTION"
    assert str(clock.rule) == "BROKER_MAPPING_24H v1"
    assert str(SlaSetting.load()) == "SLA settings"
    assert str(template("BROKER_MAPPING")) == "Broker Mapping"
    with time_machine.travel(ist(2030, 1, 1, 0, 0), tick=False):
        services.evaluate_clocks()
    row = Notification.objects.filter(recipient=ops["rahul"]).first()
    assert str(row) == f"{ops['rahul'].pk}: {row.title}"
