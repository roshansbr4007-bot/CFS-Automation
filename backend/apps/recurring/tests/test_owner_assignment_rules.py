"""Locked responsibility-owner rules A, B, D and E, through the real API, services, generator and
task workflow. Monday 5 Oct 2026; schedules run at 10:00 IST.

A  Only HR and Admin assign, change or end an owner (the Operations Manager keeps the other
   management actions of their department); enforced by the API and the services.
B  The owner belongs to the responsibility's department.
D  An owner who starts today gets today's eligible occurrence at once (generated or recovered by
   the existing generator, once), with its S1 clock at the scheduled time; it arrives overdue only
   when its deadline had passed.
E  Today's OPEN task of the replaced owner moves to the new owner through the existing
   reassignment (same task, deadline, status, comments, attachments, history; the previous owner
   is notified). Completed work, other days and later-dated owners are left alone.
"""

from datetime import date, time

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.org.models import Department
from apps.recurring import generator
from apps.recurring.models import Responsibility, ResponsibilityOwner, ScheduleOccurrence
from apps.sla.models import TaskSla
from apps.tasks import dependencies
from apps.tasks import services as task_services
from apps.tasks.models import Task, TaskAssignment, TaskCategory, TaskTemplate

from .scheduled_helpers import at, clock, duty, ist, only, run, task_of

pytestmark = pytest.mark.django_db
RESP = "/api/v1/responsibilities/"
SETUP = "/api/v1/responsibilities/setup/"
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"


@pytest.fixture
def hr(make_user):
    return make_user(roles.HR)


def _feed_type():
    return TaskTemplate.objects.get(code="FEED_UPLOAD")  # fixed time 10:00, 2 hours


def _owners(schedule):
    return f"{RESP}{schedule.responsibility_id}/owners/"


def _assign(client, schedule, employee, day="2026-10-05", note=""):
    return client.post(
        _owners(schedule), {"employee": employee.pk, "effective_from": day, "note": note}
    )


def _setup_body(owner=None):
    body = {
        "code": "BIRTHDAY_WISH", "name": "Birthday wish", "description": "",
        "department": Department.objects.get(code="OPS").pk,
        "category": TaskCategory.objects.filter(is_active=True).first().pk, "priority": "LOW",
        "schedule": {"frequency": "DAILY", "run_time": "10:00", "effective_from": "2026-10-05"},
    }
    if owner is not None:
        body["owner"] = owner
    return body


# --- A: who may assign ---------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["hr", "admin"])
def test_hr_and_admin_assign_an_owner_of_the_same_department(
    who, hr, admin_user, client_for, ops
):
    schedule = only(duty(None, admin_user, template=_feed_type()))
    actor = hr if who == "hr" else admin_user
    with at(2026, 10, 5, 9, 0):
        response = _assign(client_for(actor), schedule, ops["rahul_emp"])
    assert response.status_code == 201
    body = response.json()
    assert body["employee"]["id"] == ops["rahul_emp"].pk
    assert body["transferred_tasks"] == []
    assert [g["result"] for g in body["today_generation"]] == ["not_due"]  # 10:00 not yet
    assert not Task.objects.filter(schedule=schedule).exists()
    run(2026, 10, 5, 10, 0)  # the normal run still generates it at its run time
    assert task_of(schedule).assigned_to == ops["rahul_emp"]


def test_employees_and_operations_managers_cannot_assign_change_or_end_owners(
    hr, admin_user, client_for, ops
):
    schedule = only(duty(ops["rahul_emp"], admin_user, template=_feed_type()))
    with at(2026, 10, 5, 9, 0):
        employee = client_for(ops["amit"])
        manager = client_for(ops["manager"])
        end = f"{RESP}{schedule.responsibility_id}/end-ownership/"
        for client in (employee, manager):
            assert _assign(client, schedule, ops["amit_emp"]).status_code == 403
            assert client.post(end, {"last_day": "2026-10-20"}).status_code == 403
        refused = manager.post(SETUP, _setup_body({"employee": ops["amit_emp"].pk,
                                                   "effective_from": "2026-10-05"}),
                               format="json")
        assert refused.status_code == 403
        assert not Responsibility.objects.filter(code="BIRTHDAY_WISH").exists()  # all rolled back
        # The Operations Manager keeps the rest of the setup (no owner) for their department.
        assert manager.post(SETUP, _setup_body(), format="json").status_code == 201
        flags = {
            name: client.get(f"{RESP}{schedule.responsibility_id}/").json()["can_manage_owner"]
            for name, client in (("manager", manager), ("hr", client_for(hr)),
                                 ("admin", client_for(admin_user)))
        }
    assert flags == {"manager": False, "hr": True, "admin": True}
    assert list(ResponsibilityOwner.objects.filter(responsibility_id=schedule.responsibility_id)
                .values_list("employee", flat=True)) == [ops["rahul_emp"].pk]


# --- B: same department --------------------------------------------------------------------------


def test_an_owner_from_another_department_is_rejected(hr, admin_user, client_for, ops, staff):
    _, rm_employee = staff(roles.EMPLOYEE, department="RM")
    schedule = only(duty(None, admin_user, template=_feed_type()))
    with at(2026, 10, 5, 9, 0):
        client = client_for(hr)
        refused = _assign(client, schedule, rm_employee)
        assert refused.status_code == 400
        assert refused.json()["fields"]["employee"] == [
            "The owner must belong to the responsibility's department (OPS)."
        ]
        setup = client.post(SETUP, _setup_body({"employee": rm_employee.pk,
                                                "effective_from": "2026-10-05"}), format="json")
        assert setup.status_code == 400 and "owner.employee" in setup.json()["fields"]
        assert _assign(client, schedule, ops["rahul_emp"]).status_code == 201  # same department
    assert not Responsibility.objects.filter(code="BIRTHDAY_WISH").exists()
    assert list(ResponsibilityOwner.objects.filter(responsibility_id=schedule.responsibility_id)
                .values_list("employee", flat=True)) == [ops["rahul_emp"].pk]


def test_a_responsibility_with_an_owner_cannot_move_to_another_department(
    hr, admin_user, client_for, ops
):
    owned = only(duty(ops["rahul_emp"], admin_user, template=_feed_type()))
    unowned = duty(None, admin_user, template=_feed_type())
    rm = Department.objects.get(code="RM")
    with at(2026, 10, 5, 9, 0):
        client = client_for(hr)
        for schedule, expected in ((owned, 400), (unowned, 200)):
            r = Responsibility.objects.get(pk=schedule.responsibility_id)
            moved = client.patch(f"{RESP}{r.pk}/", {"version": r.version, "department": rm.pk},
                                 format="json")
            assert moved.status_code == expected
    assert Responsibility.objects.get(pk=owned.responsibility_id).department.code == "OPS"


# --- D: an owner assigned after today's run time -------------------------------------------------


@pytest.mark.parametrize("beat_ran", [True, False])
def test_late_assignment_generates_todays_task_once_from_the_scheduled_time(
    beat_ran, hr, admin_user, client_for, ops
):
    schedule = only(duty(None, admin_user, template=_feed_type()))
    if beat_ran:
        run(2026, 10, 5, 10, 0)  # nobody owned it at 10:00: SKIPPED
        assert ScheduleOccurrence.objects.get(schedule=schedule).status == "SKIPPED"
    with at(2026, 10, 5, 11, 0):
        body = _assign(client_for(hr), schedule, ops["rahul_emp"]).json()
    assert [g["result"] for g in body["today_generation"]] == [
        "recovered" if beat_ran else "generated"
    ]
    task = task_of(schedule)
    assert task.assigned_to == ops["rahul_emp"]
    resolution = clock(task)
    assert (resolution.start_at, resolution.due_at) == (ist(2026, 10, 5, 10, 0),
                                                        ist(2026, 10, 5, 12, 0))
    run(2026, 10, 5, 11, 1)  # the per-minute run afterwards: nothing new
    with at(2026, 10, 5, 11, 2):
        assert generator.generate_today(schedule.responsibility)[0]["result"] == "existing"
    assert Task.objects.filter(schedule=schedule).count() == 1
    assert ScheduleOccurrence.objects.filter(schedule=schedule).count() == 1
    with at(2026, 10, 5, 11, 5):
        detail = client_for(hr).get(f"/api/v1/tasks/{task.pk}/").json()
    # Scheduled time passed, deadline (12:00) not: active, not overdue on arrival.
    assert detail["arrived_overdue"] is False
    assert detail["sla"]["resolution"]["state"] == "WARNING"  # 65 of 120 minutes used
    assert detail["status"] == "PENDING"


def test_assignment_after_the_deadline_arrives_overdue(hr, admin_user, client_for, ops):
    schedule = only(duty(None, admin_user, template=_feed_type()))
    with at(2026, 10, 5, 12, 30):
        _assign(client_for(hr), schedule, ops["rahul_emp"])
        detail = client_for(hr).get(f"/api/v1/tasks/{task_of(schedule).pk}/").json()
    assert detail["arrived_overdue"] is True
    assert detail["sla"]["resolution"]["due_at"] == "2026-10-05T12:00:00+05:30"


def test_a_late_dependency_task_keeps_waiting_for_its_prerequisite(
    hr, admin_user, client_for, ops
):
    recon = TaskTemplate.objects.get(code="RECONCILIATION")  # waits on Brokerage Calculation
    schedule = only(duty(None, admin_user, template=recon))
    with at(2026, 10, 5, 12, 0):
        body = _assign(client_for(hr), schedule, ops["rahul_emp"]).json()
    assert [g["result"] for g in body["today_generation"]] == ["generated"]
    task = task_of(schedule)
    waiting = clock(task)
    assert (waiting.trigger, waiting.start_at) == ("DEPENDENCY", None)  # not backdated
    assert dependencies.is_waiting(task)


def test_a_generation_failure_is_reported_not_hidden(hr, admin_user, client_for, ops):
    kind = TaskTemplate.objects.create(
        code="BROKEN_TYPE", name="Broken", department=Department.objects.get(code="OPS"),
        resolution_rule_code="FEED_UPLOAD_2H", trigger="ASSIGNMENT",
    )
    schedule = only(duty(None, admin_user, template=kind))
    TaskTemplate.objects.filter(pk=kind.pk).update(is_active=False)  # generation will fail
    with at(2026, 10, 5, 11, 0):
        response = _assign(client_for(hr), schedule, ops["rahul_emp"])
    assert response.status_code == 201  # the owner change itself is saved
    assert [g["result"] for g in response.json()["today_generation"]] == ["failed"]
    assert ScheduleOccurrence.objects.get(schedule=schedule).status == "FAILED"
    assert not Task.objects.filter(schedule=schedule).exists()


# --- E: today's existing task moves to the new owner ---------------------------------------------


def test_changing_the_owner_moves_todays_open_task_with_its_history(
    hr, admin_user, client_for, ops
):
    schedule = only(duty(ops["rahul_emp"], admin_user, template=_feed_type()))
    run(2026, 10, 5, 10, 0)
    task = task_of(schedule)
    with at(2026, 10, 5, 10, 10):
        task_services.start_task(actor=ops["rahul"], task=task, version=task.version)
        task_services.add_comment(actor=ops["rahul"], task=task, body="Feed file received.")
        task_services.add_attachment(
            actor=ops["rahul"], task=task, upload=SimpleUploadedFile("feed.pdf", PDF)
        )
    before = TaskSla.objects.get(task=task, kind="RESOLUTION")
    with at(2026, 10, 5, 10, 30):
        response = _assign(client_for(hr), schedule, ops["amit_emp"], note="Rota change")
    assert response.status_code == 201
    body = response.json()
    assert [t["id"] for t in body["transferred_tasks"]] == [task.pk]
    assert [g["result"] for g in body["today_generation"]] == ["existing"]

    task.refresh_from_db()
    assert task.assigned_to == ops["amit_emp"] and task.status == "IN_PROGRESS"
    assert Task.objects.filter(schedule=schedule).count() == 1  # moved, not duplicated
    assert ScheduleOccurrence.objects.filter(schedule=schedule).count() == 1
    after = TaskSla.objects.get(task=task, kind="RESOLUTION")
    assert (after.pk, after.start_at, after.due_at, after.state, after.stopped_at) == (
        before.pk, before.start_at, before.due_at, before.state, None,
    )
    assert after.due_at == ist(2026, 10, 5, 12, 0)
    assert task.comments.count() == 1 and task.attachments.count() == 1
    hops = list(TaskAssignment.objects.filter(task=task).order_by("id")
                .values_list("from_employee", "to_employee", "assigned_by"))
    assert hops == [(None, ops["rahul_emp"].pk, task.created_by_id),
                    (ops["rahul_emp"].pk, ops["amit_emp"].pk, hr.pk)]
    moved = AuditLog.objects.get(action="task.reassigned", entity_id=str(task.pk))
    assert moved.actor_user == hr
    assert (moved.context["from_employee_id"], moved.context["to_employee_id"]) == (
        ops["rahul_emp"].pk, ops["amit_emp"].pk,
    )
    assert "Rota change" in moved.context["note"]
    changed = AuditLog.objects.get(action="responsibility.owner_changed")
    assert changed.actor_user == hr
    assert changed.context["transferred_task_ids"] == [task.pk]

    notices = Notification.objects.filter(kind="TASK_REASSIGNED")
    # Not linked: the previous owner may no longer open the task; its reference is in the title.
    assert [(n.recipient, n.task_id) for n in notices] == [(ops["rahul"], None)]
    assert task.reference in notices.get().title
    assert notices.get().email_status == "NOT_REQUIRED"
    run(2026, 10, 5, 10, 31)
    assert Task.objects.filter(schedule=schedule).count() == 1


def test_completed_work_and_other_days_never_move(hr, admin_user, client_for, ops):
    schedule = only(duty(ops["rahul_emp"], admin_user, template=_feed_type(),
                         start=date(2026, 10, 2)))
    run(2026, 10, 2, 10, 0)  # Friday: still open on Monday (carried over)
    run(2026, 10, 5, 10, 0)
    friday, monday = task_of(schedule, date(2026, 10, 2)), task_of(schedule, date(2026, 10, 5))
    with at(2026, 10, 5, 10, 20):
        task_services.start_task(actor=ops["rahul"], task=monday, version=monday.version)
        monday.refresh_from_db()
        task_services.complete_task(
            actor=ops["rahul"], task=monday, version=monday.version, work_response="Work done."
        )
    with at(2026, 10, 5, 10, 30):
        body = _assign(client_for(hr), schedule, ops["amit_emp"]).json()
    assert body["transferred_tasks"] == []
    friday.refresh_from_db()
    monday.refresh_from_db()
    assert (monday.assigned_to, monday.status) == (ops["rahul_emp"], "COMPLETED")
    assert (friday.assigned_to, friday.status) == (ops["rahul_emp"], "PENDING")
    assert Task.objects.filter(schedule=schedule).count() == 2
    assert not Notification.objects.filter(kind="TASK_REASSIGNED").exists()


def test_an_owner_starting_later_leaves_today_alone(hr, admin_user, client_for, ops):
    schedule = only(duty(ops["rahul_emp"], admin_user, template=_feed_type()))
    run(2026, 10, 5, 10, 0)
    with at(2026, 10, 5, 10, 30):
        body = _assign(client_for(hr), schedule, ops["amit_emp"], day="2026-10-06").json()
    assert (body["transferred_tasks"], body["today_generation"]) == ([], [])
    assert task_of(schedule).assigned_to == ops["rahul_emp"]
    run(2026, 10, 6, 10, 0)
    assert task_of(schedule, date(2026, 10, 6)).assigned_to == ops["amit_emp"]


def test_a_task_changed_meanwhile_fails_the_owner_change_as_a_whole(
    hr, admin_user, client_for, ops, monkeypatch
):
    """The move uses the existing versioned reassignment: if it cannot apply, nothing does."""
    schedule = only(duty(ops["rahul_emp"], admin_user, template=_feed_type()))
    run(2026, 10, 5, 10, 0)
    real = task_services.reassign_task

    def stale(**kwargs):
        return real(**{**kwargs, "version": kwargs["version"] - 1})

    monkeypatch.setattr(task_services, "reassign_task", stale)
    with at(2026, 10, 5, 10, 30):
        response = _assign(client_for(hr), schedule, ops["amit_emp"])
    assert response.status_code == 409
    assert task_of(schedule).assigned_to == ops["rahul_emp"]
    assert list(ResponsibilityOwner.objects.filter(responsibility_id=schedule.responsibility_id)
                .values_list("employee", flat=True)) == [ops["rahul_emp"].pk]
    assert not Notification.objects.filter(kind="TASK_REASSIGNED").exists()


def test_the_generator_resolves_the_owner_under_the_responsibility_lock(admin_user, ops):
    """The per-minute run and an owner change take the same row lock, so generation always sees
    a committed owner (checked here by the lock being taken inside _generate)."""
    schedule = only(duty(ops["rahul_emp"], admin_user, template=_feed_type(),
                         run_time=time(10, 0)))
    with at(2026, 10, 5, 10, 0):
        with CaptureQueriesContext(connection) as captured:
            generator.generate_due_occurrences()
    locks = [q["sql"] for q in captured.captured_queries
             if "FOR UPDATE" in q["sql"] and "recurring_responsibility" in q["sql"]]
    assert locks, "owner resolution must lock the responsibility row"
    assert task_of(schedule).assigned_to == ops["rahul_emp"]


# --- further edge cases (review) ------------------------------------------------------------


def test_a_skip_for_an_inactive_owner_is_reported_not_called_existing(
    hr, admin_user, client_for, ops
):
    """The existing recovery rule only recovers 'nobody owned it' skips (unchanged here); any
    other state of today's occurrence is reported as it is, with its detail."""
    schedule = only(duty(ops["rahul_emp"], admin_user, template=_feed_type()))
    type(ops["rahul_emp"]).objects.filter(pk=ops["rahul_emp"].pk).update(is_active=False)
    run(2026, 10, 5, 10, 0)  # Rahul is inactive: SKIPPED with that reason
    with at(2026, 10, 5, 11, 0):
        body = _assign(client_for(hr), schedule, ops["amit_emp"]).json()
    (outcome,) = body["today_generation"]
    assert outcome["result"] == "skipped" and "inactive" in outcome["detail"]
    assert not Task.objects.filter(schedule=schedule).exists()


def test_a_blocked_task_needing_acknowledgment_moves_on_hold_with_its_deadline(
    hr, admin_user, client_for, ops
):
    broker = TaskTemplate.objects.get(code="BROKER_MAPPING")  # 24 h, acknowledgment required
    schedule = only(duty(ops["rahul_emp"], admin_user, template=broker))
    run(2026, 10, 5, 10, 0)
    task = task_of(schedule)
    with at(2026, 10, 5, 10, 5):
        task_services.acknowledge_task(actor=ops["rahul"], task=task, version=task.version)
        task.refresh_from_db()
        task_services.start_task(actor=ops["rahul"], task=task, version=task.version)
        task.refresh_from_db()
        task_services.block_task(actor=ops["rahul"], task=task, version=task.version,
                                 reason="Waiting for the RM codes")
    before = clock(task)
    with at(2026, 10, 5, 10, 30):
        assert _assign(client_for(hr), schedule, ops["amit_emp"]).status_code == 201
    task.refresh_from_db()
    assert (task.assigned_to, task.status, task.blocked_reason) == (
        ops["amit_emp"], "BLOCKED", "Waiting for the RM codes",
    )
    after = clock(task)
    assert (after.pk, after.start_at, after.due_at) == (before.pk, before.start_at, before.due_at)
    # Existing reassignment rule: the new assignee acknowledges (a new ACK clock from now).
    assert task.acknowledged_at is None
    ack = clock(task, "ACK")
    assert (ack.trigger, ack.start_at) == ("ASSIGNMENT", ist(2026, 10, 5, 10, 30))


def test_a_task_already_handed_to_someone_else_is_not_moved(hr, admin_user, client_for, ops):
    schedule = only(duty(ops["rahul_emp"], admin_user, template=_feed_type()))
    run(2026, 10, 5, 10, 0)
    task = task_of(schedule)
    with at(2026, 10, 5, 10, 10):
        task_services.reassign_task(actor=ops["manager"], task=task, version=task.version,
                                    assigned_to=ops["manager_emp"], note="Covering today")
    with at(2026, 10, 5, 10, 30):
        body = _assign(client_for(hr), schedule, ops["amit_emp"]).json()
    assert body["transferred_tasks"] == []
    assert task_of(schedule).assigned_to == ops["manager_emp"]


def test_a_previous_owner_without_a_login_gets_no_notice(hr, admin_user, client_for, ops):
    schedule = only(duty(ops["rahul_emp"], admin_user, template=_feed_type()))
    run(2026, 10, 5, 10, 0)
    type(ops["rahul_emp"]).objects.filter(pk=ops["rahul_emp"].pk).update(user=None)
    with at(2026, 10, 5, 10, 30):
        body = _assign(client_for(hr), schedule, ops["amit_emp"]).json()
    assert len(body["transferred_tasks"]) == 1
    assert task_of(schedule).assigned_to == ops["amit_emp"]
    assert not Notification.objects.filter(kind="TASK_REASSIGNED").exists()


def test_correcting_a_planned_owner_to_today_moves_the_task_from_todays_owner(
    hr, admin_user, client_for, ops
):
    schedule = only(duty(ops["rahul_emp"], admin_user, template=_feed_type()))
    with at(2026, 10, 5, 9, 0):
        assert _assign(client_for(hr), schedule, ops["amit_emp"], day="2026-10-09").status_code \
            == 201
    run(2026, 10, 5, 10, 0)
    task = task_of(schedule)
    assert task.assigned_to == ops["rahul_emp"]
    with at(2026, 10, 5, 10, 30):
        body = _assign(client_for(hr), schedule, ops["manager_emp"]).json()
    assert [t["id"] for t in body["transferred_tasks"]] == [task.pk]
    assert task_of(schedule).assigned_to == ops["manager_emp"]
    planned = ResponsibilityOwner.objects.get(employee=ops["amit_emp"])
    assert planned.superseded_at is not None  # kept for history, never resolved


def test_a_planned_owner_also_blocks_a_department_move(hr, admin_user, client_for, ops):
    schedule = only(duty(None, admin_user, template=_feed_type()))
    rm = Department.objects.get(code="RM")
    with at(2026, 10, 5, 9, 0):
        client = client_for(hr)
        assert _assign(client, schedule, ops["rahul_emp"], day="2026-10-09").status_code == 201
        r = Responsibility.objects.get(pk=schedule.responsibility_id)
        moved = client.patch(f"{RESP}{r.pk}/", {"version": r.version, "department": rm.pk},
                             format="json")
    assert moved.status_code == 400 and "department" in moved.json()["fields"]


def test_admin_ends_an_ownership(admin_user, client_for, ops):
    schedule = only(duty(ops["rahul_emp"], admin_user, template=_feed_type()))
    with at(2026, 10, 5, 9, 0):
        ended = client_for(admin_user).post(
            f"{RESP}{schedule.responsibility_id}/end-ownership/", {"last_day": "2026-10-20"}
        )
    assert ended.status_code == 200 and ended.json()["effective_to"] == "2026-10-20"


def test_a_late_setup_with_an_owner_reports_todays_generation(admin_user, client_for, ops):
    with at(2026, 10, 5, 11, 0):
        body = _setup_body({"employee": ops["rahul_emp"].pk, "effective_from": "2026-10-05"})
        body["template"] = _feed_type().pk
        response = client_for(admin_user).post(SETUP, body, format="json")
    assert response.status_code == 201
    assert [g["result"] for g in response.json()["today_generation"]] == ["generated"]
    task = Task.objects.get(responsibility__code="BIRTHDAY_WISH")
    assert task.assigned_to == ops["rahul_emp"]
    assert clock(task).start_at == ist(2026, 10, 5, 10, 0)  # S1: the scheduled time
