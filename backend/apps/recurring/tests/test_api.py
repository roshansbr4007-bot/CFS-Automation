"""Responsibility, ownership, schedule and occurrence APIs (Phase 5)."""

import pytest
import time_machine

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.core.errors import FieldValidationError
from apps.org.models import Department
from apps.org.tests.factories import EmployeeFactory
from apps.recurring import generator, services
from apps.recurring.models import RecurringSchedule, Responsibility, ScheduleOccurrence
from apps.tasks.models import TaskCategory

pytestmark = pytest.mark.django_db
RESP = "/api/v1/responsibilities/"
SCHED = "/api/v1/recurring-schedules/"
OCC = "/api/v1/schedule-occurrences/"


def _dept(code):
    return Department.objects.get(code=code)


def _new(client, department="OPS", code="TXN_SHEET"):
    return client.post(RESP, {
        "code": code, "name": "Transaction Sheet Update", "department": _dept(department).pk,
        "category": TaskCategory.objects.get(code="OPERATIONS").pk,
    })


# --- responsibilities -------------------------------------------------------------------------


def test_scope_by_role(client_for, make_user, ops):
    rm_resp = Responsibility.objects.create(
        code="RM_DUTY", name="RM duty", department=_dept("RM"),
        category=TaskCategory.objects.get(code="SALES"),
    )
    hr_codes = {r["code"] for r in client_for(make_user(roles.HR)).get(RESP).json()}
    manager_codes = {r["code"] for r in client_for(ops["manager"]).get(RESP).json()}
    assert "RM_DUTY" in hr_codes and "FEED_UPLOAD" in hr_codes
    assert "FEED_UPLOAD" in manager_codes and "RM_DUTY" not in manager_codes
    assert client_for(ops["manager"]).get(f"{RESP}{rm_resp.pk}/").status_code == 404
    assert client_for(ops["rahul"]).get(RESP).status_code == 403  # employees: no admin view


def test_admin_hr_and_ops_manager_create_other_departments_cannot(
    client_for, admin_client, ops, make_user
):
    assert _new(admin_client, "RM", "RM_NEW").status_code == 201
    created = _new(client_for(ops["manager"]))
    assert created.status_code == 201 and created.json()["current_owner"] is None
    assert _new(client_for(ops["manager"]), "RM", "RM_X").status_code == 403
    # Phase A (D1): HR manages responsibilities organisation-wide.
    assert _new(client_for(make_user(roles.HR)), "OPS", "HR_X").status_code == 201
    assert _new(admin_client, code="TXN_SHEET").status_code == 409  # code taken
    assert AuditLog.objects.filter(action="responsibility.created").count() == 3


def test_edit_activate_deactivate_with_versions_and_audit(admin_client, ops, client_for):
    feed = Responsibility.objects.get(code="FEED_UPLOAD")
    url = f"{RESP}{feed.pk}/"
    edited = admin_client.patch(url, {"version": feed.version, "name": "NAV Feed Upload"})
    assert edited.status_code == 200 and edited.json()["name"] == "NAV Feed Upload"
    stale = admin_client.patch(f"{RESP}{feed.pk}/", {"version": feed.version, "name": "x"})
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
    off = admin_client.patch(f"{RESP}{feed.pk}/", {"version": feed.version + 1, "is_active": False})
    assert off.json()["is_active"] is False
    manager = client_for(ops["manager"])
    moved = manager.patch(url, {"version": off.json()["version"], "department": _dept("RM").pk})
    assert moved.status_code == 403  # only Admin moves a responsibility to another department
    rows = AuditLog.objects.filter(entity_type="responsibility").order_by("id")
    actions = list(rows.values_list("action", flat=True))
    assert actions == ["responsibility.updated", "responsibility.deactivated"]
    unchanged = admin_client.patch(
        url, {"version": off.json()["version"], "name": "NAV Feed Upload"}
    )
    assert unchanged.json()["version"] == off.json()["version"]  # nothing changed, no audit
    assert str(feed) == "Feed Upload"


# --- ownership --------------------------------------------------------------------------------


def test_ownership_assign_change_history_and_end(client_for, ops, ist):
    feed = Responsibility.objects.get(code="FEED_UPLOAD")
    manager = client_for(ops["manager"])
    url = f"{RESP}{feed.pk}/owners/"
    with time_machine.travel(ist(2026, 10, 1, 9, 0), tick=False):
        first = manager.post(url, {"employee": ops["rahul_emp"].pk, "effective_from": "2026-10-01"})
        assert first.status_code == 201
        again = {"employee": ops["rahul_emp"].pk, "effective_from": "2026-10-05"}
        assert manager.post(url, again).status_code == 409
        # (Phase 5.2: a change effective on the current owner's own start date - today - is now
        # an approved same-day correction; see test_owner_correction.py.)
        past = manager.post(url, {"employee": ops["amit_emp"].pk, "effective_from": "2026-09-30"})
        assert past.status_code == 400 and "effective_from" in past.json()["fields"]
        inactive = EmployeeFactory(is_active=False)
        to_inactive = {"employee": inactive.pk, "effective_from": "2026-10-02"}
        assert manager.post(url, to_inactive).status_code == 400
        handover = {"employee": ops["amit_emp"].pk, "effective_from": "2026-10-10", "note": "Rota"}
        change = manager.post(url, handover)
        assert change.status_code == 201
        detail = manager.get(f"{RESP}{feed.pk}/").json()
        assert detail["current_owner"]["employee"]["id"] == ops["rahul_emp"].pk  # until 9 Oct
    history = manager.get(url).json()
    assert [(h["employee"]["id"], h["effective_from"], h["effective_to"]) for h in history] == [
        (ops["rahul_emp"].pk, "2026-10-01", "2026-10-09"),
        (ops["amit_emp"].pk, "2026-10-10", None),
    ]
    changed = AuditLog.objects.get(action="responsibility.owner_changed")
    assert changed.old_value["employee_id"] == ops["rahul_emp"].pk
    assert changed.new_value == {"employee_id": ops["amit_emp"].pk, "effective_from": "2026-10-10"}
    with time_machine.travel(ist(2026, 10, 12, 9, 0), tick=False):
        end_url = f"{RESP}{feed.pk}/end-ownership/"
        assert manager.post(end_url, {"last_day": "2026-10-11"}).status_code == 400  # past
        ended = manager.post(end_url, {"last_day": "2026-10-20", "note": "Transferred"})
        assert ended.status_code == 200 and ended.json()["effective_to"] == "2026-10-20"
        assert manager.post(end_url, {"last_day": "2026-10-25"}).status_code == 409  # none open
    assert AuditLog.objects.filter(action="responsibility.owner_ended").exists()
    assert AuditLog.objects.filter(action="responsibility.owner_assigned").count() == 1


def test_end_ownership_cannot_precede_the_start(client_for, ops, ist):
    feed = Responsibility.objects.get(code="FEED_UPLOAD")
    manager = client_for(ops["manager"])
    with time_machine.travel(ist(2026, 10, 1, 9, 0), tick=False):
        future = {"employee": ops["rahul_emp"].pk, "effective_from": "2026-10-08"}
        manager.post(f"{RESP}{feed.pk}/owners/", future)
        early = manager.post(f"{RESP}{feed.pk}/end-ownership/", {"last_day": "2026-10-05"})
    assert early.status_code == 400 and "last_day" in early.json()["fields"]


def test_hr_reads_and_changes_ownership_organisation_wide(client_for, make_user, ops):
    feed = Responsibility.objects.get(code="FEED_UPLOAD")
    hr = client_for(make_user(roles.HR))
    assert hr.get(f"{RESP}{feed.pk}/owners/").status_code == 200
    body = {"employee": ops["rahul_emp"].pk, "effective_from": "2099-01-01"}
    assert hr.post(f"{RESP}{feed.pk}/owners/", body).status_code == 201  # Phase A (D1)


# --- schedules --------------------------------------------------------------------------------


def test_schedule_writes_and_validation(admin_client, client_for, make_user, ops):
    listed = client_for(make_user(roles.HR)).get(SCHED).json()
    assert {s["title"] for s in listed} >= {"Feed Upload", "Brokerage Calculation"}
    feed = Responsibility.objects.get(code="FEED_UPLOAD")
    body = {
        "responsibility": feed.pk, "title": "Evening feed", "frequency": "DAILY",
        "run_time": "17:00", "effective_from": "2026-11-01",
    }
    assert client_for(ops["rahul"]).post(SCHED, body).status_code == 403  # employees never write
    created = admin_client.post(SCHED, body)
    assert created.status_code == 201 and created.json()["non_working_day_policy"] == "SKIP"
    bad = admin_client.post(SCHED, {**body, "frequency": "MONTHLY"})
    assert bad.status_code == 400 and "day_of_month" in bad.json()["fields"]
    daily_with_day = admin_client.post(SCHED, {**body, "day_of_month": 5})
    assert daily_with_day.status_code == 400
    ends_early = admin_client.post(SCHED, {**body, "effective_to": "2026-10-01"})
    assert ends_early.status_code == 400 and "effective_to" in ends_early.json()["fields"]
    pk, version = created.json()["id"], created.json()["version"]
    edited = admin_client.patch(f"{SCHED}{pk}/", {"version": version, "run_time": "17:30"})
    assert edited.json()["run_time"] == "17:30:00"
    stale = admin_client.patch(f"{SCHED}{pk}/", {"version": version, "title": "x"})
    assert stale.status_code == 409
    off = admin_client.patch(f"{SCHED}{pk}/", {"version": version + 1, "is_active": False})
    assert off.json()["is_active"] is False
    same = admin_client.patch(f"{SCHED}{pk}/", {"version": version + 2, "is_active": False})
    assert same.json()["version"] == version + 2
    rows = AuditLog.objects.filter(entity_type="recurring_schedule").order_by("id")
    actions = list(rows.values_list("action", flat=True))
    assert actions == ["schedule.created", "schedule.updated", "schedule.deactivated"]
    schedule = RecurringSchedule.objects.get(pk=pk)
    assert str(schedule) == "Evening feed (DAILY)"
    assert client_for(ops["rahul"]).get(SCHED).status_code == 403


# --- occurrences ------------------------------------------------------------------------------


def test_occurrences_are_read_only_filterable_and_scoped(client_for, make_user, ops, ist):
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        generator.generate_due_occurrences()  # nobody owns anything: three SKIPPED
    hr = client_for(make_user(roles.HR))
    body = hr.get(OCC, {"status": "SKIPPED", "from": "2026-10-05", "to": "2026-10-05"}).json()
    assert body["count"] == 3
    feed = Responsibility.objects.get(code="FEED_UPLOAD")
    only_feed = hr.get(OCC, {"responsibility": feed.pk}).json()["results"]
    assert [o["responsibility"]["code"] for o in only_feed] == ["FEED_UPLOAD"]
    occurrence = ScheduleOccurrence.objects.get(schedule__responsibility=feed)
    assert hr.get(OCC, {"schedule": occurrence.schedule_id}).json()["count"] == 1
    detail = hr.get(f"{OCC}{occurrence.pk}/").json()
    assert detail["status"] == "SKIPPED" and detail["task"] is None
    for bad in ({"status": "LOST"}, {"from": "yesterday"}, {"responsibility": "x"}):
        assert hr.get(OCC, bad).status_code == 400
    assert hr.post(OCC, {}).status_code == 405
    assert client_for(ops["manager"]).get(OCC).json()["count"] == 3  # Operations scope
    assert client_for(ops["rahul"]).get(OCC).status_code == 403
    assert str(occurrence).endswith("SKIPPED")


# --- service guards ---------------------------------------------------------------------------


def test_services_refuse_unknown_fields_and_audit_type_changes(admin_user):
    feed = Responsibility.objects.get(code="FEED_UPLOAD")
    with pytest.raises(TypeError):
        services.update_responsibility(actor=admin_user, responsibility=feed, version=1, code="X")
    schedule = feed.schedules.first()
    with pytest.raises(TypeError):
        services.update_schedule(actor=admin_user, schedule=schedule, version=1, frequency_x=1)
    updated = services.update_responsibility(
        actor=admin_user, responsibility=feed, version=1, template=None, priority="HIGH",
        description="Upload the NAV feed",
    )
    assert updated.template is None and updated.priority == "HIGH"
    row = AuditLog.objects.get(action="responsibility.updated")
    assert row.new_value["template_id"] is None and row.new_value["priority"] == "HIGH"
    inactive = TaskCategory.objects.create(code="OLD", name="Old", is_active=False)
    with pytest.raises(FieldValidationError):
        services.update_responsibility(
            actor=admin_user, responsibility=feed, version=2, category=inactive
        )
    with pytest.raises(FieldValidationError):
        services.create_responsibility(
            actor=admin_user, code="NEW", name="New", department=feed.department, category=inactive
        )
