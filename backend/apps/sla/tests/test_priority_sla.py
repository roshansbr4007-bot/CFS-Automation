"""Phase 5.2: priority-based SLA for manually raised tasks (configurable; nothing seeded).

A configured priority rule decides a MANUAL task's deadline from its assignment time, never
from a daily responsibility's fixed schedule. Without a configured mapping the existing
task-type SLA applies unchanged."""

import pytest
import time_machine

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.sla import services
from apps.sla.models import PrioritySla, SlaRule, TaskSla
from apps.tasks import services as task_services

pytestmark = pytest.mark.django_db
TASKS = "/api/v1/tasks/"
RULES = "/api/v1/sla-rules/"
PRIORITY_RULES = "/api/v1/sla-priority-rules/"


@pytest.fixture
def high_4h(admin_user):
    """An Admin-configured rule (4 hours) mapped to HIGH. The value is test data only."""
    services.create_rule(actor=admin_user, code="PRIORITY_HIGH", name="High priority",
                         duration_minutes=240)
    services.set_priority_rule(actor=admin_user, priority="HIGH", rule_code="PRIORITY_HIGH")


def _clock(task):
    return TaskSla.objects.get(task=task, kind="RESOLUTION")


def test_nothing_is_configured_by_default(client_for, make_user):
    assert not PrioritySla.objects.exists()
    assert client_for(make_user(roles.EMPLOYEE)).get(PRIORITY_RULES).json() == []


def test_admin_configures_a_rule_and_a_priority_mapping(admin_client, client_for, make_user):
    created = admin_client.post(RULES, {"code": "priority_high", "name": "High priority",
                                        "duration_minutes": 240})
    assert created.status_code == 201 and created.json()["code"] == "PRIORITY_HIGH"
    assert admin_client.post(RULES, {"code": "PRIORITY_HIGH", "name": "x",
                                     "duration_minutes": 60}).status_code == 409
    bad = admin_client.post(RULES, {"code": "X", "name": "x", "duration_minutes": 60,
                                    "warning_pct": 90, "critical_pct": 80})
    assert bad.status_code == 400
    mapped = admin_client.put(f"{PRIORITY_RULES}HIGH/", {"rule_code": "PRIORITY_HIGH"})
    assert mapped.status_code == 200 and mapped.json()["is_active"] is True
    not_duration = admin_client.put(f"{PRIORITY_RULES}LOW/", {"rule_code": "MAIL_SAME_DAY"})
    assert not_duration.status_code == 400
    assert admin_client.put(f"{PRIORITY_RULES}LOW/", {"rule_code": "NOPE"}).status_code == 400
    unknown = admin_client.put(f"{PRIORITY_RULES}CRITICAL/", {"rule_code": "PRIORITY_HIGH"})
    assert unknown.status_code == 400  # not one of the existing priority levels
    assert admin_client.put(f"{PRIORITY_RULES}LOW/", {"is_active": False}).status_code == 400
    switched_off = admin_client.put(f"{PRIORITY_RULES}HIGH/", {"is_active": False}).json()
    assert switched_off["rule_code"] == "PRIORITY_HIGH" and switched_off["is_active"] is False
    rows = AuditLog.objects.filter(action__startswith="sla.")
    actions = sorted(rows.values_list("action", flat=True))
    assert actions == ["sla.priority_rule_set", "sla.priority_rule_set", "sla.rule_created"]
    other = client_for(make_user(roles.HR))
    assert other.post(RULES, {"code": "Y", "name": "y", "duration_minutes": 5}).status_code == 403
    assert other.put(f"{PRIORITY_RULES}HIGH/", {"rule_code": "PRIORITY_HIGH"}).status_code == 403
    assert str(PrioritySla.objects.get()) == "HIGH -> PRIORITY_HIGH"


def test_a_manual_task_uses_its_priority_rule_from_assignment_not_the_daily_schedule(
    ops, new_task, template, ist, high_4h
):
    """Even a manual task of a fixed-time type (Feed Upload) raised at 15:00 runs from 15:00 on
    its priority rule: the daily 10:00 schedule never decides a manual task's deadline."""
    with time_machine.travel(ist(2026, 10, 5, 15, 0), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], template=template("FEED_UPLOAD"),
                        priority="HIGH")
    clock = _clock(task)
    assert clock.trigger == "ASSIGNMENT" and clock.rule.code == "PRIORITY_HIGH"
    assert clock.start_at == ist(2026, 10, 5, 15, 0) and clock.due_at == ist(2026, 10, 5, 19, 0)


def test_an_ad_hoc_manual_task_gets_its_priority_sla(ops, new_task, ist, high_4h):
    with time_machine.travel(ist(2026, 10, 5, 11, 0), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], priority="HIGH")
    assert _clock(task).due_at == ist(2026, 10, 5, 15, 0)


def test_without_a_mapping_the_task_type_sla_is_unchanged(ops, new_task, template, ist, high_4h):
    with time_machine.travel(ist(2026, 10, 5, 11, 0), tick=False):
        medium = new_task(ops["manager"], ops["rahul_emp"], template=template("FEED_UPLOAD"))
        adhoc = new_task(ops["manager"], ops["rahul_emp"], priority="MEDIUM")
    assert _clock(medium).trigger == "FIXED_TIME"
    assert _clock(medium).start_at == ist(2026, 10, 5, 10, 0)  # approved task-type behaviour
    assert not TaskSla.objects.filter(task=adhoc).exists()  # ad-hoc, no mapping: no SLA


def test_a_switched_off_or_retired_mapping_falls_back(admin_user, ops, new_task, ist, high_4h):
    services.set_priority_rule(actor=admin_user, priority="HIGH", rule_code="", is_active=False)
    with time_machine.travel(ist(2026, 10, 5, 11, 0), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], priority="HIGH")
    assert not TaskSla.objects.filter(task=task).exists()
    services.set_priority_rule(actor=admin_user, priority="HIGH", rule_code="PRIORITY_HIGH")
    SlaRule.objects.filter(code="PRIORITY_HIGH").update(is_active=False)
    with time_machine.travel(ist(2026, 10, 5, 11, 0), tick=False):
        retired = new_task(ops["manager"], ops["rahul_emp"], priority="HIGH")
    assert not TaskSla.objects.filter(task=retired).exists()


def test_changing_priority_later_never_moves_the_deadline(client_for, ops, new_task, ist, high_4h):
    with time_machine.travel(ist(2026, 10, 5, 11, 0), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], priority="HIGH")
        task_services.update_task(actor=ops["manager"], task=task, version=task.version,
                                  priority="LOW")
    assert _clock(task).due_at == ist(2026, 10, 5, 15, 0)


def test_the_creation_preview_follows_the_priority_rule(client_for, ops, template, ist, high_4h):
    with time_machine.travel(ist(2026, 10, 5, 15, 0), tick=False):
        client = client_for(ops["manager"])
        body = {"assigned_to": ops["rahul_emp"].pk, "template": template("FEED_UPLOAD").pk}
        high = client.post(f"{TASKS}sla-preview/", {**body, "priority": "HIGH"}).json()
        medium = client.post(f"{TASKS}sla-preview/", {**body, "priority": "MEDIUM"}).json()
    assert high["resolution"]["rule_code"] == "PRIORITY_HIGH"
    assert high["resolution"]["due_at"] == "2026-10-05T19:00:00+05:30"
    assert medium["resolution"]["rule_code"] == "FEED_UPLOAD_2H"  # unchanged without a mapping
