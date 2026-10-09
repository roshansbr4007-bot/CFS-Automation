import pytest

from apps.accounts import roles
from apps.audit.models import AuditLog

DEPTS = "/api/v1/departments/"
pytestmark = pytest.mark.django_db


def test_any_signed_in_user_lists_all_departments_unpaginated(client_for, make_user):
    body = client_for(make_user(roles.EMPLOYEE)).get(DEPTS).json()
    assert [d["code"] for d in body] == ["HR", "INS", "LOAN", "OPS", "RM"]
    assert {"id", "code", "name", "is_live", "created_at", "updated_at"} == set(body[0])


def test_anonymous_cannot_list(api_client):
    assert api_client.get(DEPTS).status_code == 401


def test_detail(client_for, make_user, dept):
    body = client_for(make_user()).get(f"{DEPTS}{dept('OPS').pk}/").json()
    assert body["code"] == "OPS" and body["is_live"] is True


def test_admin_creates_department_with_uppercased_code(admin_client, admin_user):
    response = admin_client.post(DEPTS, {"code": " mgmt ", "name": "Management"})
    assert response.status_code == 201
    body = response.json()
    assert body["code"] == "MGMT" and body["is_live"] is False
    row = AuditLog.objects.get(action="department.created")
    assert row.actor_user == admin_user
    assert row.new_value == {"code": "MGMT", "name": "Management", "is_live": False}


@pytest.mark.parametrize("code", ["", "1OPS", "OPS-1", "A" * 17])
def test_invalid_codes_are_rejected(admin_client, code):
    response = admin_client.post(DEPTS, {"code": code, "name": "X"})
    assert response.status_code == 400
    assert "code" in response.json()["fields"]


def test_duplicate_code_conflicts(admin_client):
    response = admin_client.post(DEPTS, {"code": "ops", "name": "Again"})
    assert response.status_code == 409
    assert response.json()["code"] == "department_code_taken"
    assert not AuditLog.objects.filter(action="department.created").exists()


def test_admin_updates_name_and_live_flag(admin_client, dept):
    rm = dept("RM")
    changes = {"name": "Relationship Mgmt", "is_live": True}
    response = admin_client.patch(f"{DEPTS}{rm.pk}/", changes)
    assert response.status_code == 200
    assert response.json()["is_live"] is True
    row = AuditLog.objects.get(action="department.updated")
    assert row.old_value == {"name": "Relationship Management", "is_live": False}
    assert row.new_value == {"name": "Relationship Mgmt", "is_live": True}


def test_code_is_immutable(admin_client, dept):
    response = admin_client.patch(f"{DEPTS}{dept('RM').pk}/", {"code": "RMX"})
    assert response.status_code == 400
    assert response.json()["code"] == "field_immutable"


def test_sending_the_same_code_is_not_a_change(admin_client, dept):
    same = {"code": "rm", "name": "Relationship Management"}
    response = admin_client.patch(f"{DEPTS}{dept('RM').pk}/", same)
    assert response.status_code == 200
    assert not AuditLog.objects.filter(action="department.updated").exists()


@pytest.mark.parametrize("role", [roles.EMPLOYEE, roles.OPERATIONS_MANAGER, roles.HR])
def test_only_admin_writes(client_for, make_user, dept, role):
    client = client_for(make_user(role))
    assert client.post(DEPTS, {"code": "NEW", "name": "New"}).status_code == 403
    assert client.patch(f"{DEPTS}{dept('RM').pk}/", {"name": "X"}).status_code == 403


def test_unknown_department_is_404(admin_client):
    assert admin_client.get(f"{DEPTS}999999/").status_code == 404
    assert admin_client.patch(f"{DEPTS}999999/", {"name": "X"}).status_code == 404


def test_no_delete(admin_client, dept):
    assert admin_client.delete(f"{DEPTS}{dept('RM').pk}/").status_code == 405
