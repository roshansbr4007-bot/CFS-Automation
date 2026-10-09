import pytest

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.org import services
from apps.org.models import Employee
from apps.org.tests.factories import EmployeeFactory

EMPLOYEES = "/api/v1/employees/"
pytestmark = pytest.mark.django_db


def _payload(dept, **overrides):
    data = {
        "full_name": "Ravi Sharma",
        "email": "Ravi.Sharma@Example.com",
        "department": dept("OPS").pk,
        "employee_code": "CFS-010",
        "designation": "Operations Executive",
    }
    data.update(overrides)
    return data


def _patch(client, employee, **changes):
    employee.refresh_from_db()
    return client.patch(f"{EMPLOYEES}{employee.pk}/", {"version": employee.version, **changes})


# --- create -----------------------------------------------------------------------------------


@pytest.mark.parametrize("role", [roles.HR, roles.ADMIN])
def test_hr_and_admin_create_employee(client_for, make_user, dept, role):
    actor = make_user(role)
    response = client_for(actor).post(EMPLOYEES, _payload(dept))
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ravi.sharma@example.com"
    assert body["department"] == {"id": dept("OPS").pk, "code": "OPS", "name": "Operations"}
    assert body["user"] is None and body["reporting_manager"] is None
    assert body["is_active"] is True and body["version"] == 1
    row = AuditLog.objects.get(action="employee.created")
    assert row.actor_user == actor
    assert row.context["department_id"] == dept("OPS").pk
    assert row.new_value["employee_code"] == "CFS-010"


def test_blank_code_is_stored_as_null(hr_client, dept):
    body = hr_client.post(EMPLOYEES, _payload(dept, employee_code="  ")).json()
    assert body["employee_code"] is None


def test_operations_manager_and_employee_cannot_create(client_for, make_user, dept):
    for role in (roles.OPERATIONS_MANAGER, roles.EMPLOYEE):
        assert client_for(make_user(role)).post(EMPLOYEES, _payload(dept)).status_code == 403


def test_email_taken_ignoring_case(hr_client, dept):
    EmployeeFactory(email="ravi.sharma@example.com")
    response = hr_client.post(EMPLOYEES, _payload(dept))
    assert response.status_code == 409 and response.json()["code"] == "email_taken"


def test_code_taken(hr_client, dept):
    EmployeeFactory(employee_code="CFS-010")
    response = hr_client.post(EMPLOYEES, _payload(dept))
    assert response.status_code == 409 and response.json()["code"] == "employee_code_taken"


def test_inactive_manager_rejected(hr_client, dept):
    manager = EmployeeFactory(is_active=False)
    response = hr_client.post(EMPLOYEES, _payload(dept, reporting_manager=manager.pk))
    assert response.status_code == 400
    assert "reporting_manager" in response.json()["fields"]


def test_unknown_department_or_manager_is_a_field_error(hr_client, dept):
    response = hr_client.post(EMPLOYEES, _payload(dept, department=999999))
    assert "department" in response.json()["fields"]
    response = hr_client.post(EMPLOYEES, _payload(dept, reporting_manager=999999))
    assert "reporting_manager" in response.json()["fields"]


def test_create_race_on_unique_fields_becomes_conflict(hr_client, dept, monkeypatch):
    def racing_create(**kwargs):
        from django.db import IntegrityError

        raise IntegrityError("duplicate")

    monkeypatch.setattr(Employee.objects, "create", racing_create)
    response = hr_client.post(EMPLOYEES, _payload(dept))
    assert response.status_code == 409 and response.json()["code"] == "employee_conflict"


# --- update -----------------------------------------------------------------------------------


def test_update_audits_changed_fields_and_bumps_version(hr_client, dept):
    employee = EmployeeFactory(full_name="Anu Jain", designation="Executive")
    response = _patch(hr_client, employee, full_name="Anushka Jain", designation="Executive")
    assert response.status_code == 200 and response.json()["version"] == 2
    row = AuditLog.objects.get(action="employee.updated")
    assert row.old_value == {"full_name": "Anu Jain"}
    assert row.new_value == {"full_name": "Anushka Jain"}


def test_department_change_records_old_and_new_department(hr_client, dept):
    employee = EmployeeFactory()
    _patch(hr_client, employee, department=dept("RM").pk)
    row = AuditLog.objects.get(action="employee.updated")
    assert row.old_value == {"department_id": dept("OPS").pk}
    assert row.new_value == {"department_id": dept("RM").pk}
    assert row.context["department_id"] == dept("RM").pk
    assert row.context["old_department_id"] == dept("OPS").pk


def test_version_is_required_and_checked(hr_client):
    employee = EmployeeFactory()
    assert hr_client.patch(f"{EMPLOYEES}{employee.pk}/", {"full_name": "X"}).status_code == 400
    stale = hr_client.patch(f"{EMPLOYEES}{employee.pk}/", {"version": 99, "full_name": "X"})
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"


def test_no_change_writes_no_audit_and_keeps_version(hr_client):
    employee = EmployeeFactory(full_name="Same")
    response = _patch(hr_client, employee, full_name="Same")
    assert response.json()["version"] == 1
    assert not AuditLog.objects.exists()


def test_deactivate_and_reactivate_keep_the_login_active(hr_client, person):
    user, employee = person()
    _patch(hr_client, employee, is_active=False)
    _patch(hr_client, employee, is_active=True)
    actions = list(AuditLog.objects.order_by("id").values_list("action", flat=True))
    assert actions == ["employee.deactivated", "employee.reactivated"]
    user.refresh_from_db()
    assert user.is_active is True  # Q4: deactivating an employee never touches the login


def test_field_change_and_status_change_in_one_patch_write_two_rows(hr_client):
    employee = EmployeeFactory()
    _patch(hr_client, employee, designation="Lead", is_active=False)
    actions = list(AuditLog.objects.order_by("id").values_list("action", flat=True))
    assert actions == ["employee.updated", "employee.deactivated"]


def test_manager_rules_on_update(hr_client):
    a, b, c = EmployeeFactory(), EmployeeFactory(), EmployeeFactory()
    assert _patch(hr_client, a, reporting_manager=a.pk).status_code == 400  # self
    assert _patch(hr_client, a, reporting_manager=b.pk).status_code == 200  # a -> b
    assert _patch(hr_client, b, reporting_manager=c.pk).status_code == 200  # b -> c
    cycle = _patch(hr_client, c, reporting_manager=a.pk)  # c -> a would close a -> b -> c -> a
    assert cycle.status_code == 400
    assert "cycle" in cycle.json()["fields"]["reporting_manager"][0]
    assert _patch(hr_client, a, reporting_manager=None).json()["reporting_manager"] is None


def test_code_and_email_changes_are_checked_for_conflicts(hr_client):
    EmployeeFactory(email="taken@example.com", employee_code="CFS-001")
    employee = EmployeeFactory()
    assert _patch(hr_client, employee, email="TAKEN@example.com").json()["code"] == "email_taken"
    code_taken = _patch(hr_client, employee, employee_code="CFS-001")
    assert code_taken.json()["code"] == "employee_code_taken"
    assert _patch(hr_client, employee, employee_code="").json()["employee_code"] is None


def test_email_change_must_keep_matching_linked_login(hr_client, person):
    user, employee = person()
    response = _patch(hr_client, employee, email="other@example.com")
    assert response.status_code == 400
    assert response.json()["code"] == "login_email_mismatch"
    assert _patch(hr_client, employee, email=user.email.upper()).status_code == 200


def test_email_change_allowed_without_login(hr_client):
    employee = EmployeeFactory()
    assert _patch(hr_client, employee, email="New@Example.com").json()["email"] == "new@example.com"


def test_operations_manager_cannot_update(client_for, ops_manager):
    user, own = ops_manager
    response = client_for(user).patch(f"{EMPLOYEES}{own.pk}/", {"version": 1, "full_name": "X"})
    assert response.status_code == 403


def test_no_delete_or_put(admin_client):
    employee = EmployeeFactory()
    assert admin_client.delete(f"{EMPLOYEES}{employee.pk}/").status_code == 405
    assert admin_client.put(f"{EMPLOYEES}{employee.pk}/", {}).status_code == 405


def test_update_service_rejects_unknown_fields(admin_user):
    employee = EmployeeFactory()
    with pytest.raises(TypeError):
        services.update_employee(actor=admin_user, employee=employee, version=1, user=None)


# --- me and filters ---------------------------------------------------------------------------


def test_me_returns_own_record(client_for, person):
    user, employee = person(roles.EMPLOYEE)
    body = client_for(user).get(f"{EMPLOYEES}me/").json()
    assert body["id"] == employee.pk
    assert body["user"] == {"id": user.pk, "email": user.email, "is_active": True}


def test_me_without_record_is_404(client_for, make_user):
    response = client_for(make_user()).get(f"{EMPLOYEES}me/")
    assert response.status_code == 404 and response.json()["code"] == "no_employee_record"


def test_list_filters(hr_client, dept, person):
    person(department="OPS", full_name="Priya Shah")
    EmployeeFactory(
        full_name="Kiran Rao", department=dept("RM"), is_active=False, employee_code="K-1"
    )

    def names(**params):
        return [e["full_name"] for e in hr_client.get(EMPLOYEES, params).json()["results"]]

    assert names(search="priya") == ["Priya Shah"]
    assert names(search="k-1") == ["Kiran Rao"]
    assert names(department=dept("RM").pk) == ["Kiran Rao"]
    assert names(is_active="false") == ["Kiran Rao"]
    assert names(has_login="true") == ["Priya Shah"]
    assert names(has_login="0") == ["Kiran Rao"]


@pytest.mark.parametrize(
    ("params", "field"),
    [({"department": "ops"}, "department"), ({"is_active": "maybe"}, "is_active")],
)
def test_invalid_filters(hr_client, params, field):
    response = hr_client.get(EMPLOYEES, params)
    assert response.status_code == 400 and field in response.json()["fields"]
