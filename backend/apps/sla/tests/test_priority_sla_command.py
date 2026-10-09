"""Change Set 1 — priority-based SLA (D2/D3/D4) through the existing Phase 5.2 infrastructure.

The `configure_priority_sla` command creates the rules and mappings; every new MANUAL task and
every SCHEDULED task WITHOUT a task type then gets its resolution SLA from its priority. Scheduled
tasks with a task type keep that task type's SLA; existing clocks never change.
"""

from datetime import date, time, timedelta
from io import StringIO

import pytest
import time_machine
from django.core.management import call_command

from apps.audit.models import AuditLog
from apps.org.models import Department
from apps.recurring import generator
from apps.recurring import services as recurring_services
from apps.recurring.models import ResponsibilityOwner
from apps.sla import services as sla_services
from apps.sla.models import PrioritySla, SlaRule, TaskSla
from apps.tasks.models import Task, TaskCategory, TaskTemplate

pytestmark = pytest.mark.django_db
EXPECTED = {
    "URGENT": ("PRIORITY_URGENT_8H", 8 * 60),
    "HIGH": ("PRIORITY_HIGH_24H", 24 * 60),
    "MEDIUM": ("PRIORITY_MEDIUM_48H", 48 * 60),
    "LOW": ("PRIORITY_LOW_72H", 72 * 60),
}


def _configure(*args):
    out = StringIO()
    call_command("configure_priority_sla", *args, stdout=out)
    return out.getvalue()


def _resolution(task):
    return TaskSla.objects.get(task=task, kind="RESOLUTION")


# --- the command ---------------------------------------------------------------------------------


def test_creates_all_rules_and_mappings_and_is_idempotent():
    assert "8 change(s)" in _configure()
    for priority, (code, minutes) in EXPECTED.items():
        rule = SlaRule.objects.get(code=code)
        assert (rule.rule_type, rule.duration_minutes, rule.version) == ("DURATION", minutes, 1)
        mapping = PrioritySla.objects.get(priority=priority)
        assert (mapping.rule_code, mapping.is_active) == (code, True)
    audit = AuditLog.objects.filter(action__in=["sla.rule_created", "sla.priority_rule_set"])
    assert audit.count() == 8
    assert "0 change(s)" in _configure()  # idempotent
    assert SlaRule.objects.filter(code__startswith="PRIORITY_").count() == 4
    assert audit.count() == 8


def test_an_existing_mapping_is_kept_unless_forced(admin_user):
    sla_services.create_rule(actor=admin_user, code="OLD_HIGH_4H", name="Old high",
                             duration_minutes=240)
    sla_services.set_priority_rule(actor=admin_user, priority="HIGH", rule_code="OLD_HIGH_4H")
    out = _configure()
    assert "kept existing OLD_HIGH_4H" in out
    assert PrioritySla.objects.get(priority="HIGH").rule_code == "OLD_HIGH_4H"
    assert SlaRule.objects.filter(code="PRIORITY_HIGH_24H").exists()  # the rule is still created
    forced = _configure("--force")
    assert "mapping HIGH: replaced -> PRIORITY_HIGH_24H" in forced
    assert PrioritySla.objects.get(priority="HIGH").rule_code == "PRIORITY_HIGH_24H"


def test_dry_run_changes_nothing():
    out = _configure("--dry-run")
    assert "[dry run]" in out and "8 change(s)" in out
    assert not SlaRule.objects.filter(code__startswith="PRIORITY_").exists()
    assert not PrioritySla.objects.exists()


# --- new tasks -----------------------------------------------------------------------------------


@pytest.mark.parametrize("priority", list(EXPECTED))
def test_manual_tasks_get_their_priority_sla(ops, new_task, ist, priority):
    _configure()
    code, minutes = EXPECTED[priority]
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], priority=priority)
    clock = _resolution(task)
    assert clock.rule_snapshot["code"] == code
    assert clock.due_at - clock.start_at == timedelta(minutes=minutes)
    assert clock.start_at == ist(2026, 10, 5, 10, 0)  # starts at assignment


def test_the_preview_follows_the_priority(client_for, ops, ist):
    _configure()
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        preview = client_for(ops["manager"]).post(
            "/api/v1/tasks/sla-preview/",
            {"assigned_to": ops["rahul_emp"].pk, "template": None, "priority": "URGENT"},
            format="json",
        ).json()
    assert preview["resolution"]["rule_code"] == "PRIORITY_URGENT_8H"
    assert preview["resolution"]["due_at"] == "2026-10-05T18:00:00+05:30"


def _scheduled_task(admin_user, ops, ist, *, code, template=None):
    responsibility = recurring_services.create_responsibility(
        actor=admin_user, code=code, name=code.title(),
        department=Department.objects.get(code="OPS"),
        category=TaskCategory.objects.filter(is_active=True).first(), template=template,
        priority="LOW",
    )
    ResponsibilityOwner.objects.create(responsibility=responsibility, employee=ops["rahul_emp"],
                                       effective_from=date(2026, 1, 1), assigned_by=admin_user)
    recurring_services.create_schedule(
        actor=admin_user, responsibility=responsibility, title=code.title(), frequency="DAILY",
        run_time=time(10, 0), effective_from=date(2026, 10, 5),
    )
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        generator.generate_due_occurrences()
    return Task.objects.get(responsibility=responsibility)


def test_a_scheduled_task_without_a_task_type_gets_the_priority_sla(admin_user, ops, ist):
    _configure()
    task = _scheduled_task(admin_user, ops, ist, code="BIRTHDAY_WISH")
    clock = _resolution(task)
    assert (task.source, task.template_id, task.priority) == ("SCHEDULED", None, "LOW")
    assert clock.rule_snapshot["code"] == "PRIORITY_LOW_72H"
    assert clock.due_at - clock.start_at == timedelta(hours=72)


def test_a_scheduled_task_with_a_task_type_keeps_the_task_type_sla(admin_user, ops, ist):
    _configure()
    feed = TaskTemplate.objects.get(code="FEED_UPLOAD")
    task = _scheduled_task(admin_user, ops, ist, code="TYPED_DUTY", template=feed)
    assert _resolution(task).rule_snapshot["code"] == "FEED_UPLOAD_2H"  # not PRIORITY_LOW_72H


def test_a_scheduled_task_without_a_task_type_and_no_mapping_has_no_sla(admin_user, ops, ist):
    task = _scheduled_task(admin_user, ops, ist, code="UNCONFIGURED")  # command never run
    assert not TaskSla.objects.filter(task=task, kind="RESOLUTION").exists()


def test_existing_clocks_never_change(ops, new_task, ist):
    broker = TaskTemplate.objects.get(code="BROKER_MAPPING")
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], template=broker, priority="MEDIUM")
    before = list(TaskSla.objects.filter(task=task).order_by("id").values())
    assert before  # an existing clock (task-type SLA)
    _configure()
    _configure("--force")
    assert list(TaskSla.objects.filter(task=task).order_by("id").values()) == before
