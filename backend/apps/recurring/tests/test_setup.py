"""Phase A — responsibility management foundation.

One-step setup (responsibility + optional owner + first schedule, atomically), schedule
authority that follows responsibility authority (HR organisation-wide, Operations Manager own
department, Admin global), the non-Admin "no past start" rule, and the read-only
`active_schedule_count` / `can_manage` fields. The clock is fixed at Monday 5 Oct 2026 09:00 IST.
"""

from datetime import date, datetime

import pytest
import time_machine

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.core.timeutils import IST
from apps.org.models import Department
from apps.org.tests.factories import EmployeeFactory
from apps.recurring import services
from apps.recurring.models import RecurringSchedule, Responsibility, ResponsibilityOwner
from apps.tasks.models import TaskCategory, TaskTemplate

pytestmark = pytest.mark.django_db
SETUP = "/api/v1/responsibilities/setup/"
RESP = "/api/v1/responsibilities/"
SCHED = "/api/v1/recurring-schedules/"
TODAY = date(2026, 10, 5)


@pytest.fixture(autouse=True)
def monday_morning():
    with time_machine.travel(datetime(2026, 10, 5, 9, 0, tzinfo=IST), tick=False):
        yield


def _dept(code):
    return Department.objects.get(code=code)


def _body(code="BIRTHDAY_WISH", department="OPS", owner=None, **schedule):
    body = {
        "code": code,
        "name": "Birthday wish",
        "description": "Wish colleagues on their birthday.",
        "department": _dept(department).pk,
        "category": TaskCategory.objects.filter(is_active=True).first().pk,
        "priority": "LOW",
        "schedule": {
            "frequency": "DAILY",
            "run_time": "10:00",
            "effective_from": "2026-10-05",
            **schedule,
        },
    }
    if owner is not None:
        body["owner"] = owner
    return body


def _nothing_created(code):
    assert not Responsibility.objects.filter(code=code).exists()
    assert not RecurringSchedule.objects.filter(responsibility__code=code).exists()
    assert not ResponsibilityOwner.objects.filter(responsibility__code=code).exists()
    assert not AuditLog.objects.filter(
        action="responsibility.created", new_value__code=code
    ).exists()


# --- setup --------------------------------------------------------------------------------------


def test_admin_sets_up_responsibility_owner_and_schedule_at_once(admin_client, ops):
    template = TaskTemplate.objects.filter(is_active=True).first()
    body = _body(owner={"employee": ops["rahul_emp"].pk, "effective_from": "2026-10-05"})
    body["template"] = template.pk
    response = admin_client.post(SETUP, body, format="json")
    assert response.status_code == 201
    data = response.json()
    assert (data["code"], data["template"]["id"]) == ("BIRTHDAY_WISH", template.pk)
    assert data["current_owner"]["employee"]["id"] == ops["rahul_emp"].pk
    assert (data["active_schedule_count"], data["can_manage"]) == (1, True)
    schedule = RecurringSchedule.objects.get(responsibility__code="BIRTHDAY_WISH")
    assert schedule.title == "Birthday wish"  # defaults to the responsibility name
    assert (schedule.frequency, schedule.non_working_day_policy) == ("DAILY", "SKIP")
    actions = set(AuditLog.objects.values_list("action", flat=True))
    expected = {"responsibility.created", "responsibility.owner_assigned", "schedule.created"}
    assert expected <= actions


def test_hr_sets_up_in_any_department(client_for, make_user):
    hr = client_for(make_user(roles.HR))
    response = hr.post(SETUP, _body(code="RM_REVIEW", department="RM"), format="json")
    assert response.status_code == 201
    assert response.json()["current_owner"] is None  # owner is optional
    assert RecurringSchedule.objects.filter(responsibility__code="RM_REVIEW").count() == 1


def test_ops_manager_sets_up_only_in_own_department(client_for, ops):
    manager = client_for(ops["manager"])
    assert manager.post(SETUP, _body(code="OPS_OWN"), format="json").status_code == 201
    other = manager.post(SETUP, _body(code="RM_OTHER", department="RM"), format="json")
    assert other.status_code == 403
    _nothing_created("RM_OTHER")


def test_employee_cannot_set_up_or_manage_schedules(client_for, ops):
    rahul = client_for(ops["rahul"])
    assert rahul.post(SETUP, _body(code="NOPE"), format="json").status_code == 403
    feed_schedule = RecurringSchedule.objects.get(responsibility__code="FEED_UPLOAD")
    body = {"responsibility": feed_schedule.responsibility_id, "title": "x", "frequency": "DAILY",
            "run_time": "11:00", "effective_from": "2026-10-06"}
    assert rahul.post(SCHED, body).status_code == 403
    patch = {"version": feed_schedule.version, "run_time": "11:00"}
    assert rahul.patch(f"{SCHED}{feed_schedule.pk}/", patch).status_code == 403
    _nothing_created("NOPE")


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"schedule": {"frequency": "MONTHLY"}}, "schedule.day_of_month"),
        ({"owner_inactive": True}, "owner.employee"),
        ({"owner": {"effective_from": "2026-10-04"}}, "owner.effective_from"),
        ({"schedule": {"effective_from": "2026-10-04"}}, "schedule.effective_from"),
    ],
)
def test_any_failure_rolls_back_the_whole_setup(client_for, make_user, ops, change, field):
    hr = client_for(make_user(roles.HR))
    owner = {"employee": ops["rahul_emp"].pk, "effective_from": "2026-10-05"}
    if change.get("owner_inactive"):
        owner["employee"] = EmployeeFactory(is_active=False).pk
    owner.update(change.get("owner", {}))
    body = _body(owner=owner, **change.get("schedule", {}))
    response = hr.post(SETUP, body, format="json")
    assert response.status_code == 400
    assert field in response.json()["fields"]
    _nothing_created("BIRTHDAY_WISH")


def test_setup_input_errors_are_keyed_by_section(admin_client):
    body = _body()
    del body["schedule"]["run_time"]
    missing_time = admin_client.post(SETUP, body, format="json")
    assert missing_time.status_code == 400 and "schedule.run_time" in missing_time.json()["fields"]
    no_schedule = _body()
    del no_schedule["schedule"]
    response = admin_client.post(SETUP, no_schedule, format="json")
    assert response.status_code == 400 and "schedule" in response.json()["fields"]
    _nothing_created("BIRTHDAY_WISH")


def test_admin_may_still_start_a_schedule_in_the_past(admin_client):
    response = admin_client.post(SETUP, _body(effective_from="2026-10-01"), format="json")
    assert response.status_code == 201  # existing Admin behaviour preserved


# --- schedules for existing responsibilities -----------------------------------------------------


def test_schedule_create_and_update_follow_responsibility_scope(
    client_for, admin_user, make_user, ops
):
    feed = Responsibility.objects.get(code="FEED_UPLOAD")  # OPS
    rm = services.create_responsibility(
        actor=admin_user, code="RM_CHECK", name="RM check", department=_dept("RM"),
        category=TaskCategory.objects.filter(is_active=True).first(),
    )
    base = {"title": "Second run", "frequency": "DAILY", "run_time": "15:00",
            "effective_from": "2026-10-05"}
    manager = client_for(ops["manager"])
    own = manager.post(SCHED, {**base, "responsibility": feed.pk})
    assert own.status_code == 201 and own.json()["can_manage"] is True
    edited = manager.patch(f"{SCHED}{own.json()['id']}/", {"version": 1, "run_time": "15:30"})
    assert edited.status_code == 200 and edited.json()["run_time"] == "15:30:00"
    assert manager.post(SCHED, {**base, "responsibility": rm.pk}).status_code == 404  # not visible
    hr = client_for(make_user(roles.HR))
    created = hr.post(SCHED, {**base, "responsibility": rm.pk, "frequency": "MONTHLY",
                              "day_of_month": 20})
    assert created.status_code == 201
    hr_edit = hr.patch(f"{SCHED}{created.json()['id']}/", {"version": 1, "day_of_month": 21})
    assert hr_edit.status_code == 200 and hr_edit.json()["day_of_month"] == 21
    rm_schedule = RecurringSchedule.objects.get(pk=created.json()["id"])
    path = f"{SCHED}{rm_schedule.pk}/"
    assert manager.patch(path, {"version": 2, "run_time": "16:00"}).status_code == 404


def test_non_admins_cannot_start_or_move_a_schedule_into_the_past(client_for, make_user, ops):
    feed = Responsibility.objects.get(code="FEED_UPLOAD")
    body = {"responsibility": feed.pk, "title": "Late", "frequency": "DAILY", "run_time": "16:00",
            "effective_from": "2026-10-04"}
    hr = client_for(make_user(roles.HR))
    for client in (hr, client_for(ops["manager"])):
        response = client.post(SCHED, body)
        assert response.status_code == 400 and "effective_from" in response.json()["fields"]
    started = RecurringSchedule.objects.get(responsibility=feed)  # seeded: started 5 Oct
    RecurringSchedule.objects.filter(pk=started.pk).update(effective_from=date(2026, 9, 1))
    ok = hr.patch(f"{SCHED}{started.pk}/", {"version": started.version, "run_time": "10:15"})
    assert ok.status_code == 200  # an existing past start is kept when other fields change
    moved = hr.patch(f"{SCHED}{started.pk}/", {"version": started.version + 1,
                                                "effective_from": "2026-09-15"})
    assert moved.status_code == 400 and "effective_from" in moved.json()["fields"]


# --- read-only fields ----------------------------------------------------------------------------


def test_active_schedule_count_and_no_owner(admin_client, admin_user):
    r = services.create_responsibility(
        actor=admin_user, code="NO_SCHEDULE", name="No schedule yet", department=_dept("OPS"),
        category=TaskCategory.objects.filter(is_active=True).first(),
    )

    def row():
        listed = admin_client.get(RESP).json()
        return next(item for item in listed if item["code"] == "NO_SCHEDULE")

    assert (row()["active_schedule_count"], row()["current_owner"]) == (0, None)
    schedule = RecurringSchedule.objects.create(
        responsibility=r, title="Later", frequency="DAILY", run_time="10:00",
        effective_from=date(2026, 12, 1), created_by=admin_user, updated_by=admin_user,
    )
    assert row()["active_schedule_count"] == 1  # a future start still counts
    RecurringSchedule.objects.filter(pk=schedule.pk).update(
        effective_from=date(2026, 9, 1), effective_to=date(2026, 10, 4)
    )
    assert row()["active_schedule_count"] == 0  # ended yesterday
    RecurringSchedule.objects.filter(pk=schedule.pk).update(effective_to=None, is_active=False)
    assert row()["active_schedule_count"] == 0  # inactive


def test_can_manage_flags_follow_scope(client_for, admin_client, admin_user, make_user, ops):
    services.create_responsibility(
        actor=admin_user, code="RM_FLAG", name="RM flag", department=_dept("RM"),
        category=TaskCategory.objects.filter(is_active=True).first(),
    )
    manager_view = client_for(ops["manager"]).get(RESP).json()
    assert manager_view and all(item["department"]["code"] == "OPS" for item in manager_view)
    assert all(item["can_manage"] for item in manager_view)
    for client in (client_for(make_user(roles.HR)), admin_client):
        listed = client.get(RESP).json()
        assert {"RM_FLAG", "FEED_UPLOAD"} <= {item["code"] for item in listed}
        assert all(item["can_manage"] for item in listed)
        assert all(s["can_manage"] for s in client.get(SCHED).json())
    assert client_for(ops["rahul"]).get(RESP).status_code == 403
