"""Who sees which tasks, the Received/Sent/All views, filters and the assignee picker."""

import pytest
from django.contrib.auth.models import AnonymousUser

from apps.accounts import roles
from apps.tasks import policy, selectors, services
from apps.tasks.errors import TaskPermissionDenied
from apps.tasks.models import Task

TASKS = "/api/v1/tasks/"
pytestmark = pytest.mark.django_db


def _ids(response):
    assert response.status_code == 200, response.json()
    return {t["id"] for t in response.json()["results"]}


@pytest.fixture
def board(ops, staff, new_task, grant_assign):
    """Rahul (with tasks.assign) sends one task to Amit and one to himself;
    the manager sends one to Amit; an RM task exists elsewhere."""
    rahul = grant_assign(ops["rahul"])
    rm_user, rm_emp = staff(roles.EMPLOYEE, department="RM")
    return {
        "to_amit": new_task(rahul, ops["amit_emp"], title="Mandate for Amit", priority="HIGH"),
        "to_self": new_task(rahul, ops["rahul_emp"], title="Own reminder", priority="LOW"),
        "mgr_to_amit": new_task(ops["manager"], ops["amit_emp"], title="Manager request"),
        "rm_task": new_task(rm_user, rm_emp, title="RM follow-up"),
        "rahul": rahul,
    }


def test_received_and_sent_views_use_the_same_task_rows(client_for, ops, board):
    rahul = client_for(board["rahul"])
    assert _ids(rahul.get(TASKS, {"view": "received"})) == {board["to_self"].pk}
    assert _ids(rahul.get(TASKS, {"view": "sent"})) == {board["to_amit"].pk, board["to_self"].pk}
    assert _ids(rahul.get(TASKS)) == {board["to_amit"].pk, board["to_self"].pk}
    amit = client_for(ops["amit"])
    received = _ids(amit.get(TASKS, {"view": "received"}))
    assert received == {board["to_amit"].pk, board["mgr_to_amit"].pk}
    assert _ids(amit.get(TASKS, {"view": "sent"})) == set()
    assert Task.objects.count() == 4  # nothing duplicated for display


def test_employee_cannot_see_other_peoples_tasks(client_for, ops, board):
    amit = client_for(ops["amit"])
    assert board["to_self"].pk not in _ids(amit.get(TASKS))
    assert amit.get(f"{TASKS}{board['to_self'].pk}/").status_code == 404


def test_manager_sees_own_department_only(client_for, ops, board):
    manager = client_for(ops["manager"])
    expected = {board["to_amit"].pk, board["to_self"].pk, board["mgr_to_amit"].pk}
    assert _ids(manager.get(TASKS)) == expected
    assert manager.get(f"{TASKS}{board['rm_task'].pk}/").status_code == 404


def test_manager_without_active_employee_record_sees_only_own(client_for, staff, board):
    inactive_mgr, _ = staff(roles.OPERATIONS_MANAGER, employee_active=False)
    assert _ids(client_for(inactive_mgr).get(TASKS)) == set()


@pytest.mark.parametrize("role", [roles.HR, roles.ADMIN])
def test_hr_and_admin_see_everything(client_for, make_user, board, role):
    assert len(_ids(client_for(make_user(role)).get(TASKS))) == 4


def test_filters(client_for, ops, board):
    manager = client_for(ops["manager"])
    assert _ids(manager.get(TASKS, {"priority": "HIGH"})) == {board["to_amit"].pk}
    assert _ids(manager.get(TASKS, {"assignee": ops["amit_emp"].pk})) == {
        board["to_amit"].pk,
        board["mgr_to_amit"].pk,
    }
    assert _ids(manager.get(TASKS, {"search": "reminder"})) == {board["to_self"].pk}
    in_ops = {"status": "PENDING", "department": ops["amit_emp"].department_id}
    assert _ids(manager.get(TASKS, in_ops)) == {
        board["to_amit"].pk,
        board["to_self"].pk,
        board["mgr_to_amit"].pk,
    }
    assert _ids(manager.get(TASKS, {"from": "2000-01-01", "to": "2999-12-31"})) != set()
    assert _ids(manager.get(TASKS, {"to": "2000-01-01"})) == set()


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"view": "mine"}, "view"),
        ({"status": "DONE"}, "status"),
        ({"priority": "CRITICAL"}, "priority"),
        ({"assignee": "amit"}, "assignee"),
        ({"department": "ops"}, "department"),
        ({"from": "2026-13-01"}, "from"),
        ({"to": "yesterday"}, "to"),
    ],
)
def test_invalid_filters(client_for, ops, params, field):
    response = client_for(ops["rahul"]).get(TASKS, params)
    assert response.status_code == 400 and field in response.json()["fields"]


def test_allowed_actions_reflect_role_and_state(client_for, ops, board):
    task = board["mgr_to_amit"]
    amit = client_for(ops["amit"]).get(f"{TASKS}{task.pk}/").json()["allowed_actions"]
    assert set(amit) == {"start", "block", "comment", "attach"}
    manager = client_for(ops["manager"]).get(f"{TASKS}{task.pk}/").json()["allowed_actions"]
    assert set(manager) == {
        "edit",
        "change_department",  # Phase 4: managers may change the department of their tasks
        "reassign",
        "cancel",
        "block",
        "comment",
        "attach",
    }


def test_assignee_picker_follows_assignment_rights(client_for, ops, staff, board, make_user):
    def ids(user):
        response = client_for(user).get(f"{TASKS}assignees/")
        assert response.status_code == 200
        return {e["id"] for e in response.json()}

    rm_emp_ids = {e.pk for e in Task.objects.get(pk=board["rm_task"].pk).department.employees.all()}
    # Changed in Phase 4: every task role holds tasks.assign, so everyone may pick any active
    # employee in any department (it was "self only" for Employees, "own department" for OMs).
    for user in (ops["amit"], ops["manager"], board["rahul"], make_user(roles.ADMIN)):
        picked = ids(user)
        assert rm_emp_ids <= picked
        assert {ops["amit_emp"].pk, ops["rahul_emp"].pk, ops["manager_emp"].pk} <= picked


def test_users_without_create_permission_have_no_assignees_and_cannot_create(
    make_user, ops, category
):
    """Changed in Phase 4: HR may now create tasks, so this uses a user with no task role."""
    nobody = make_user()
    assert not selectors.assignable_employees(nobody).exists()
    with pytest.raises(TaskPermissionDenied):
        services.create_task(
            actor=nobody,
            title="x",
            assigned_to=ops["rahul_emp"],
            department=ops["rahul_emp"].department,
            category=category,
        )


def test_anonymous_users_have_no_employee_and_see_no_tasks():
    assert policy.own_employee(AnonymousUser()) is None
    assert not selectors.visible_tasks(AnonymousUser()).exists()
