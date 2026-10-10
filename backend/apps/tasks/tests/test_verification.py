"""Verification is a separate process: approve, reject with rework on the SAME task, history."""

import pytest

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.tasks.models import Task, TaskVerification

TASKS = "/api/v1/tasks/"
pytestmark = pytest.mark.django_db


def _act(client, task, name, **body):
    task.refresh_from_db()
    return client.post(f"{TASKS}{task.pk}/{name}/", {"version": task.version, **body})


@pytest.fixture
def completed(client_for, ops, new_task):
    task = new_task(ops["manager"], ops["rahul_emp"], verification_required=True)
    rahul = client_for(ops["rahul"])
    _act(rahul, task, "start")
    _act(rahul, task, "complete", work_response="Work done.")
    return task


def test_manager_verifies(client_for, ops, completed):
    response = _act(client_for(ops["manager"]), completed, "verify", remarks="Checked with RTA")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "COMPLETED" and body["verification_status"] == "VERIFIED"
    assert body["verifications"][0]["decision"] == "VERIFIED"
    assert body["verifications"][0]["remarks"] == "Checked with RTA"
    assert AuditLog.objects.get(action="task.verified").new_value["cycle_no"] == 1


def test_rejection_needs_reason_and_remarks(client_for, ops, completed):
    manager = client_for(ops["manager"])
    no_reason = _act(manager, completed, "reject-verification", reason="", remarks="Fix it")
    assert no_reason.status_code == 400 and "reason" in no_reason.json()["fields"]
    no_remarks = _act(manager, completed, "reject-verification", reason="Wrong folio", remarks=" ")
    assert no_remarks.status_code == 400 and "remarks" in no_remarks.json()["fields"]
    assert not TaskVerification.objects.exists()


def test_rejection_sends_the_same_task_back_for_rework(client_for, ops, completed):
    manager, rahul = client_for(ops["manager"]), client_for(ops["rahul"])
    rejected = _act(
        manager, completed, "reject-verification", reason="Wrong folio", remarks="Use folio 1234"
    ).json()
    assert rejected["id"] == completed.pk and Task.objects.count() == 1  # no new task
    assert rejected["status"] == "IN_PROGRESS"
    assert rejected["verification_status"] == "REJECTED"
    assert rejected["rework_count"] == 1
    assert rejected["completed_at"] is None
    row = AuditLog.objects.get(action="task.verification_rejected")
    assert row.new_value["reason"] == "Wrong folio" and row.new_value["rework_count"] == 1

    again = _act(rahul, completed, "complete", work_response="Work done.").json()
    assert again["status"] == "COMPLETED" and again["verification_status"] == "PENDING"
    first = TaskVerification.objects.get(task=completed, cycle_no=1)
    assert first.rework_seconds is not None

    final = _act(manager, completed, "verify").json()
    assert [v["cycle_no"] for v in final["verifications"]] == [1, 2]
    assert [v["decision"] for v in final["verifications"]] == ["REJECTED", "VERIFIED"]
    assert final["rework_count"] == 1


def test_only_task_managers_verify(client_for, ops, staff, completed, admin_client):
    hr, _ = staff(roles.HR, department="HR")
    rm_manager, _ = staff(roles.OPERATIONS_MANAGER, department="RM")
    assert _act(client_for(ops["rahul"]), completed, "verify").status_code == 403
    assert _act(client_for(hr), completed, "verify").status_code == 403
    assert _act(client_for(rm_manager), completed, "verify").status_code == 404
    assert _act(admin_client, completed, "verify").status_code == 200


def test_verify_only_when_pending(client_for, ops, new_task):
    task = new_task(ops["manager"], ops["rahul_emp"])  # verification not required
    rahul = client_for(ops["rahul"])
    _act(rahul, task, "start")
    _act(rahul, task, "complete", work_response="Work done.")
    response = _act(client_for(ops["manager"]), task, "verify")
    assert response.status_code == 409 and response.json()["code"] == "invalid_state_transition"


def test_verified_task_cannot_be_verified_or_rejected_again(client_for, ops, completed):
    manager = client_for(ops["manager"])
    _act(manager, completed, "verify")
    assert _act(manager, completed, "verify").status_code == 409
    rejected = _act(manager, completed, "reject-verification", reason="a", remarks="b")
    assert rejected.status_code == 409


def test_verification_history_is_kept_in_the_detail_view(client_for, ops, completed):
    manager = client_for(ops["manager"])
    _act(manager, completed, "reject-verification", reason="r1", remarks="m1")
    rahul = client_for(ops["rahul"])
    _act(rahul, completed, "complete", work_response="Work done.")
    _act(manager, completed, "reject-verification", reason="r2", remarks="m2")
    detail = rahul.get(f"{TASKS}{completed.pk}/").json()
    assert [v["rejection_reason"] for v in detail["verifications"]] == ["r1", "r2"]
    assert detail["rework_count"] == 2
