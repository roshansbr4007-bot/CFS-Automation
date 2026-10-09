"""Task creation and assignment rules (Phase 3 rules as changed by the approved Phase 4
decisions: assignment across users and departments, task-level department and category)."""

from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.auth.models import Permission

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.org.models import Department
from apps.org.tests.factories import EmployeeFactory
from apps.tasks.models import Task, TaskCategory, TaskStatus

TASKS = "/api/v1/tasks/"
pytestmark = pytest.mark.django_db


def _payload(assignee, **overrides):
    data = {
        "title": "Process SIP mandate",
        "description": "Client: K. Mehta",
        "priority": "HIGH",
        "assigned_to": assignee.pk,
        "department": Department.objects.get(code="OPS").pk,
        "category": TaskCategory.objects.get(code="OPERATIONS").pk,
    }
    data.update(overrides)
    return data


def test_employee_creates_a_task_for_themselves(client_for, ops):
    response = client_for(ops["rahul"]).post(TASKS, _payload(ops["rahul_emp"]))
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["reference"] == f"T-{body['id']:06d}"
    assert body["created_by"]["id"] == ops["rahul"].pk
    assert body["assigned_to"]["id"] == ops["rahul_emp"].pk
    assert body["assigned_by"]["id"] == ops["rahul"].pk
    assert body["department"]["code"] == "OPS"
    assert body["received_at"] is None and body["received_at_source"] is None
    assert body["verification_status"] == "NOT_REQUIRED" and body["version"] == 1
    assert [a["to_employee"]["id"] for a in body["assignments"]] == [ops["rahul_emp"].pk]
    # Only this task's audit rows: logging in also writes the (legitimate) Phase 2
    # employee.daily_login_recorded row, which is not part of task creation.
    task_rows = AuditLog.objects.filter(entity_type="task", entity_id=str(body["id"]))
    actions = list(task_rows.order_by("id").values_list("action", flat=True))
    assert actions == ["task.created", "task.assigned"]


def _creator_without_assign(make_user):
    """A user who may create tasks but holds no assign permission (no role grants this mix by
    default since Phase 4; it proves the self-only rule still exists)."""
    user = make_user()
    user.user_permissions.add(
        Permission.objects.get(codename="create_task", content_type__app_label="tasks")
    )
    return type(user).objects.get(pk=user.pk)


def test_creator_without_tasks_assign_still_cannot_assign_to_a_colleague(
    client_for, make_user, ops
):
    """Phase 3 test kept: the self-only rule still applies to creators without tasks.assign.
    (Changed in Phase 4: the Employee ROLE now holds tasks.assign, so this uses a bare user.)"""
    response = client_for(_creator_without_assign(make_user)).post(TASKS, _payload(ops["amit_emp"]))
    assert response.status_code == 403
    assert response.json()["code"] == "assignment_not_allowed"
    assert not Task.objects.exists()


def test_employee_role_assigns_to_another_department_and_department_is_not_derived(
    client_for, ops, staff
):
    """Changed in Phase 4: department is chosen by the creator and never follows the assignee."""
    _, rm_emp = staff(roles.EMPLOYEE, department="RM")
    response = client_for(ops["rahul"]).post(TASKS, _payload(rm_emp))
    assert response.status_code == 201
    assert response.json()["department"]["code"] == "OPS"  # as chosen, assignee is in RM
    assert response.json()["category"]["code"] == "OPERATIONS"


def test_operations_manager_assigns_across_departments(client_for, ops, staff):
    """Changed in Phase 4: department no longer blocks an Operations Manager's assignment."""
    client = client_for(ops["manager"])
    assert client.post(TASKS, _payload(ops["amit_emp"])).status_code == 201
    _, rm_emp = staff(roles.EMPLOYEE, department="RM")
    assert client.post(TASKS, _payload(rm_emp)).status_code == 201


def test_admin_assigns_anywhere(admin_client, staff):
    _, rm_emp = staff(roles.EMPLOYEE, department="RM")
    assert admin_client.post(TASKS, _payload(rm_emp)).status_code == 201


def test_hr_creates_and_assigns_tasks(client_for, staff, ops):
    """Changed in Phase 4: HR is no longer read-only on tasks."""
    hr, hr_emp = staff(roles.HR, department="HR")
    assert client_for(hr).post(TASKS, _payload(hr_emp)).status_code == 201
    assert client_for(hr).post(TASKS, _payload(ops["rahul_emp"])).status_code == 201
    assert client_for(hr).get(f"{TASKS}assignees/").status_code == 200


def test_inactive_assignee_is_refused(admin_client):
    inactive = EmployeeFactory(is_active=False)
    response = admin_client.post(TASKS, _payload(inactive))
    assert response.status_code == 403 and response.json()["code"] == "assignment_not_allowed"


def test_user_without_employee_record_or_assign_cannot_assign(client_for, make_user):
    """Changed in Phase 4: the Employee role can assign to others, so the self-only case is
    shown with a creator who has no assign permission and no employee record."""
    creator = _creator_without_assign(make_user)
    response = client_for(creator).post(TASKS, _payload(EmployeeFactory()))
    assert response.status_code == 403


def test_users_without_any_task_role_cannot_create(client_for, make_user, ops):
    assert client_for(make_user()).post(TASKS, _payload(ops["rahul_emp"])).status_code == 403


@pytest.mark.parametrize("field", ["department", "category"])
def test_department_and_category_are_required(client_for, ops, field):
    payload = _payload(ops["rahul_emp"])
    del payload[field]
    response = client_for(ops["rahul"]).post(TASKS, payload)
    assert response.status_code == 400 and field in response.json()["fields"]


def test_inactive_category_is_refused(client_for, ops):
    TaskCategory.objects.filter(code="SALES").update(is_active=False)
    sales = TaskCategory.objects.get(code="SALES")
    response = client_for(ops["rahul"]).post(TASKS, _payload(ops["rahul_emp"], category=sales.pk))
    assert response.status_code == 400 and "category" in response.json()["fields"]


def test_recurring_type_is_reserved_for_the_scheduler(client_for, ops):
    payload = _payload(ops["rahul_emp"], task_type="RECURRING")
    response = client_for(ops["rahul"]).post(TASKS, payload)
    assert response.status_code == 400 and "task_type" in response.json()["fields"]


@pytest.mark.parametrize("value", ["", "   "])
def test_title_is_required(client_for, ops, value):
    response = client_for(ops["rahul"]).post(TASKS, _payload(ops["rahul_emp"], title=value))
    assert response.status_code == 400 and "title" in response.json()["fields"]


def test_received_at_is_kept_separate_from_created_at(client_for, ops, now):
    received = (now - timedelta(hours=3)).replace(microsecond=0)
    response = client_for(ops["rahul"]).post(
        TASKS,
        _payload(ops["rahul_emp"], received_at=received.isoformat(), received_at_source="EMAIL"),
    )
    assert response.status_code == 201
    task = Task.objects.get()
    assert task.received_at == received and task.received_at_source == "EMAIL"
    assert task.created_at > task.received_at
    row = AuditLog.objects.get(action="task.created")
    # Same instant, whatever offset the ISO string carries (+00:00 or +05:30).
    audited = datetime.fromisoformat(row.new_value["received_at"])
    assert audited.astimezone(UTC) == received.astimezone(UTC)
    assert row.context["received_at_entered"] is True


@pytest.mark.parametrize(
    "extra",
    [
        {"received_at_source": "EMAIL"},  # source without time
        {"received_at": "2026-01-01T10:00:00+05:30"},  # time without source
    ],
)
def test_received_at_and_source_go_together(client_for, ops, extra):
    response = client_for(ops["rahul"]).post(TASKS, _payload(ops["rahul_emp"], **extra))
    assert response.status_code == 400 and "received_at_source" in response.json()["fields"]


def test_received_at_cannot_be_in_the_future(client_for, ops, now):
    future = (now + timedelta(days=1)).isoformat()
    response = client_for(ops["rahul"]).post(
        TASKS, _payload(ops["rahul_emp"], received_at=future, received_at_source="MANUAL")
    )
    assert response.status_code == 400 and "received_at" in response.json()["fields"]


def test_invalid_choices_and_unknown_assignee(client_for, ops):
    client = client_for(ops["rahul"])
    bad_priority = client.post(TASKS, _payload(ops["rahul_emp"], priority="CRITICAL"))
    assert "priority" in bad_priority.json()["fields"]
    bad_assignee = client.post(TASKS, _payload(ops["rahul_emp"], assigned_to=999999))
    assert "assigned_to" in bad_assignee.json()["fields"]


def test_new_task_starts_pending(admin_user, ops, new_task):
    task = new_task(admin_user, ops["rahul_emp"])
    assert task.status == TaskStatus.PENDING and task.department.code == "OPS"


def test_anonymous_gets_401(api_client):
    assert api_client.get(TASKS).status_code == 401
    assert api_client.post(TASKS, {}).status_code == 401
