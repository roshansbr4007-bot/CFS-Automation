import pytest

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.org.tests.factories import EmployeeFactory

EMPLOYEES = "/api/v1/employees/"
pytestmark = pytest.mark.django_db


def _link(client, employee, user):
    employee.refresh_from_db()
    return client.post(
        f"{EMPLOYEES}{employee.pk}/link-login/", {"version": employee.version, "user": user.pk}
    )


def _unlink(client, employee):
    employee.refresh_from_db()
    return client.post(f"{EMPLOYEES}{employee.pk}/unlink-login/", {"version": employee.version})


def test_admin_links_login_with_matching_email_ignoring_case(admin_client, admin_user, make_user):
    login = make_user(email="meera@example.com")
    employee = EmployeeFactory(email="Meera@Example.com")
    response = _link(admin_client, employee, login)
    assert response.status_code == 200
    assert response.json()["user"]["id"] == login.pk and response.json()["version"] == 2
    row = AuditLog.objects.get(action="employee.login_linked")
    assert row.actor_user == admin_user
    assert row.old_value == {"user_id": None} and row.new_value == {"user_id": login.pk}


def test_email_mismatch_is_rejected(admin_client, make_user):
    response = _link(admin_client, EmployeeFactory(), make_user(email="someone@example.com"))
    assert response.status_code == 400 and response.json()["code"] == "login_email_mismatch"
    assert not AuditLog.objects.exists()


def test_login_already_linked_to_another_employee(admin_client, person):
    user, _ = person()
    other = EmployeeFactory(email="second@example.com")
    response = _link(admin_client, other, user)
    assert response.status_code == 409 and response.json()["code"] == "login_already_linked"


def test_employee_already_linked(admin_client, person, make_user):
    _, employee = person()
    response = _link(admin_client, employee, make_user(email=employee.email.replace("@", "+2@")))
    assert response.status_code == 409 and response.json()["code"] == "employee_already_linked"


def test_unlink_then_relink(admin_client, person):
    user, employee = person()
    response = _unlink(admin_client, employee)
    assert response.status_code == 200 and response.json()["user"] is None
    row = AuditLog.objects.get(action="employee.login_unlinked")
    assert row.old_value == {"user_id": user.pk} and row.new_value == {"user_id": None}
    assert _link(admin_client, employee, user).status_code == 200


def test_unlink_without_login(admin_client):
    response = _unlink(admin_client, EmployeeFactory())
    assert response.status_code == 409 and response.json()["code"] == "no_login_linked"


def test_stale_version_on_link_and_unlink(admin_client, person, make_user):
    _, linked = person()
    unlinked = EmployeeFactory(email="v@example.com")
    stale_link = admin_client.post(
        f"{EMPLOYEES}{unlinked.pk}/link-login/",
        {"version": 5, "user": make_user(email="v@example.com").pk},
    )
    assert stale_link.status_code == 409 and stale_link.json()["code"] == "version_conflict"
    stale_unlink = admin_client.post(f"{EMPLOYEES}{linked.pk}/unlink-login/", {"version": 5})
    assert stale_unlink.status_code == 409


@pytest.mark.parametrize("role", [roles.HR, roles.OPERATIONS_MANAGER, roles.EMPLOYEE])
def test_only_admin_links(client_for, make_user, role):
    employee = EmployeeFactory(email="target@example.com")
    login = make_user(email="target@example.com")
    assert _link(client_for(make_user(role)), employee, login).status_code == 403
    assert _unlink(client_for(make_user(role)), employee).status_code == 403
