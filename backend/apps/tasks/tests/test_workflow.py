"""Workflow transitions, the acknowledgment gate, block/unblock and cancellation."""

import pytest

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.tasks.models import Task, TaskStatus

TASKS = "/api/v1/tasks/"
pytestmark = pytest.mark.django_db


def _act(client, task, name, **body):
    task.refresh_from_db()
    return client.post(f"{TASKS}{task.pk}/{name}/", {"version": task.version, **body})


@pytest.fixture
def rahul_task(ops, new_task):
    """Assigned by the manager to Rahul."""
    return new_task(ops["manager"], ops["rahul_emp"])


# --- start / complete -------------------------------------------------------------------------


def test_assignee_starts_and_completes(client_for, ops, rahul_task):
    client = client_for(ops["rahul"])
    started = _act(client, rahul_task, "start")
    assert started.status_code == 200
    assert started.json()["status"] == "IN_PROGRESS" and started.json()["started_at"]
    done = _act(client, rahul_task, "complete")
    body = done.json()
    assert body["status"] == "COMPLETED" and body["completed_at"]
    assert body["completed_by"]["id"] == ops["rahul"].pk
    assert body["verification_status"] == "NOT_REQUIRED"
    assert body["completion_source"] == "DIRECT"
    actions = list(
        AuditLog.objects.filter(entity_type="task").order_by("id").values_list("action", flat=True)
    )
    assert actions[-2:] == ["task.started", "task.completed"]


def test_only_the_assignee_starts_or_completes(client_for, ops, rahul_task, admin_client):
    assert _act(client_for(ops["manager"]), rahul_task, "start").status_code == 403
    assert _act(admin_client, rahul_task, "start").status_code == 403
    _act(client_for(ops["rahul"]), rahul_task, "start")
    assert _act(client_for(ops["manager"]), rahul_task, "complete").status_code == 403


def test_completion_with_verification_required_waits_for_verification(client_for, ops, new_task):
    task = new_task(ops["manager"], ops["rahul_emp"], verification_required=True)
    client = client_for(ops["rahul"])
    _act(client, task, "start")
    body = _act(client, task, "complete").json()
    assert body["status"] == "COMPLETED"  # never a sixth status
    assert body["verification_status"] == "PENDING"


# --- acknowledgment ---------------------------------------------------------------------------


def test_acknowledgment_gate(client_for, ops, new_task):
    task = new_task(ops["manager"], ops["rahul_emp"], acknowledgment_required=True)
    client = client_for(ops["rahul"])
    blocked = _act(client, task, "start")
    assert blocked.status_code == 409 and blocked.json()["code"] == "acknowledgment_required"
    assert "start" not in client.get(f"{TASKS}{task.pk}/").json()["allowed_actions"]
    acked = _act(client, task, "acknowledge")
    assert acked.status_code == 200 and acked.json()["acknowledged_at"]
    assert AuditLog.objects.filter(action="task.acknowledged").count() == 1
    assert _act(client, task, "start").status_code == 200
    again = _act(client, task, "acknowledge")
    assert again.status_code == 409 and again.json()["code"] == "invalid_state_transition"


def test_only_the_assignee_acknowledges(client_for, ops, new_task):
    task = new_task(ops["manager"], ops["rahul_emp"], acknowledgment_required=True)
    assert _act(client_for(ops["manager"]), task, "acknowledge").status_code == 403


def test_acknowledge_when_not_required_is_refused(client_for, ops, rahul_task):
    response = _act(client_for(ops["rahul"]), rahul_task, "acknowledge")
    assert response.status_code == 409


def test_completion_needs_acknowledgment_too(client_for, ops, new_task):
    """After a reassignment the new assignee must acknowledge before completing."""
    task = new_task(ops["manager"], ops["rahul_emp"], acknowledgment_required=True)
    _act(client_for(ops["rahul"]), task, "acknowledge")
    _act(client_for(ops["rahul"]), task, "start")
    manager = client_for(ops["manager"])
    _act(manager, task, "reassign", assigned_to=ops["amit_emp"].pk)
    amit = client_for(ops["amit"])
    gate = _act(amit, task, "complete")
    assert gate.status_code == 409 and gate.json()["code"] == "acknowledgment_required"
    _act(amit, task, "acknowledge")
    assert _act(amit, task, "complete").status_code == 200


# --- block / unblock --------------------------------------------------------------------------


def test_block_requires_reason_and_unblock_returns_to_pending_if_never_started(
    client_for, ops, rahul_task
):
    client = client_for(ops["rahul"])
    missing = _act(client, rahul_task, "block", reason="  ")
    assert missing.status_code == 400 and "reason" in missing.json()["fields"]
    blocked = _act(client, rahul_task, "block", reason="Waiting for KYC documents")
    assert blocked.json()["status"] == "BLOCKED"
    assert blocked.json()["blocked_reason"] == "Waiting for KYC documents"
    row = AuditLog.objects.get(action="task.blocked")
    assert row.new_value == {"status": "BLOCKED", "reason": "Waiting for KYC documents"}
    unblocked = _act(client, rahul_task, "unblock")
    assert unblocked.json()["status"] == "PENDING" and unblocked.json()["blocked_reason"] == ""
    unblock_row = AuditLog.objects.get(action="task.unblocked")
    assert unblock_row.old_value["reason"] == "Waiting for KYC documents"


def test_unblock_returns_to_in_progress_if_started(client_for, ops, rahul_task):
    client = client_for(ops["rahul"])
    _act(client, rahul_task, "start")
    _act(client, rahul_task, "block", reason="System down")
    assert _act(client, rahul_task, "unblock").json()["status"] == "IN_PROGRESS"


def test_manager_may_block_and_unblock(client_for, ops, rahul_task):
    manager = client_for(ops["manager"])
    assert _act(manager, rahul_task, "block", reason="Client on hold").status_code == 200
    assert _act(manager, rahul_task, "unblock").status_code == 200


def test_unrelated_colleague_cannot_see_or_block(client_for, ops, rahul_task):
    response = _act(client_for(ops["amit"]), rahul_task, "block", reason="x")
    assert response.status_code == 404


# --- cancel -----------------------------------------------------------------------------------


def test_creator_cancels_own_task_before_acknowledgment(client_for, ops, new_task):
    task = new_task(ops["rahul"], ops["rahul_emp"])
    client = client_for(ops["rahul"])
    missing = _act(client, task, "cancel", reason="")
    assert missing.status_code == 400
    done = _act(client, task, "cancel", reason="Created by mistake")
    assert done.json()["status"] == "CANCELLED"
    assert done.json()["cancelled_reason"] == "Created by mistake"
    assert AuditLog.objects.get(action="task.cancelled").new_value["reason"] == "Created by mistake"


def test_creator_cannot_cancel_after_acknowledgment(client_for, ops, new_task, grant_assign):
    rahul = grant_assign(ops["rahul"])
    task = new_task(rahul, ops["amit_emp"], acknowledgment_required=True)
    _act(client_for(ops["amit"]), task, "acknowledge")
    assert _act(client_for(rahul), task, "cancel", reason="No longer needed").status_code == 403
    manager = client_for(ops["manager"])
    assert _act(manager, task, "cancel", reason="Manager decision").status_code == 200


def test_creator_cannot_cancel_once_started(client_for, ops, new_task):
    task = new_task(ops["rahul"], ops["rahul_emp"])
    client = client_for(ops["rahul"])
    _act(client, task, "start")
    assert _act(client, task, "cancel", reason="x").status_code == 403


def test_manager_of_another_department_cannot_reach_the_task(client_for, staff, rahul_task):
    rm_manager, _ = staff(roles.OPERATIONS_MANAGER, department="RM")
    assert _act(client_for(rm_manager), rahul_task, "cancel", reason="x").status_code == 404


def test_admin_cancels_any_task(admin_client, rahul_task):
    assert _act(admin_client, rahul_task, "cancel", reason="Duplicate").status_code == 200


def test_hr_manages_but_does_not_run_the_workflow(client_for, staff, rahul_task):
    """Changed in Phase 4: HR edits, reassigns and deletes any task, but still cannot cancel,
    block or work the task (cancel / block / verify are not part of HR's authority)."""
    hr, _ = staff(roles.HR, department="HR")
    client = client_for(hr)
    detail = client.get(f"{TASKS}{rahul_task.pk}/")
    assert detail.status_code == 200
    allowed = set(detail.json()["allowed_actions"])
    assert allowed == {"edit", "change_department", "reassign", "delete"}
    for name, body in (("cancel", {"reason": "x"}), ("block", {"reason": "x"}), ("start", {})):
        assert _act(client, rahul_task, name, **body).status_code == 403


# --- invalid transitions and concurrency ------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "action", "body"),
    [
        (TaskStatus.PENDING, "complete", {}),
        (TaskStatus.PENDING, "unblock", {}),
        (TaskStatus.IN_PROGRESS, "start", {}),
        (TaskStatus.BLOCKED, "start", {}),
        (TaskStatus.BLOCKED, "complete", {}),
        (TaskStatus.BLOCKED, "block", {"reason": "x"}),
        (TaskStatus.COMPLETED, "start", {}),
        (TaskStatus.COMPLETED, "block", {"reason": "x"}),
        (TaskStatus.COMPLETED, "cancel", {"reason": "x"}),
        (TaskStatus.CANCELLED, "start", {}),
        (TaskStatus.CANCELLED, "unblock", {}),
        (TaskStatus.CANCELLED, "cancel", {"reason": "x"}),
    ],
)
def test_invalid_transitions_are_refused(client_for, ops, rahul_task, status, action, body):
    fields = {"status": status}
    if status == TaskStatus.COMPLETED:
        fields.update(completed_at=rahul_task.created_at, started_at=rahul_task.created_at)
    Task.objects.filter(pk=rahul_task.pk).update(**fields)
    response = _act(client_for(ops["rahul"]), rahul_task, action, **body)
    assert response.status_code == 409
    assert response.json()["code"] == "invalid_state_transition"


def test_stale_version_is_a_conflict(client_for, ops, rahul_task):
    response = client_for(ops["rahul"]).post(f"{TASKS}{rahul_task.pk}/start/", {"version": 99})
    assert response.status_code == 409 and response.json()["code"] == "version_conflict"


def test_version_is_required(client_for, ops, rahul_task):
    response = client_for(ops["rahul"]).post(f"{TASKS}{rahul_task.pk}/start/", {})
    assert response.status_code == 400 and "version" in response.json()["fields"]


def test_every_action_bumps_the_version(client_for, ops, rahul_task):
    client = client_for(ops["rahul"])
    assert _act(client, rahul_task, "start").json()["version"] == 2
    assert _act(client, rahul_task, "complete").json()["version"] == 3


def test_delete_needs_a_version_and_put_is_not_offered(admin_client, rahul_task):
    """Updated for Phase 4: DELETE is now the approved physical delete and requires ?version=N
    (a request without it is a 400 and deletes nothing); PUT is still not offered. Full delete
    behaviour is covered in test_phase4_handoff.py."""
    response = admin_client.delete(f"{TASKS}{rahul_task.pk}/")
    assert response.status_code == 400 and "version" in response.json()["fields"]
    assert Task.objects.filter(pk=rahul_task.pk).exists()
    assert admin_client.put(f"{TASKS}{rahul_task.pk}/", {}).status_code == 405


def test_status_cannot_be_patched_directly(admin_client, rahul_task):
    response = admin_client.patch(
        f"{TASKS}{rahul_task.pk}/", {"version": rahul_task.version, "status": "COMPLETED"}
    )
    assert response.status_code == 200  # unknown field ignored by the serializer
    rahul_task.refresh_from_db()
    assert rahul_task.status == TaskStatus.PENDING
