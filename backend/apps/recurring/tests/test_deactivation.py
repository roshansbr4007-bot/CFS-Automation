"""Change Set 1 (D6) — deactivating (archiving) a responsibility.

Uses the existing update_responsibility() and its audit. Everything is kept (owners, schedules,
occurrences, tasks, audit); nothing new is generated; the archived responsibility no longer
changes (no edits, owners or schedules). Reactivation is not offered in the UI.
"""

from datetime import datetime

import pytest
import time_machine

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.core.timeutils import IST
from apps.org.models import Department
from apps.recurring import generator, services
from apps.recurring.models import (
    RecurringSchedule,
    Responsibility,
    ResponsibilityOwner,
    ScheduleOccurrence,
)
from apps.tasks.models import Task, TaskCategory

pytestmark = pytest.mark.django_db
RESP = "/api/v1/responsibilities/"
SCHED = "/api/v1/recurring-schedules/"


def _at(*parts):
    return time_machine.travel(datetime(*parts, tzinfo=IST), tick=False)


def _deactivate(client, responsibility):
    responsibility.refresh_from_db()
    return client.patch(f"{RESP}{responsibility.pk}/",
                        {"version": responsibility.version, "is_active": False}, format="json")


@pytest.fixture
def feed(resp):
    return resp("FEED_UPLOAD")  # OPS


@pytest.fixture
def rm_duty(admin_user):
    return services.create_responsibility(
        actor=admin_user, code="RM_DUTY", name="RM duty",
        department=Department.objects.get(code="RM"),
        category=TaskCategory.objects.filter(is_active=True).first(),
    )


# --- who may deactivate --------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["admin", "hr", "manager"])
def test_admin_hr_and_own_department_manager_deactivate(who, admin_client, client_for, make_user,
                                                        ops, feed):
    client = {"admin": admin_client, "hr": client_for(make_user(roles.HR)),
              "manager": client_for(ops["manager"])}[who]
    with _at(2026, 10, 5, 9, 0):
        response = _deactivate(client, feed)
    assert response.status_code == 200 and response.json()["is_active"] is False
    assert AuditLog.objects.filter(action="responsibility.deactivated",
                                   entity_id=str(feed.pk)).count() == 1


def test_manager_cannot_deactivate_another_department_and_employee_never(client_for, ops, rm_duty,
                                                                        make_user):
    with _at(2026, 10, 5, 9, 0):
        assert _deactivate(client_for(ops["manager"]), rm_duty).status_code == 404  # not visible
        assert _deactivate(client_for(ops["rahul"]), rm_duty).status_code == 403
        assert _deactivate(client_for(make_user(roles.HR)), rm_duty).status_code == 200
    rm_duty.refresh_from_db()
    assert rm_duty.is_active is False


# --- what is kept, and what stops ----------------------------------------------------------------


def test_everything_is_kept_and_nothing_new_is_generated(admin_client, ops, feed, own):
    own(feed, ops["rahul_emp"])
    with _at(2026, 10, 5, 10, 0):
        generator.generate_due_occurrences()
    kept = {
        "owners": ResponsibilityOwner.objects.filter(responsibility=feed).count(),
        "schedules": list(RecurringSchedule.objects.filter(responsibility=feed).values()),
        "occurrences": ScheduleOccurrence.objects.filter(schedule__responsibility=feed).count(),
        "tasks": list(Task.objects.filter(responsibility=feed).values("id", "assigned_to_id")),
        # generator audit for THIS responsibility (other seeded duties keep writing their own)
        "audit": AuditLog.objects.filter(context__responsibility_id=feed.pk).count(),
    }
    assert kept["occurrences"] == 1 and len(kept["tasks"]) == 1
    with _at(2026, 10, 5, 11, 0):
        assert _deactivate(admin_client, feed).status_code == 200
    for day in (6, 7, 8):  # later working days: nothing generated, nothing recorded
        with _at(2026, 10, day, 10, 30):
            generator.generate_due_occurrences()
    assert ResponsibilityOwner.objects.filter(responsibility=feed).count() == kept["owners"]
    assert list(RecurringSchedule.objects.filter(responsibility=feed).values()) == kept["schedules"]
    assert ScheduleOccurrence.objects.filter(schedule__responsibility=feed).count() == 1
    tasks = list(Task.objects.filter(responsibility=feed).values("id", "assigned_to_id"))
    assert tasks == kept["tasks"]
    assert AuditLog.objects.filter(context__responsibility_id=feed.pk).count() == kept["audit"]
    assert AuditLog.objects.filter(action="responsibility.deactivated",
                                   entity_id=str(feed.pk)).count() == 1


def test_a_deactivated_monthly_responsibility_generates_nothing(admin_client, ops, own):
    monthly = RecurringSchedule.objects.filter(frequency="MONTHLY").first().responsibility
    own(monthly, ops["rahul_emp"])
    with _at(2026, 10, 5, 9, 0):
        assert _deactivate(admin_client, monthly).status_code == 200
    with _at(2026, 10, 30, 18, 0):  # after its monthly date
        generator.generate_due_occurrences()
    assert not ScheduleOccurrence.objects.filter(schedule__responsibility=monthly).exists()


# --- an archived responsibility no longer changes ------------------------------------------------


def test_no_owner_schedule_or_edit_changes_after_deactivation(admin_client, client_for, make_user,
                                                              ops, feed):
    with _at(2026, 10, 5, 9, 0):
        assert _deactivate(admin_client, feed).status_code == 200
        hr = client_for(make_user(roles.HR))
        owner = {"employee": ops["rahul_emp"].pk, "effective_from": "2026-10-05"}
        for client in (admin_client, hr):
            assigned = client.post(f"{RESP}{feed.pk}/owners/", owner, format="json")
            assert assigned.status_code == 409
            assert assigned.json()["code"] == "responsibility_inactive"
        ended = admin_client.post(f"{RESP}{feed.pk}/end-ownership/", {"last_day": "2026-10-10"})
        assert ended.status_code == 409
        body = {"responsibility": feed.pk, "title": "Second", "frequency": "DAILY",
                "run_time": "15:00", "effective_from": "2026-10-05"}
        assert admin_client.post(SCHED, body).status_code == 409
        schedule = RecurringSchedule.objects.get(responsibility=feed)
        patch = {"version": schedule.version, "run_time": "11:00"}
        assert admin_client.patch(f"{SCHED}{schedule.pk}/", patch).status_code == 409
        feed.refresh_from_db()
        renamed = admin_client.patch(f"{RESP}{feed.pk}/", {"version": feed.version, "name": "x"})
        assert renamed.status_code == 409
        same = admin_client.patch(f"{RESP}{feed.pk}/",
                                  {"version": feed.version, "name": feed.name})
        assert same.status_code == 200  # a no-op edit still answers unchanged
    assert ResponsibilityOwner.objects.filter(responsibility=feed).count() == 0
    assert RecurringSchedule.objects.filter(responsibility=feed).count() == 1
    assert Responsibility.objects.get(pk=feed.pk).name == "Feed Upload"
