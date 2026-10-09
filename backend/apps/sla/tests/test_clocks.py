"""Clock lifecycle per trigger, stop rules, rule versioning (locked Phase 6 decisions)."""

from datetime import time, timedelta

import pytest
import time_machine

from apps.org.models import Department, EmployeeDailyLogin
from apps.sla import services
from apps.sla.models import SlaRule, SlaSetting, TaskSla
from apps.tasks import services as task_services
from apps.tasks.models import TaskCategory, TaskTemplate

TASKS = "/api/v1/tasks/"
pytestmark = pytest.mark.django_db


def _classified(**body):
    """Phase 4: every new task carries a creator-chosen department and category."""
    return {
        "department": Department.objects.get(code="OPS").pk,
        "category": TaskCategory.objects.get(code="OPERATIONS").pk,
        **body,
    }


def _clock(task, kind="RESOLUTION"):
    return TaskSla.objects.get(task=task, kind=kind, is_current=True)


def _sla(client, task):
    return client.get(f"{TASKS}{task.pk}/").json()["sla"]


def _act(client, task, name, **body):
    task.refresh_from_db()
    return client.post(f"{TASKS}{task.pk}/{name}/", {"version": task.version, **body})


def test_adhoc_task_has_no_resolution_sla(client_for, ops, new_task):
    task = new_task(ops["manager"], ops["rahul_emp"])
    sla = _sla(client_for(ops["rahul"]), task)
    assert sla["resolution"] is None and sla["resolution_note"] == "No SLA configured"
    assert sla["acknowledgment"] is None
    assert not TaskSla.objects.exists()


def test_broker_mapping_starts_at_assignment_with_ack_clock(ops, new_task, template):
    task = new_task(ops["manager"], ops["rahul_emp"], template=template("BROKER_MAPPING"))
    assert task.task_type == "REGULAR" and task.acknowledgment_required is True
    clock = _clock(task)
    assert clock.start_at == task.assigned_at
    assert clock.due_at == task.assigned_at + timedelta(hours=24)
    assert clock.rule_snapshot["code"] == "BROKER_MAPPING_24H"
    ack = _clock(task, "ACK")
    assert ack.due_at == task.assigned_at + timedelta(hours=2)


def test_late_feed_upload_keeps_the_original_10_00_trigger(
    client_for, ops, new_task, template, ist
):
    with time_machine.travel(ist(2026, 10, 5, 11, 30), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], template=template("FEED_UPLOAD"))
        clock = _clock(task)
        assert clock.start_at == ist(2026, 10, 5, 10, 0)
        assert clock.due_at == ist(2026, 10, 5, 12, 0)
        sla = _sla(client_for(ops["rahul"]), task)["resolution"]
        assert sla["elapsed_pct"] == 75.0 and sla["state"] == "CRITICAL"


def test_early_feed_upload_waits_for_10_00(client_for, ops, new_task, template, ist):
    with time_machine.travel(ist(2026, 10, 5, 9, 0), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], template=template("FEED_UPLOAD"))
        sla = _sla(client_for(ops["rahul"]), task)["resolution"]
        assert sla["state"] == "NOT_STARTED" and sla["waiting_for"] == "Starts at 10:00 IST"
        assert services.evaluate_clocks()["thresholds"] == 0


def test_login_task_uses_the_days_login_time(ops, new_task, template, ist):
    with time_machine.travel(ist(2026, 10, 5, 11, 30), tick=False):
        EmployeeDailyLogin.objects.create(
            employee=ops["rahul_emp"],
            work_date="2026-10-05",
            first_login_at=ist(2026, 10, 5, 9, 30),
        )
        task = new_task(ops["manager"], ops["rahul_emp"], template=template("BIRTHDAY_WISHES"))
        assert _clock(task).start_at == ist(2026, 10, 5, 9, 30)


def test_login_task_waits_then_starts_at_login(client_for, ops, new_task, template, ist):
    """Updated in Phase 5: SIP/STP is now fixed-time, so the LOGIN trigger is shown with
    Birthday Wishes (still login-triggered, 2 hours)."""
    with time_machine.travel(ist(2026, 10, 5, 8, 0), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], template=template("BIRTHDAY_WISHES"))
        sla = _sla(client_for(ops["manager"]), task)["resolution"]
        assert sla["state"] == "NOT_STARTED"
        assert "no fallback time configured" in sla["waiting_for"]
    with time_machine.travel(ist(2026, 10, 5, 9, 45), tick=False):
        client_for(ops["rahul"])  # a real login: the sla login receiver starts the clock
        clock = _clock(task)
        assert clock.start_at == ist(2026, 10, 5, 9, 45)
        assert clock.due_at == ist(2026, 10, 5, 11, 45)


def test_login_fallback_time_starts_the_clock_in_the_checker(ops, new_task, template, ist):
    SlaSetting.objects.update_or_create(pk=1, defaults={"login_fallback_time": time(10, 0)})
    with time_machine.travel(ist(2026, 10, 5, 8, 0), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], template=template("BIRTHDAY_WISHES"))
        assert services.evaluate_clocks()["login_clocks_started"] == 0
    with time_machine.travel(ist(2026, 10, 5, 10, 5), tick=False):
        assert services.evaluate_clocks()["login_clocks_started"] == 1
        assert _clock(task).start_at == ist(2026, 10, 5, 10, 0)


def test_dependency_task_never_starts_by_itself(client_for, ops, new_task, template):
    task = new_task(ops["manager"], ops["rahul_emp"], template=template("RECONCILIATION"))
    services.evaluate_clocks()
    sla = _sla(client_for(ops["manager"]), task)["resolution"]
    assert sla["state"] == "NOT_STARTED"
    assert sla["waiting_for"] == "Not started — waiting for upstream task"


def _end_of_day_type():
    """A task type on the (unchanged) END_OF_DAY rule MAIL_SAME_DAY, started by login."""
    return TaskTemplate.objects.create(
        code="EOD_TEST",
        name="End of day test",
        department=Department.objects.get(code="OPS"),
        resolution_rule_code="MAIL_SAME_DAY",
        trigger="LOGIN",
    )


def test_end_of_day_rule_is_inactive_until_work_end_is_set(client_for, ops, new_task, ist):
    """Updated in Phase 5: Mail Checking moved to the fixed 10:00-12:00 rule, so the
    END_OF_DAY engine behaviour is tested with a task type that still uses it."""
    eod = _end_of_day_type()
    task = new_task(ops["manager"], ops["rahul_emp"], template=eod)
    sla = _sla(client_for(ops["manager"]), task)
    assert sla["resolution"] is None
    assert sla["resolution_note"] == "SLA inactive until company work_end is configured."
    SlaSetting.objects.update_or_create(pk=1, defaults={"company_work_end": time(18, 30)})
    with time_machine.travel(ist(2026, 10, 5, 9, 0), tick=False):
        EmployeeDailyLogin.objects.create(
            employee=ops["amit_emp"], work_date="2026-10-05", first_login_at=ist(2026, 10, 5, 8, 50)
        )
        later = new_task(ops["manager"], ops["amit_emp"], template=eod)
        assert _clock(later).due_at == ist(2026, 10, 5, 18, 30)


@pytest.mark.parametrize(
    ("code", "due_hour"), [("MAIL_CHECKING", 12), ("SIP_STP_CHECK", 13), ("FEED_UPLOAD", 12)]
)
def test_phase5_fixed_time_types_run_from_10(ops, new_task, template, ist, code, due_hour):
    """Approved Phase 5 rules: Mail Checking 10:00-12:00, SIP/STP/Switch 10:00-13:00."""
    with time_machine.travel(ist(2026, 10, 5, 10, 30), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], template=template(code))
    clock = _clock(task)
    assert clock.trigger == "FIXED_TIME"
    assert clock.start_at == ist(2026, 10, 5, 10, 0)
    assert clock.due_at == ist(2026, 10, 5, due_hour, 0)


def test_event_tasks_need_a_past_event_time(client_for, ops, template, now):
    client = client_for(ops["manager"])
    base = _classified(
        title="SIP failed",
        assigned_to=ops["rahul_emp"].pk,
        template=template("SIP_FAILURE").pk,
    )
    assert "trigger_at" in client.post(TASKS, base).json()["fields"]
    future = {**base, "trigger_at": (now + timedelta(hours=1)).isoformat()}
    assert "trigger_at" in client.post(TASKS, future).json()["fields"]
    event = (now - timedelta(hours=3)).replace(microsecond=0)
    created = client.post(TASKS, {**base, "trigger_at": event.isoformat()})
    assert created.status_code == 201
    assert created.json()["sla"]["resolution"]["start_at"] is not None
    assert _clock(created.json()["id"]).start_at == event


def test_event_time_only_for_event_types_and_regular_type(client_for, ops, template, now):
    client = client_for(ops["manager"])
    base = _classified(title="x", assigned_to=ops["rahul_emp"].pk)
    with_time = {**base, "trigger_at": (now - timedelta(hours=1)).isoformat()}
    assert "trigger_at" in client.post(TASKS, with_time).json()["fields"]
    broker = template("BROKER_MAPPING").pk
    assert "trigger_at" in client.post(TASKS, {**with_time, "template": broker}).json()["fields"]
    adhoc = client.post(TASKS, {**base, "template": broker, "task_type": "ADHOC"})
    assert "task_type" in adhoc.json()["fields"]


def test_inactive_template_is_refused(client_for, ops, template):
    feed = template("FEED_UPLOAD")
    feed.is_active = False
    feed.save()
    body = _classified(title="x", assigned_to=ops["rahul_emp"].pk, template=feed.pk)
    assert "template" in client_for(ops["manager"]).post(TASKS, body).json()["fields"]


# --- stopping -----------------------------------------------------------------------------------


def test_completion_stops_at_server_time_met_or_missed(client_for, ops, new_task, template, ist):
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        on_time = new_task(ops["manager"], ops["rahul_emp"], template=template("FEED_UPLOAD"))
        late = new_task(ops["manager"], ops["amit_emp"], template=template("FEED_UPLOAD"))
    rahul, amit = client_for(ops["rahul"]), client_for(ops["amit"])
    with time_machine.travel(ist(2026, 10, 5, 11, 0), tick=False):
        _act(rahul, on_time, "start")
        body = _act(rahul, on_time, "complete").json()
        assert body["completed_at"] == "2026-10-05T11:00:00+05:30"
        assert body["sla"]["resolution"]["outcome"] == "MET"
    with time_machine.travel(ist(2026, 10, 5, 12, 30), tick=False):
        _act(amit, late, "start")
        assert _act(amit, late, "complete").json()["sla"]["resolution"]["outcome"] == "MISSED"
    assert _clock(late).stopped_at == ist(2026, 10, 5, 12, 30)


def test_on_hold_pauses_the_resolution_clock(client_for, ops, new_task, template, ist):
    """Approved HOLD rule (replaces the Phase 6 rule "Blocked never pauses a clock"): the same
    task held from 10:00 is still at 0% at 11:00, and its deadline moves with the hold."""
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], template=template("FEED_UPLOAD"))
        _act(client_for(ops["rahul"]), task, "block", reason="Feed file missing")
    with time_machine.travel(ist(2026, 10, 5, 11, 0), tick=False):
        sla = _sla(client_for(ops["rahul"]), task)["resolution"]
        assert sla["elapsed_pct"] == 0.0 and sla["state"] == "ON_TRACK"
        assert sla["remaining_seconds"] is None
        assert sla["waiting_for"] == "On hold since 10:00 IST — SLA paused"
        assert sla["due_at"] == "2026-10-05T13:00:00+05:30"  # 12:00 + 1 h held so far


def test_cancel_stops_every_clock_without_outcome(client_for, ops, new_task, template):
    task = new_task(ops["manager"], ops["rahul_emp"], template=template("BROKER_MAPPING"))
    _act(client_for(ops["manager"]), task, "cancel", reason="Duplicate")
    for kind in ("RESOLUTION", "ACK"):
        clock = _clock(task, kind)
        assert clock.stop_reason == "CANCELLED" and clock.outcome is None


def test_reassignment_restarts_ack_but_not_resolution(client_for, ops, new_task, template, ist):
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], template=template("BROKER_MAPPING"))
    with time_machine.travel(ist(2026, 10, 5, 11, 0), tick=False):
        _act(client_for(ops["manager"]), task, "reassign", assigned_to=ops["amit_emp"].pk)
    acks = TaskSla.objects.filter(task=task, kind="ACK").order_by("id")
    assert [(c.is_current, c.stop_reason) for c in acks] == [(False, "REASSIGNED"), (True, "")]
    assert acks[1].start_at == ist(2026, 10, 5, 11, 0)
    assert _clock(task).start_at == ist(2026, 10, 5, 10, 0)  # resolution untouched


def test_ack_overdue_never_bypasses_acknowledgment(client_for, ops, new_task, template, ist):
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], template=template("BROKER_MAPPING"))
    rahul = client_for(ops["rahul"])
    with time_machine.travel(ist(2026, 10, 5, 13, 0), tick=False):
        services.evaluate_clocks()
        assert _clock(task, "ACK").overdue_at is not None
        assert _act(rahul, task, "start").json()["code"] == "acknowledgment_required"
        _act(rahul, task, "acknowledge")
        ack = TaskSla.objects.get(task=task, kind="ACK")
        assert ack.stop_reason == "ACKNOWLEDGED" and ack.outcome == "MISSED"


def test_rejection_starts_no_new_resolution_clock(client_for, ops, staff, template):
    feed = template("FEED_UPLOAD")
    feed.verification_required = True
    feed.save()
    task = task_services.create_task(
        actor=ops["manager"],
        title="Feed",
        assigned_to=ops["rahul_emp"],
        department=ops["rahul_emp"].department,
        category=TaskCategory.objects.get(code="OPERATIONS"),
        template=feed,
    )
    rahul, manager = client_for(ops["rahul"]), client_for(ops["manager"])
    _act(rahul, task, "start")
    _act(rahul, task, "complete")
    _act(manager, task, "reject-verification", reason="Wrong file", remarks="Upload v2")
    _act(rahul, task, "complete")
    assert TaskSla.objects.filter(task=task, kind="RESOLUTION").count() == 1


def test_switching_acknowledgment_on_and_off(client_for, ops, new_task):
    task = new_task(ops["manager"], ops["rahul_emp"])
    manager = client_for(ops["manager"])
    task.refresh_from_db()
    manager.patch(f"{TASKS}{task.pk}/", {"version": task.version, "acknowledgment_required": True})
    assert _clock(task, "ACK").stopped_at is None
    task.refresh_from_db()
    manager.patch(f"{TASKS}{task.pk}/", {"version": task.version, "acknowledgment_required": False})
    assert TaskSla.objects.get(task=task, kind="ACK").stop_reason == "NOT_REQUIRED"


def test_rule_change_never_moves_an_existing_deadline(ops, new_task, template, admin_user):
    first = new_task(ops["manager"], ops["rahul_emp"], template=template("BROKER_MAPPING"))
    rule = SlaRule.objects.get(code="BROKER_MAPPING_24H", is_active=True)
    services.supersede_rule(actor=admin_user, rule=rule, duration_minutes=720)
    second = new_task(ops["manager"], ops["amit_emp"], template=template("BROKER_MAPPING"))
    assert _clock(first).due_at - _clock(first).start_at == timedelta(hours=24)
    assert _clock(second).due_at - _clock(second).start_at == timedelta(hours=12)
    assert _clock(second).rule_snapshot["version"] == 2


def test_missing_ack_rule_means_no_ack_clock(ops, new_task):
    SlaRule.objects.filter(code="ACK_2H").update(is_active=False)
    task = new_task(ops["manager"], ops["rahul_emp"], acknowledgment_required=True)
    assert not TaskSla.objects.filter(task=task).exists()



def test_task_type_controls_acknowledgment_and_verification(client_for, ops, template):
    client = client_for(ops["manager"])
    body = _classified(
        title="Mail",
        assigned_to=ops["rahul_emp"].pk,
        template=template("MAIL_CHECKING").pk,
    )
    clash = client.post(TASKS, {**body, "acknowledgment_required": True})
    assert clash.status_code == 400
    assert clash.json()["fields"] == {"acknowledgment_required": ["Set by the selected task type."]}
    assert client.post(TASKS, {**body, "verification_required": True}).status_code == 400
    created = client.post(TASKS, body).json()
    assert created["acknowledgment_required"] is False and created["verification_required"] is False
    broker = client.post(
        TASKS, {**body, "template": template("BROKER_MAPPING").pk, "acknowledgment_required": True}
    )
    assert broker.status_code == 201 and broker.json()["acknowledgment_required"] is True


def test_acknowledged_clock_stays_visible_with_its_outcome(client_for, ops, new_task, template):
    task = new_task(ops["manager"], ops["rahul_emp"], template=template("BROKER_MAPPING"))
    rahul = client_for(ops["rahul"])
    ack = _act(rahul, task, "acknowledge").json()["sla"]["acknowledgment"]
    assert ack["stop_reason"] == "ACKNOWLEDGED" and ack["outcome"] == "MET"
    assert TaskSla.objects.get(task=task, kind="ACK").is_current is False  # preserved fix
    _act(client_for(ops["manager"]), task, "reassign", assigned_to=ops["amit_emp"].pk)
    after = _sla(client_for(ops["manager"]), task)["acknowledgment"]
    assert after["stop_reason"] is None and after["outcome"] is None  # the new assignee's clock


def test_sla_timestamps_use_the_same_ist_format_as_other_fields(
    client_for, ops, new_task, template
):
    task = new_task(ops["manager"], ops["rahul_emp"], template=template("BROKER_MAPPING"))
    body = client_for(ops["rahul"]).get(f"{TASKS}{task.pk}/").json()
    assert body["assigned_at"].endswith("+05:30")
    assert body["sla"]["resolution"]["start_at"].endswith("+05:30")
    assert body["sla"]["resolution"]["start_at"] == body["assigned_at"]
