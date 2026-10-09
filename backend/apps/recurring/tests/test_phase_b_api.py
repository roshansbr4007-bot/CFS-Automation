"""Phase B — WEEKLY / ONCE through the API, with the unchanged Phase A permission model
(Admin global, HR organisation-wide, Operations Manager own department, Employee denied)."""

from datetime import datetime

import pytest
import time_machine

from apps.accounts import roles
from apps.core.timeutils import IST
from apps.org.models import Department
from apps.recurring import services
from apps.recurring.models import RecurringSchedule, Responsibility
from apps.tasks.models import TaskCategory

pytestmark = pytest.mark.django_db
SCHED = "/api/v1/recurring-schedules/"
SETUP = "/api/v1/responsibilities/setup/"
WEEKLY = {"title": "Weekly report", "frequency": "WEEKLY", "weekdays": [0, 3], "run_time": "10:00",
          "effective_from": "2026-10-05"}
ONCE = {"title": "Audit pack", "frequency": "ONCE", "run_date": "2026-10-14", "run_time": "10:00"}


@pytest.fixture(autouse=True)
def monday_morning():
    with time_machine.travel(datetime(2026, 10, 5, 9, 0, tzinfo=IST), tick=False):
        yield


@pytest.fixture
def rm_duty(admin_user):
    return services.create_responsibility(
        actor=admin_user, code="RM_WEEKLY", name="RM weekly",
        department=Department.objects.get(code="RM"),
        category=TaskCategory.objects.filter(is_active=True).first(),
    )


def _feed():
    return Responsibility.objects.get(code="FEED_UPLOAD")  # OPS


@pytest.mark.parametrize("who", ["admin", "hr", "manager"])
def test_weekly_and_once_create_and_update_by_role(who, admin_client, client_for, make_user, ops):
    client = {"admin": admin_client, "hr": client_for(make_user(roles.HR)),
              "manager": client_for(ops["manager"])}[who]
    weekly = client.post(SCHED, {**WEEKLY, "responsibility": _feed().pk}, format="json")
    assert weekly.status_code == 201
    assert (weekly.json()["weekdays"], weekly.json()["run_date"]) == ([0, 3], None)
    edited = client.patch(f"{SCHED}{weekly.json()['id']}/", {"version": 1, "weekdays": [4]},
                          format="json")
    assert edited.status_code == 200 and edited.json()["weekdays"] == [4]
    once = client.post(SCHED, {**ONCE, "responsibility": _feed().pk}, format="json")
    assert once.status_code == 201
    body = once.json()
    assert (body["run_date"], body["effective_from"], body["effective_to"]) == (
        "2026-10-14", "2026-10-14", "2026-10-14",
    )
    moved = client.patch(f"{SCHED}{body['id']}/", {"version": 1, "run_date": "2026-10-15"},
                         format="json")
    assert moved.status_code == 200 and moved.json()["effective_to"] == "2026-10-15"


def test_manager_cannot_touch_another_department_and_employees_never(client_for, ops, rm_duty):
    manager, rahul = client_for(ops["manager"]), client_for(ops["rahul"])
    assert manager.post(SCHED, {**WEEKLY, "responsibility": rm_duty.pk},
                        format="json").status_code == 404
    assert rahul.post(SCHED, {**ONCE, "responsibility": _feed().pk},
                      format="json").status_code == 403
    assert not RecurringSchedule.objects.filter(frequency__in=["WEEKLY", "ONCE"]).exists()


def test_validation_errors_use_the_field_keys(admin_client):
    def errors(body):
        response = admin_client.post(SCHED, {**body, "responsibility": _feed().pk}, format="json")
        assert response.status_code == 400
        return set(response.json()["fields"])

    assert errors({**WEEKLY, "weekdays": []}) == {"weekdays"}
    assert errors({**ONCE, "run_date": "2026-10-02"}) == {"run_date"}  # past
    assert errors({**ONCE, "run_date": "2026-10-10"}) == {"run_date"}  # non-working, SKIP
    daily = {"title": "x", "frequency": "DAILY", "run_time": "10:00"}
    assert errors(daily) == {"effective_from"}  # still required for every other frequency


def test_setup_with_weekly_and_once_and_section_keyed_errors(admin_client):
    base = {"name": "Weekly report", "description": "", "priority": "LOW",
            "department": Department.objects.get(code="OPS").pk,
            "category": TaskCategory.objects.filter(is_active=True).first().pk}
    weekly = admin_client.post(SETUP, {**base, "code": "WEEKLY_SETUP", "schedule": {
        "frequency": "WEEKLY", "weekdays": [2], "run_time": "10:00",
        "effective_from": "2026-10-05"}}, format="json")
    assert weekly.status_code == 201 and weekly.json()["active_schedule_count"] == 1
    once = admin_client.post(SETUP, {**base, "code": "ONCE_SETUP", "schedule": {
        "frequency": "ONCE", "run_date": "2026-10-14", "run_time": "10:00"}}, format="json")
    assert once.status_code == 201
    bad = admin_client.post(SETUP, {**base, "code": "BAD_SETUP", "schedule": {
        "frequency": "WEEKLY", "weekdays": [], "run_time": "10:00",
        "effective_from": "2026-10-05"}}, format="json")
    assert bad.status_code == 400 and "schedule.weekdays" in bad.json()["fields"]
    assert not Responsibility.objects.filter(code="BAD_SETUP").exists()  # rolled back
