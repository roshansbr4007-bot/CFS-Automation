"""Record-level scope (approved Q7/Q8): out-of-scope records answer 404."""

import pytest

from apps.accounts import roles
from apps.org.models import EmployeeDailyLogin
from apps.org.tests.factories import EmployeeFactory

EMPLOYEES = "/api/v1/employees/"
pytestmark = pytest.mark.django_db


@pytest.fixture
def people(dept):
    return {
        "ops_active": EmployeeFactory(full_name="Ops Active", department=dept("OPS")),
        "ops_inactive": EmployeeFactory(
            full_name="Ops Inactive", department=dept("OPS"), is_active=False
        ),
        "rm": EmployeeFactory(full_name="RM Person", department=dept("RM")),
    }


def _ids(response):
    return {e["id"] for e in response.json()["results"]}


@pytest.mark.parametrize("role", [roles.HR, roles.ADMIN])
def test_hr_and_admin_see_everyone(client_for, make_user, people, role):
    client = client_for(make_user(role))
    assert _ids(client.get(EMPLOYEES)) == {e.pk for e in people.values()}
    assert client.get(f"{EMPLOYEES}{people['rm'].pk}/").status_code == 200


def test_ops_manager_sees_own_department_including_inactive(client_for, ops_manager, people):
    user, own = ops_manager
    client = client_for(user)
    expected = {own.pk, people["ops_active"].pk, people["ops_inactive"].pk}
    assert _ids(client.get(EMPLOYEES)) == expected
    assert client.get(f"{EMPLOYEES}{people['ops_inactive'].pk}/").status_code == 200
    assert client.get(f"{EMPLOYEES}{people['rm'].pk}/").status_code == 404


def test_ops_manager_without_employee_record_sees_nobody(client_for, make_user, people):
    client = client_for(make_user(roles.OPERATIONS_MANAGER))
    assert _ids(client.get(EMPLOYEES)) == set()
    assert client.get(f"{EMPLOYEES}{people['ops_active'].pk}/").status_code == 404


def test_inactive_ops_manager_has_an_empty_team_but_sees_own_record(client_for, person, people):
    user, own = person(roles.OPERATIONS_MANAGER, is_active=False)
    client = client_for(user)
    assert _ids(client.get(EMPLOYEES)) == {own.pk}
    assert client.get(f"{EMPLOYEES}{people['ops_active'].pk}/").status_code == 404


def test_employee_sees_only_own_record(client_for, person, people):
    user, own = person(roles.EMPLOYEE)
    client = client_for(user)
    assert client.get(EMPLOYEES).status_code == 403
    assert client.get(f"{EMPLOYEES}{own.pk}/").status_code == 200
    assert client.get(f"{EMPLOYEES}{people['ops_active'].pk}/").status_code == 404


def test_login_history_follows_the_same_scope(client_for, ops_manager, people):
    EmployeeDailyLogin.objects.create(
        employee=people["ops_active"], work_date="2026-10-05", first_login_at="2026-10-05T04:00:00Z"
    )
    user, _ = ops_manager
    client = client_for(user)
    body = client.get(f"{EMPLOYEES}{people['ops_active'].pk}/logins/").json()
    assert body["results"] == [
        {"work_date": "2026-10-05", "first_login_at": "2026-10-05T09:30:00+05:30"}
    ]
    assert client.get(f"{EMPLOYEES}{people['rm'].pk}/logins/").status_code == 404


def test_anonymous_gets_401(api_client, people):
    assert api_client.get(EMPLOYEES).status_code == 401
    assert api_client.get(f"{EMPLOYEES}{people['rm'].pk}/").status_code == 401
