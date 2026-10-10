"""Editing task fields, received_at corrections (R6) and reassignment with history."""

from datetime import timedelta

import pytest
from django.contrib.auth.models import Permission

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.org.tests.factories import EmployeeFactory
from apps.tasks import services

TASKS = "/api/v1/tasks/"
# Completing a task requires a work response (extra keys are ignored by the other actions).
WORK = {"complete": {"work_response": "Work done."}}
pytestmark = pytest.mark.django_db


def _patch(client, task, **changes):
    task.refresh_from_db()
    return client.patch(f"{TASKS}{task.pk}/", {"version": task.version, **changes})


def _reassign(client, task, employee, **extra):
    task.refresh_from_db()
    return client.post(
        f"{TASKS}{task.pk}/reassign/",
        {"version": task.version, "assigned_to": employee.pk, **extra},
    )


@pytest.fixture
def own_task(ops, new_task):
    return new_task(ops["rahul"], ops["rahul_emp"], title="Draft", priority="LOW")


def test_creator_edits_inside_the_creator_window(client_for, ops, own_task):
    response = _patch(
        client_for(ops["rahul"]), own_task, title="Final", priority="URGENT", description=" Notes "
    )
    assert response.status_code == 200 and response.json()["version"] == 2
    updated = AuditLog.objects.get(action="task.updated")
    assert updated.old_value == {"title": "Draft", "description": ""}
    assert updated.new_value == {"title": "Final", "description": "Notes"}
    priority = AuditLog.objects.get(action="task.priority_changed")
    assert priority.old_value == {"priority": "LOW"}
    assert priority.new_value == {"priority": "URGENT"}


def test_creator_cannot_edit_after_starting(client_for, ops, own_task):
    client = client_for(ops["rahul"])
    own_task.refresh_from_db()
    client.post(f"{TASKS}{own_task.pk}/start/", {"version": own_task.version})
    assert _patch(client, own_task, title="Late change").status_code == 403
    assert _patch(client_for(ops["manager"]), own_task, title="Manager fix").status_code == 200


def test_no_change_patch_is_a_no_op(client_for, ops, own_task):
    response = _patch(client_for(ops["rahul"]), own_task, title="Draft")
    assert response.json()["version"] == 1
    assert not AuditLog.objects.filter(action="task.updated").exists()


def test_edit_validation(client_for, ops, own_task):
    client = client_for(ops["rahul"])
    assert "title" in _patch(client, own_task, title=" ").json()["fields"]
    assert "task_type" in _patch(client, own_task, task_type="RECURRING").json()["fields"]
    stale = client.patch(f"{TASKS}{own_task.pk}/", {"version": 42, "title": "x"})
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"


def test_cancelled_task_cannot_be_edited(client_for, ops, own_task):
    client = client_for(ops["rahul"])
    own_task.refresh_from_db()
    cancel = {"version": own_task.version, "reason": "Duplicate"}
    client.post(f"{TASKS}{own_task.pk}/cancel/", cancel)
    assert _patch(client_for(ops["manager"]), own_task, title="x").status_code == 403


def test_received_at_change_needs_a_reason_and_is_audited(client_for, ops, own_task, now):
    client = client_for(ops["rahul"])
    received = (now - timedelta(hours=2)).replace(microsecond=0).isoformat()
    missing = _patch(client, own_task, received_at=received, received_at_source="EMAIL")
    assert missing.status_code == 400 and "received_at_reason" in missing.json()["fields"]
    done = _patch(
        client,
        own_task,
        received_at=received,
        received_at_source="EMAIL",
        received_at_reason="Mail arrived before the task was logged",
    )
    assert done.status_code == 200 and done.json()["received_at_source"] == "EMAIL"
    row = AuditLog.objects.get(action="task.received_at_changed")
    assert row.old_value == {"received_at": None, "source": None}
    assert row.new_value["source"] == "EMAIL"
    assert row.context["reason"] == "Mail arrived before the task was logged"


def test_received_at_after_acknowledgment_is_manager_only(
    client_for, ops, new_task, now, grant_assign
):
    rahul = grant_assign(ops["rahul"])
    task = new_task(rahul, ops["amit_emp"], acknowledgment_required=True)
    task.refresh_from_db()
    client_for(ops["amit"]).post(f"{TASKS}{task.pk}/acknowledge/", {"version": task.version})
    change = {
        "received_at": (now - timedelta(hours=1)).isoformat(),
        "received_at_source": "MANUAL",
        "received_at_reason": "Correction",
    }
    assert _patch(client_for(rahul), task, **change).status_code == 403
    assert _patch(client_for(ops["manager"]), task, **change).status_code == 200


def test_received_at_can_be_corrected_after_completion_by_manager(client_for, ops, new_task, now):
    task = new_task(ops["manager"], ops["rahul_emp"])
    rahul = client_for(ops["rahul"])
    for action in ("start", "complete"):
        task.refresh_from_db()
        rahul.post(f"{TASKS}{task.pk}/{action}/", {"version": task.version, **WORK.get(action, {})})
    response = _patch(
        client_for(ops["manager"]),
        task,
        received_at=(now - timedelta(days=1)).isoformat(),
        received_at_source="EMAIL",
        received_at_reason="Found the original email",
    )
    assert response.status_code == 200


def test_received_pair_rule_applies_to_edits(client_for, ops, own_task):
    response = _patch(
        client_for(ops["rahul"]),
        own_task,
        received_at_source="EMAIL",
        received_at_reason="x",
    )
    assert response.status_code == 400 and "received_at_source" in response.json()["fields"]


# --- reassignment -----------------------------------------------------------------------------


def test_manager_reassigns_within_department_with_history(client_for, ops, new_task):
    task = new_task(ops["manager"], ops["rahul_emp"], acknowledgment_required=True)
    task.refresh_from_db()
    client_for(ops["rahul"]).post(f"{TASKS}{task.pk}/acknowledge/", {"version": task.version})
    response = _reassign(client_for(ops["manager"]), task, ops["amit_emp"], note="Rahul on leave")
    body = response.json()
    assert response.status_code == 200
    assert body["assigned_to"]["id"] == ops["amit_emp"].pk
    assert body["acknowledged_at"] is None  # the new assignee must acknowledge
    hops = [
        (a["from_employee"] and a["from_employee"]["id"], a["to_employee"]["id"])
        for a in body["assignments"]
    ]
    assert hops == [
        (None, ops["rahul_emp"].pk),
        (ops["rahul_emp"].pk, ops["amit_emp"].pk),
    ]
    assert body["assignments"][1]["note"] == "Rahul on leave"
    reassigned = AuditLog.objects.get(action="task.reassigned")
    assert reassigned.new_value["assigned_to_id"] == ops["amit_emp"].pk


def test_manager_reassigns_across_departments_and_department_stays(
    client_for, ops, staff, new_task
):
    """Changed in Phase 4: department no longer blocks reassignment, and reassignment never
    changes the task department."""
    _, rm_emp = staff(roles.EMPLOYEE, department="RM")
    task = new_task(ops["manager"], ops["rahul_emp"])
    body = _reassign(client_for(ops["manager"]), task, rm_emp).json()
    assert body["assigned_to"]["id"] == rm_emp.pk
    assert body["department"]["code"] == "OPS"


def test_admin_reassigns_across_departments_and_department_stays(
    admin_client, ops, staff, new_task
):
    """Changed in Phase 4: was "department follows the assignee"; now it never does."""
    _, rm_emp = staff(roles.EMPLOYEE, department="RM")
    task = new_task(ops["manager"], ops["rahul_emp"])
    body = _reassign(admin_client, task, rm_emp, note="Covering").json()
    assert body["department"]["code"] == "OPS"
    assert body["category"]["code"] == "OPERATIONS"
    row = AuditLog.objects.get(action="task.reassigned")
    assert row.old_value == {"assigned_to_id": ops["rahul_emp"].pk, "acknowledged_at": None}
    assert row.context["from_employee_id"] == ops["rahul_emp"].pk
    assert row.context["to_employee_id"] == rm_emp.pk and row.context["note"] == "Covering"


def test_creator_without_assign_cannot_hand_off_to_a_colleague(
    client_for, ops, make_user, new_task
):
    """Phase 3 rule kept for creators WITHOUT tasks.assign. (Changed in Phase 4: the Employee
    role now holds tasks.assign, so this uses a creator with create_task only.)"""
    creator = make_user()
    creator.user_permissions.add(
        Permission.objects.get(codename="create_task", content_type__app_label="tasks")
    )
    creator = type(creator).objects.get(pk=creator.pk)
    own_employee = EmployeeFactory(user=creator, email=creator.email)
    task = new_task(creator, own_employee)
    response = _reassign(client_for(creator), task, ops["amit_emp"])
    assert response.status_code == 403 and response.json()["code"] == "assignment_not_allowed"


def test_creator_with_assign_reassigns_inside_window_only(client_for, ops, new_task, grant_assign):
    rahul = grant_assign(ops["rahul"])
    task = new_task(rahul, ops["rahul_emp"])
    assert _reassign(client_for(rahul), task, ops["amit_emp"]).status_code == 200
    task.refresh_from_db()
    client_for(ops["amit"]).post(f"{TASKS}{task.pk}/start/", {"version": task.version})
    assert _reassign(client_for(rahul), task, ops["rahul_emp"]).status_code == 403


def test_reassign_to_same_person_or_closed_task(client_for, ops, new_task):
    task = new_task(ops["manager"], ops["rahul_emp"])
    manager = client_for(ops["manager"])
    same = _reassign(manager, task, ops["rahul_emp"])
    assert same.status_code == 400 and "assigned_to" in same.json()["fields"]
    task.refresh_from_db()
    manager.post(f"{TASKS}{task.pk}/cancel/", {"version": task.version, "reason": "Duplicate"})
    closed = _reassign(manager, task, ops["amit_emp"])
    assert closed.status_code == 409 and closed.json()["code"] == "invalid_state_transition"


def test_update_service_rejects_unknown_fields(ops, own_task):
    with pytest.raises(TypeError):
        services.update_task(actor=ops["rahul"], task=own_task, version=1, status="COMPLETED")
