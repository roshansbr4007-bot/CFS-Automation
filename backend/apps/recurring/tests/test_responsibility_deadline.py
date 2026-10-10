"""Responsibility deadline (SLA): defined by HR / Admin on a responsibility; scheduled tasks
generated from it use it (over the task type and priority SLA); manual tasks keep the priority
SLA (Critical 8 h, High 24 h, Medium 48 h, Low 72 h). Clock start = the occurrence's SCHEDULED
time (approved S1; it was the generation time before the scheduling fix), even when generated late.
"""

from datetime import date, datetime, time, timedelta
from io import StringIO

import pytest
import time_machine
from django.core.management import call_command

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.core.timeutils import IST
from apps.org.models import Department
from apps.recurring import generator, services
from apps.recurring.models import Responsibility
from apps.sla.models import SlaRule, TaskSla
from apps.tasks.models import Task, TaskCategory, TaskTemplate

pytestmark = pytest.mark.django_db
RESP = "/api/v1/responsibilities/"
SETUP = "/api/v1/responsibilities/setup/"
PRIORITY_HOURS = {"URGENT": 8, "HIGH": 24, "MEDIUM": 48, "LOW": 72}


def _at(*parts):
    return time_machine.travel(datetime(*parts, tzinfo=IST), tick=False)


def _configure_priority_sla():
    call_command("configure_priority_sla", stdout=StringIO())


def _patch(client, r, **body):
    r.refresh_from_db()
    return client.patch(f"{RESP}{r.pk}/", {"version": r.version, **body}, format="json")


@pytest.fixture
def feed():
    return Responsibility.objects.get(code="FEED_UPLOAD")  # OPS


@pytest.fixture
def duty(admin_user, ops, own):
    """duty(code, template=None): a daily OPS responsibility owned by Rahul, from 5 Oct 2026."""

    def _duty(code, template=None):
        r = services.create_responsibility(
            actor=admin_user, code=code, name=code.title(),
            department=Department.objects.get(code="OPS"),
            category=TaskCategory.objects.filter(is_active=True).first(),
            template=template, priority="LOW",
        )
        own(r, ops["rahul_emp"])
        services.create_schedule(
            actor=admin_user, responsibility=r, title=code.title(), frequency="DAILY",
            run_time=time(10, 0), effective_from=date(2026, 10, 5),
        )
        return r

    return _duty


def _set_deadline(actor, r, minutes):
    r.refresh_from_db()
    return services.update_responsibility(
        actor=actor, responsibility=r, version=r.version, deadline_minutes=minutes
    )


def _generate(r, *at):
    with _at(*at):
        generator.generate_due_occurrences()
    return Task.objects.filter(responsibility=r).order_by("id").last()


def _resolution(task):
    return TaskSla.objects.get(task=task, kind="RESOLUTION")


# --- who may define it -----------------------------------------------------------------------


@pytest.mark.parametrize("who", ["admin", "hr"])
def test_hr_and_admin_set_edit_and_clear_the_deadline(who, admin_client, client_for, make_user,
                                                       feed):
    client = admin_client if who == "admin" else client_for(make_user(roles.HR))
    created = _patch(client, feed, deadline_minutes=120)
    assert created.status_code == 200
    body = created.json()
    assert (body["deadline_minutes"], body["can_manage_deadline"]) == (120, True)
    code = f"RESP_{feed.pk}"
    assert SlaRule.objects.get(code=code, is_active=True).duration_minutes == 120
    edited = _patch(client, feed, deadline_minutes=90)
    assert edited.status_code == 200 and edited.json()["deadline_minutes"] == 90
    versions = list(SlaRule.objects.filter(code=code).order_by("version")
                    .values_list("version", "duration_minutes", "is_active"))
    assert versions == [(1, 120, False), (2, 90, True)]  # versioned via supersede_rule
    cleared = _patch(client, feed, deadline_minutes=None)
    assert cleared.status_code == 200 and cleared.json()["deadline_minutes"] is None
    feed.refresh_from_db()
    assert feed.deadline_rule_code == "" and SlaRule.objects.filter(code=code).count() == 2
    actions = list(AuditLog.objects.filter(entity_type="responsibility", entity_id=str(feed.pk))
                   .order_by("id").values_list("action", flat=True))
    assert actions[-3:] == [
        "responsibility.deadline_set", "responsibility.deadline_set",
        "responsibility.deadline_cleared",
    ]


def test_operations_manager_cannot_set_edit_or_clear_it(client_for, admin_user, ops, feed):
    manager = client_for(ops["manager"])
    assert _patch(manager, feed, deadline_minutes=120).status_code == 403
    _set_deadline(admin_user, feed, 120)
    assert _patch(manager, feed, deadline_minutes=60).status_code == 403
    assert _patch(manager, feed, deadline_minutes=None).status_code == 403
    renamed = _patch(manager, feed, name="Feed upload (morning)")  # other edits still allowed
    assert renamed.status_code == 200
    body = renamed.json()
    assert (body["deadline_minutes"], body["can_manage_deadline"]) == (120, False)


def test_employee_has_no_access(client_for, ops, feed):
    rahul = client_for(ops["rahul"])
    assert rahul.get(RESP).status_code == 403
    assert _patch(rahul, feed, deadline_minutes=120).status_code == 403
    feed.refresh_from_db()
    assert feed.deadline_rule_code == ""


def test_setup_with_a_deadline_is_hr_and_admin_only(client_for, make_user, ops):
    body = {
        "name": "Birthday Wishes", "description": "", "priority": "LOW",
        "department": Department.objects.get(code="OPS").pk,
        "category": TaskCategory.objects.filter(is_active=True).first().pk,
        "schedule": {"frequency": "DAILY", "run_time": "10:00", "effective_from": "2030-01-01"},
    }
    hr = client_for(make_user(roles.HR))
    made = hr.post(SETUP, {**body, "code": "BIRTHDAY_HR", "deadline_minutes": 120}, format="json")
    assert made.status_code == 201 and made.json()["deadline_minutes"] == 120
    manager = client_for(ops["manager"])
    refused = manager.post(SETUP, {**body, "code": "BIRTHDAY_OM", "deadline_minutes": 120},
                           format="json")
    assert refused.status_code == 403
    assert not Responsibility.objects.filter(code="BIRTHDAY_OM").exists()  # rolled back
    plain = manager.post(SETUP, {**body, "code": "BIRTHDAY_OM2"}, format="json")
    assert plain.status_code == 201 and plain.json()["deadline_minutes"] is None


def test_archived_responsibilities_and_invalid_values_are_refused(admin_client, feed):
    assert _patch(admin_client, feed, deadline_minutes=0).status_code == 400
    assert _patch(admin_client, feed, is_active=False).status_code == 200
    archived = _patch(admin_client, feed, deadline_minutes=120)
    assert archived.status_code == 409 and archived.json()["code"] == "responsibility_inactive"
    listed = next(r for r in admin_client.get(RESP).json() if r["id"] == feed.pk)
    assert (listed["deadline_minutes"], listed["can_manage_deadline"]) == (None, False)


# --- scheduled tasks ----------------------------------------------------------------------------


def test_a_scheduled_task_uses_the_responsibility_deadline_from_the_scheduled_time(
    admin_user, duty, ist
):
    """Approved S1 (scheduling fix): the responsibility deadline runs from the scheduled time,
    not from generation. Before the fix this clock started at 10:20 (generation, ASSIGNMENT)."""
    _configure_priority_sla()  # the priority SLA exists, but must not win
    broker = TaskTemplate.objects.get(code="BROKER_MAPPING")  # own 24 h SLA, acknowledgment
    r = duty("BIRTHDAY_WISHES_DUTY", template=broker)
    _set_deadline(admin_user, r, 120)
    task = _generate(r, 2026, 10, 5, 10, 20)  # generated 20 minutes after the 10:00 run time
    clock = _resolution(task)
    assert clock.rule_snapshot["code"] == f"RESP_{r.pk}"
    assert clock.trigger == "FIXED_TIME"
    assert clock.start_at == ist(2026, 10, 5, 10, 0)  # the scheduled time, not 10:20
    assert task.assigned_at == ist(2026, 10, 5, 10, 20)  # generation is still recorded as is
    assert clock.due_at - clock.start_at == timedelta(hours=2)
    assert task.acknowledgment_required is True  # the task type's acknowledgment still applies
    assert TaskSla.objects.filter(task=task, kind="ACK").exists()


@pytest.mark.parametrize(("template_code", "expected_rule"), [
    (None, "PRIORITY_LOW_72H"),  # Change Set 1: no task type -> priority SLA
    ("FEED_UPLOAD", "FEED_UPLOAD_2H"),  # task type -> the task type's SLA
])
def test_without_a_deadline_the_existing_behaviour_is_kept(duty, template_code, expected_rule):
    _configure_priority_sla()
    template = TaskTemplate.objects.get(code=template_code) if template_code else None
    r = duty(f"NO_DEADLINE_{template_code or 'NONE'}", template=template)
    task = _generate(r, 2026, 10, 5, 10, 0)
    assert _resolution(task).rule_snapshot["code"] == expected_rule


def test_changing_the_deadline_never_moves_existing_clocks(admin_user, duty):
    r = duty("DEADLINE_HISTORY")
    _set_deadline(admin_user, r, 120)
    first = _generate(r, 2026, 10, 5, 10, 0)
    before = list(TaskSla.objects.filter(task=first).order_by("id").values())
    _set_deadline(admin_user, r, 60)
    _set_deadline(admin_user, r, None)
    assert list(TaskSla.objects.filter(task=first).order_by("id").values()) == before
    assert _resolution(first).rule_snapshot["duration_minutes"] == 120
    _set_deadline(admin_user, r, 60)
    second = _generate(r, 2026, 10, 6, 10, 0)
    second_clock = _resolution(second)
    assert second_clock.due_at - second_clock.start_at == timedelta(hours=1)
    assert second_clock.rule.version == 2


# --- manual tasks: the priority SLA, unchanged ----------------------------------------------------


@pytest.mark.parametrize("priority", list(PRIORITY_HOURS))
def test_manual_tasks_keep_the_priority_sla(admin_user, ops, new_task, ist, duty, priority):
    _configure_priority_sla()
    _set_deadline(admin_user, duty("UNRELATED_DEADLINE"), 120)  # a deadline exists elsewhere
    with _at(2026, 10, 5, 11, 0):
        task = new_task(ops["manager"], ops["rahul_emp"], priority=priority)
    clock = _resolution(task)
    assert clock.rule_snapshot["code"].startswith("PRIORITY_")
    assert clock.due_at - clock.start_at == timedelta(hours=PRIORITY_HOURS[priority])
    assert clock.start_at == ist(2026, 10, 5, 11, 0)
