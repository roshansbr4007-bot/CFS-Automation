"""Phase 4: assignment across users and departments, task-level department and category,
reassignment / hand-off, HR and Admin task authority, and physical delete."""

from datetime import timedelta

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.org.models import Department
from apps.sla.models import TaskSla
from apps.tasks.models import (
    Task,
    TaskAssignment,
    TaskAttachment,
    TaskCategory,
    TaskComment,
    TaskTemplate,
    TaskVerification,
)

TASKS = "/api/v1/tasks/"
CATEGORIES = "/api/v1/task-categories/"
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"
pytestmark = pytest.mark.django_db


def _dept(code):
    return Department.objects.get(code=code)


def _cat(code):
    return TaskCategory.objects.get(code=code)


def _payload(assignee, department="OPS", category="OPERATIONS", **extra):
    return {
        "title": "Hand-off check",
        "assigned_to": assignee.pk,
        "department": _dept(department).pk,
        "category": _cat(category).pk,
        **extra,
    }


def _patch(client, task, **changes):
    task.refresh_from_db()
    return client.patch(f"{TASKS}{task.pk}/", {"version": task.version, **changes})


def _reassign(client, task, employee, **extra):
    task.refresh_from_db()
    body = {"version": task.version, "assigned_to": employee.pk, **extra}
    return client.post(f"{TASKS}{task.pk}/reassign/", body)


def _delete(client, task, version=None):
    task.refresh_from_db()
    return client.delete(f"{TASKS}{task.pk}/?version={version or task.version}")


@pytest.fixture
def people(ops, staff):
    hr, hr_emp = staff(roles.HR, department="HR")
    hr2, hr2_emp = staff(roles.HR, department="HR")
    return {**ops, "hr": hr, "hr_emp": hr_emp, "hr2": hr2, "hr2_emp": hr2_emp}


# --- 1. assignment across users and departments ----------------------------------------------


@pytest.mark.parametrize(
    ("creator", "target"),
    [
        ("rahul", "amit_emp"),  # Employee -> Employee
        ("rahul", "hr_emp"),  # Employee -> HR (another department)
        ("hr", "rahul_emp"),  # HR -> Employee (another department)
        ("hr", "hr2_emp"),  # HR -> HR
    ],
)
def test_assignment_directions(client_for, people, creator, target):
    response = client_for(people[creator]).post(TASKS, _payload(people[target]))
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["assigned_to"]["id"] == people[target].pk
    assert body["created_by"]["id"] == people[creator].pk
    assert body["department"]["code"] == "OPS"  # as chosen, whatever the assignee's department


@pytest.mark.parametrize("target", ["rahul_emp", "hr_emp"])
def test_admin_assigns_to_employees_and_hr(admin_client, people, target):
    assert admin_client.post(TASKS, _payload(people[target])).status_code == 201


def test_raised_by_always_comes_from_the_signed_in_user(client_for, people):
    body = _payload(people["amit_emp"], created_by=people["hr"].pk, raised_by=people["hr"].pk)
    response = client_for(people["rahul"]).post(TASKS, body)
    assert response.json()["created_by"]["id"] == people["rahul"].pk


def test_unauthorized_assignment_is_rejected(api_client, client_for, make_user, people):
    assert api_client.post(TASKS, _payload(people["amit_emp"])).status_code == 401
    assert client_for(make_user()).post(TASKS, _payload(people["amit_emp"])).status_code == 403
    people["amit_emp"].is_active = False
    people["amit_emp"].save()
    refused = client_for(people["rahul"]).post(TASKS, _payload(people["amit_emp"]))
    assert refused.status_code == 403 and refused.json()["code"] == "assignment_not_allowed"


# --- 2/3. task department and category --------------------------------------------------------


def test_creator_sets_department_and_category_independently_of_the_assignee(client_for, people):
    body = client_for(people["rahul"]).post(
        TASKS, _payload(people["amit_emp"], department="HR", category="COMPLIANCE")
    ).json()
    assert body["department"]["code"] == "HR"  # the assignee is in OPS
    assert body["category"]["code"] == "COMPLIANCE"
    created = AuditLog.objects.get(action="task.created")
    assert created.new_value["department_id"] == _dept("HR").pk
    assert created.new_value["category_id"] == _cat("COMPLIANCE").pk


def test_reassignment_keeps_department_and_category(client_for, people, new_task):
    task = new_task(
        people["hr"], people["rahul_emp"], department=_dept("RM"), category=_cat("SALES")
    )
    body = _reassign(client_for(people["hr"]), task, people["hr2_emp"], note="Leave cover").json()
    assert body["assigned_to"]["id"] == people["hr2_emp"].pk
    assert body["department"]["code"] == "RM" and body["category"]["code"] == "SALES"


def test_hr_changes_department_and_category_with_audit(client_for, people, new_task):
    task = new_task(people["rahul"], people["rahul_emp"])
    response = _patch(
        client_for(people["hr"]), task, department=_dept("HR").pk, category=_cat("FINANCE").pk
    )
    assert response.status_code == 200
    assert response.json()["department"]["code"] == "HR"
    dept_row = AuditLog.objects.get(action="task.department_changed")
    assert dept_row.old_value == {"department_id": _dept("OPS").pk}
    assert dept_row.new_value == {"department_id": _dept("HR").pk}
    cat_row = AuditLog.objects.get(action="task.category_changed")
    assert cat_row.new_value == {"category_id": _cat("FINANCE").pk}


def test_ops_manager_changes_department_only_inside_scope(client_for, people, new_task):
    manager = client_for(people["manager"])
    in_scope = new_task(people["rahul"], people["rahul_emp"])  # task department OPS
    assert _patch(manager, in_scope, department=_dept("RM").pk).status_code == 200
    own_hr_task = new_task(people["manager"], people["amit_emp"], department=_dept("HR"))
    refused = _patch(manager, own_hr_task, department=_dept("OPS").pk)
    assert refused.status_code == 403  # creator of it, but HR tasks are outside the scope


def test_ops_manager_moving_a_task_out_of_scope_succeeds_then_loses_access(
    client_for, people, new_task
):
    """Regression: the PATCH used to raise Task.DoesNotExist (500) because the response was
    re-read through the manager's scope, which the authorized change had just left."""
    task = new_task(people["rahul"], people["rahul_emp"])  # task department OPS
    manager = client_for(people["manager"])
    response = _patch(manager, task, department=_dept("RM").pk)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["department"]["code"] == "RM"
    assert body["allowed_actions"] == []  # nothing left for the manager on an RM task
    task.refresh_from_db()
    assert task.department.code == "RM"
    moved = AuditLog.objects.filter(action="task.department_changed", entity_id=str(task.pk))
    assert moved.exists()
    # Normal scoped visibility applies from the next request on.
    assert manager.get(f"{TASKS}{task.pk}/").status_code == 404
    assert task.pk not in [t["id"] for t in manager.get(TASKS).json()["results"]]
    assert _patch(manager, task, department=_dept("OPS").pk).status_code == 404
    assert _patch(manager, task, title="Still mine?").status_code == 404
    assert _reassign(manager, task, people["amit_emp"]).status_code == 404
    task.refresh_from_db()
    cancel = manager.post(f"{TASKS}{task.pk}/cancel/", {"version": task.version, "reason": "x"})
    assert cancel.status_code == 404
    task.refresh_from_db()
    assert task.department.code == "RM" and task.status == "PENDING"


def test_employee_cannot_change_department_after_creation(client_for, people, new_task):
    task = new_task(people["rahul"], people["rahul_emp"])
    rahul = client_for(people["rahul"])
    assert _patch(rahul, task, department=_dept("HR").pk).status_code == 403
    # Category is an ordinary field: still editable by the creator inside the creator window.
    assert _patch(rahul, task, category=_cat("SALES").pk).status_code == 200


def test_inactive_category_cannot_be_chosen_on_edit(client_for, people, new_task):
    task = new_task(people["rahul"], people["rahul_emp"])
    TaskCategory.objects.filter(code="SALES").update(is_active=False)
    response = _patch(client_for(people["hr"]), task, category=_cat("SALES").pk)
    assert response.status_code == 400 and "category" in response.json()["fields"]


# --- 5. reassignment / hand-off ---------------------------------------------------------------


def test_hand_off_history_audit_and_sla(client_for, people, new_task):
    broker = TaskTemplate.objects.get(code="BROKER_MAPPING")
    task = new_task(people["manager"], people["rahul_emp"], template=broker)
    resolution_start = TaskSla.objects.get(task=task, kind="RESOLUTION").start_at
    response = _reassign(client_for(people["hr"]), task, people["hr_emp"], note="Moved to HR")
    assert response.status_code == 200
    hops = [(a.from_employee_id, a.to_employee_id, a.assigned_by_id, a.note)
            for a in TaskAssignment.objects.filter(task=task).order_by("id")]
    assert hops == [
        (None, people["rahul_emp"].pk, people["manager"].pk, ""),
        (people["rahul_emp"].pk, people["hr_emp"].pk, people["hr"].pk, "Moved to HR"),
    ]
    row = AuditLog.objects.get(action="task.reassigned")
    assert row.actor_user == people["hr"]
    assert row.context["from_employee_id"] == people["rahul_emp"].pk
    assert row.context["to_employee_id"] == people["hr_emp"].pk
    # SLA unchanged by Phase 4: ACK restarts for the new assignee, resolution does not reset.
    acks = list(TaskSla.objects.filter(task=task, kind="ACK").order_by("id"))
    assert [(c.is_current, c.stop_reason) for c in acks] == [(False, "REASSIGNED"), (True, "")]
    assert TaskSla.objects.get(task=task, kind="RESOLUTION").start_at == resolution_start


# --- 6/7/8. HR, Admin and Employee authority --------------------------------------------------


def test_hr_and_admin_edit_reassign_any_task(client_for, admin_client, people, new_task):
    for client in (client_for(people["hr"]), admin_client):
        task = new_task(people["manager"], people["rahul_emp"])
        assert _patch(client, task, title="Edited by authority").status_code == 200
        assert _reassign(client, task, people["hr2_emp"]).status_code == 200


def test_hr_does_not_get_cancel_block_or_verify(client_for, people, new_task):
    task = new_task(people["manager"], people["rahul_emp"])
    hr = client_for(people["hr"])
    task.refresh_from_db()
    body = {"version": task.version, "reason": "x"}
    assert hr.post(f"{TASKS}{task.pk}/cancel/", body).status_code == 403
    assert hr.post(f"{TASKS}{task.pk}/block/", body).status_code == 403
    assert "verify" not in hr.get(f"{TASKS}{task.pk}/").json()["allowed_actions"]


def test_employee_restrictions_and_allowed_actions(client_for, people, new_task):
    task = new_task(people["manager"], people["rahul_emp"])
    rahul = client_for(people["rahul"])
    assert _patch(rahul, task, title="Mine now").status_code == 403  # not the creator
    assert _delete(rahul, task).status_code == 403
    task.refresh_from_db()
    assert rahul.post(f"{TASKS}{task.pk}/start/", {"version": task.version}).status_code == 200
    task.refresh_from_db()
    assert rahul.post(f"{TASKS}{task.pk}/complete/", {"version": task.version}).status_code == 200


# --- 9. physical delete -----------------------------------------------------------------------


def _fully_loaded_task(client_for, people, new_task):
    """A Broker Mapping task with assignments, SLA clocks, a comment, an attachment,
    a verification row and notifications pointing at it and at one of its clocks."""
    broker = TaskTemplate.objects.get(code="BROKER_MAPPING")
    task = new_task(people["manager"], people["rahul_emp"], template=broker)
    _reassign(client_for(people["manager"]), task, people["amit_emp"])
    amit = client_for(people["amit"])
    amit.post(f"{TASKS}{task.pk}/comments/", {"body": "On it"})
    amit.post(
        f"{TASKS}{task.pk}/attachments/",
        {"file": SimpleUploadedFile("folio.pdf", PDF)},
        format="multipart",
    )
    TaskVerification.objects.create(
        task=task, cycle_no=1, submitted_at=task.created_at, decision="VERIFIED",
        decided_by=people["manager"], decided_at=task.created_at,
    )
    clock = TaskSla.objects.filter(task=task).first()
    Notification.objects.create(
        recipient=people["amit"], task=task, kind="SLA_WARNING", title="Warn", body="b",
        dedup_key="test:task",
    )
    Notification.objects.create(
        recipient=people["amit"], clock=clock, kind="SLA_CRITICAL", title="Crit", body="b",
        dedup_key="test:clock",
    )
    task.refresh_from_db()
    return task


@pytest.mark.parametrize("actor", ["hr", "admin"])
def test_physical_delete_removes_owned_records_and_keeps_audit(
    client_for, admin_user, people, new_task, django_capture_on_commit_callbacks, actor
):
    task = _fully_loaded_task(client_for, people, new_task)
    user = admin_user if actor == "admin" else people[actor]
    file_name = TaskAttachment.objects.get(task=task).file.name
    pk, title, reference = task.pk, task.title, task.reference
    with django_capture_on_commit_callbacks(execute=True):
        response = _delete(client_for(user), task)
    assert response.status_code == 204
    assert not Task.objects.filter(pk=pk).exists()
    for model in (TaskAssignment, TaskComment, TaskAttachment, TaskVerification, TaskSla):
        assert not model.objects.filter(task_id=pk).exists(), model
    kept = Notification.objects.filter(dedup_key__startswith="test:")
    assert kept.count() == 2 and all(n.task_id is None and n.clock_id is None for n in kept)
    assert not default_storage.exists(file_name)  # removed after the commit
    row = AuditLog.objects.get(action="task.deleted")
    assert row.entity_id == str(pk) and row.actor_user == user
    assert row.old_value["title"] == title and row.old_value["reference"] == reference
    assert row.context["deleted_records"] == {
        "assignments": 2, "verifications": 1, "comments": 1, "attachments": 1, "sla_clocks": 3,
    }
    assert row.context["notifications_unlinked"] == 2
    history = AuditLog.objects.filter(entity_type="task", entity_id=str(pk))
    assert history.count() > 1  # the task's earlier audit history is kept too


def test_files_are_only_removed_after_commit(
    client_for, people, new_task, django_capture_on_commit_callbacks
):
    task = _fully_loaded_task(client_for, people, new_task)
    file_name = TaskAttachment.objects.get(task=task).file.name
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        assert _delete(client_for(people["hr"]), task).status_code == 204
    assert default_storage.exists(file_name) and len(callbacks) == 1  # not yet committed


def test_delete_in_any_status_including_verified(client_for, people, new_task):
    task = new_task(people["manager"], people["rahul_emp"], verification_required=True)
    rahul, manager = client_for(people["rahul"]), client_for(people["manager"])
    for action in ("start", "complete"):
        task.refresh_from_db()
        rahul.post(f"{TASKS}{task.pk}/{action}/", {"version": task.version})
    task.refresh_from_db()
    manager.post(f"{TASKS}{task.pk}/verify/", {"version": task.version})
    task.refresh_from_db()
    assert task.status == "COMPLETED" and task.verification_status == "VERIFIED"
    assert _delete(client_for(people["hr"]), task).status_code == 204


def test_stale_or_missing_version_never_deletes(client_for, people, new_task):
    task = new_task(people["manager"], people["rahul_emp"])
    hr = client_for(people["hr"])
    stale = _delete(hr, task, version=task.version + 5)
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
    missing = hr.delete(f"{TASKS}{task.pk}/")
    assert missing.status_code == 400 and "version" in missing.json()["fields"]
    assert Task.objects.filter(pk=task.pk).exists()
    assert not AuditLog.objects.filter(action="task.deleted").exists()


def test_unauthorized_delete_is_rejected(api_client, client_for, people, new_task):
    task = new_task(people["manager"], people["rahul_emp"])
    assert api_client.delete(f"{TASKS}{task.pk}/?version=1").status_code == 401
    for who in ("rahul", "manager"):  # assignee, and the Ops Manager who created it
        assert _delete(client_for(people[who]), task).status_code == 403
    assert Task.objects.filter(pk=task.pk).exists()


def test_failed_delete_rolls_back_everything(client_for, people, new_task, monkeypatch):
    task = new_task(people["manager"], people["rahul_emp"])

    def boom(*args, **kwargs):
        raise RuntimeError("database refused")

    monkeypatch.setattr(Task, "delete", boom)
    with pytest.raises(RuntimeError):
        _delete(client_for(people["hr"]), task)
    assert Task.objects.filter(pk=task.pk).exists()
    assert TaskAssignment.objects.filter(task=task).exists()
    assert not AuditLog.objects.filter(action="task.deleted").exists()


# --- categories (Admin-managed list) ----------------------------------------------------------


def test_everyone_reads_active_categories(client_for, make_user):
    names = [c["name"] for c in client_for(make_user(roles.EMPLOYEE)).get(CATEGORIES).json()]
    assert names == sorted([
        "Client Servicing", "Compliance", "Coordination", "Finance", "HR", "Marketing",
        "Operations", "Sales",
    ])


def test_admin_manages_categories_with_audit(admin_client):
    created = admin_client.post(CATEGORIES, {"code": " legal ", "name": "Legal"})
    assert created.status_code == 201 and created.json()["code"] == "LEGAL"
    assert admin_client.post(CATEGORIES, {"code": "LEGAL", "name": "Again"}).status_code == 409
    pk = created.json()["id"]
    deactivated = admin_client.patch(f"{CATEGORIES}{pk}/", {"is_active": False})
    assert deactivated.json()["is_active"] is False
    assert "LEGAL" not in [c["code"] for c in admin_client.get(CATEGORIES).json()]
    listed = admin_client.get(CATEGORIES, {"include_inactive": "true"}).json()
    assert "LEGAL" in [c["code"] for c in listed]
    immutable = admin_client.patch(f"{CATEGORIES}{pk}/", {"code": "LAW"})
    assert immutable.status_code == 400 and immutable.json()["code"] == "field_immutable"
    rows = AuditLog.objects.filter(entity_type="task_category")
    actions = sorted(rows.values_list("action", flat=True))
    assert actions == ["task_category.created", "task_category.updated"]
    assert str(_cat("SALES")) == "Sales"


@pytest.mark.parametrize("role", [roles.EMPLOYEE, roles.OPERATIONS_MANAGER, roles.HR])
def test_only_admin_changes_categories(client_for, make_user, role):
    client = client_for(make_user(role))
    assert client.post(CATEGORIES, {"code": "X", "name": "X"}).status_code == 403
    assert client.patch(f"{CATEGORIES}{_cat('SALES').pk}/", {"name": "Y"}).status_code == 403


# --- HR / Admin edit authority is not limited by workflow status ------------------------------


def _closed(client_for, people, new_task, status):
    """A task by the manager for Rahul, then COMPLETED (by Rahul) or CANCELLED (by the manager)."""
    task = new_task(people["manager"], people["rahul_emp"], title="Closed task")
    if status == "COMPLETED":
        rahul = client_for(people["rahul"])
        for action in ("start", "complete"):
            task.refresh_from_db()
            rahul.post(f"{TASKS}{task.pk}/{action}/", {"version": task.version})
    else:
        task.refresh_from_db()
        client_for(people["manager"]).post(
            f"{TASKS}{task.pk}/cancel/", {"version": task.version, "reason": "Duplicate"}
        )
    task.refresh_from_db()
    assert task.status == status
    return task


@pytest.mark.parametrize("status", ["COMPLETED", "CANCELLED"])
@pytest.mark.parametrize("actor", ["hr", "admin"])
def test_hr_and_admin_edit_closed_tasks(client_for, admin_user, people, new_task, status, actor):
    task = _closed(client_for, people, new_task, status)
    client = client_for(admin_user if actor == "admin" else people[actor])
    allowed = set(client.get(f"{TASKS}{task.pk}/").json()["allowed_actions"])
    assert {"edit", "change_department", "delete"} <= allowed
    assert "reassign" not in allowed  # reassignment stays an open-task workflow action
    edited = _patch(client, task, title="Corrected title", category=_cat("COMPLIANCE").pk)
    assert edited.status_code == 200, edited.json()
    assert edited.json()["status"] == status  # editing never changes the workflow status
    moved = _patch(client, task, department=_dept("HR").pk)
    assert moved.status_code == 200 and moved.json()["department"]["code"] == "HR"
    assert AuditLog.objects.filter(action="task.updated", entity_id=str(task.pk)).exists()
    assert AuditLog.objects.filter(action="task.category_changed", entity_id=str(task.pk)).exists()
    dept_row = AuditLog.objects.get(action="task.department_changed", entity_id=str(task.pk))
    assert dept_row.new_value == {"department_id": _dept("HR").pk}


@pytest.mark.parametrize("status", ["COMPLETED", "CANCELLED"])
def test_employees_and_ops_manager_still_cannot_edit_closed_tasks(
    client_for, people, new_task, status
):
    task = _closed(client_for, people, new_task, status)
    for who in ("rahul", "manager"):  # the assignee, and the Ops Manager who created it
        client = client_for(people[who])
        assert _patch(client, task, title="Not allowed").status_code == 403
        assert _patch(client, task, department=_dept("HR").pk).status_code == 403
    task.refresh_from_db()
    assert task.title == "Closed task" and task.department.code == "OPS"


def test_closed_tasks_are_still_not_reassignable(client_for, people, new_task):
    task = _closed(client_for, people, new_task, "COMPLETED")
    response = _reassign(client_for(people["hr"]), task, people["hr2_emp"])
    assert response.status_code == 409 and response.json()["code"] == "invalid_state_transition"


@pytest.mark.parametrize("field", ["acknowledgment_required", "verification_required"])
def test_sla_and_verification_settings_are_frozen_on_closed_tasks(
    client_for, people, new_task, field
):
    task = _closed(client_for, people, new_task, "COMPLETED")
    clocks_before = TaskSla.objects.filter(task=task).count()
    response = _patch(client_for(people["hr"]), task, **{field: True})
    assert response.status_code == 409 and response.json()["code"] == "invalid_state_transition"
    assert TaskSla.objects.filter(task=task).count() == clocks_before  # no new ACK clock


def test_hr_corrects_received_time_on_a_cancelled_task(client_for, people, new_task, now):
    task = _closed(client_for, people, new_task, "CANCELLED")
    response = _patch(
        client_for(people["hr"]),
        task,
        received_at=(now - timedelta(hours=2)).isoformat(),
        received_at_source="EMAIL",
        received_at_reason="Original email found",
    )
    assert response.status_code == 200
    assert AuditLog.objects.filter(action="task.received_at_changed").exists()
