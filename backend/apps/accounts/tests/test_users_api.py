import pytest

from apps.accounts import roles
from apps.accounts.models import User
from apps.audit import actions
from apps.audit.models import AuditLog
from apps.recurring.generator import SCHEDULER_EMAIL

USERS = "/api/v1/users/"
NEW_PASSWORD = "Jaipur-Ledger-Blue-77"
pytestmark = pytest.mark.django_db


def _create(client, **overrides):
    payload = {
        "email": "ravi@example.com",
        "first_name": "Ravi",
        "last_name": "Sharma",
        "roles": [roles.EMPLOYEE, roles.OPERATIONS_MANAGER],
        "password": NEW_PASSWORD,
    }
    payload.update(overrides)
    return client.post(USERS, payload)


def test_u1_admin_creates_user_with_roles(admin_client, admin_user):
    response = _create(admin_client)
    assert response.status_code == 201
    body = response.json()
    assert body["roles"] == ["Employee", "Operations Manager"]
    user = User.objects.get(email="ravi@example.com")
    assert user.check_password(NEW_PASSWORD)
    row = AuditLog.objects.get(action=actions.USER_CREATED)
    assert row.actor_user == admin_user
    assert row.new_value == {
        "email": "ravi@example.com", "first_name": "Ravi", "last_name": "Sharma",
        "roles": ["Employee", "Operations Manager"], "is_active": True,
    }


def test_u2_duplicate_email_any_case(admin_client, make_user):
    make_user(email="ravi@example.com")
    response = _create(admin_client, email="RAVI@example.com")
    assert response.status_code == 409
    assert response.json()["code"] == "email_taken"
    assert not AuditLog.objects.filter(action=actions.USER_CREATED).exists()


def test_create_rejects_weak_password_and_unknown_role(admin_client):
    weak = _create(admin_client, password="123")
    assert weak.status_code == 400 and "password" in weak.json()["fields"]
    bad_role = _create(admin_client, roles=["Superhero"])
    assert bad_role.status_code == 400 and "roles" in bad_role.json()["fields"]
    assert User.objects.filter(email="ravi@example.com").count() == 0


def test_u3_role_change_is_audited(admin_client, make_user):
    user = make_user(roles.EMPLOYEE)
    response = admin_client.patch(f"{USERS}{user.pk}/", {"roles": [roles.HR]})
    assert response.status_code == 200 and response.json()["roles"] == ["HR"]
    row = AuditLog.objects.get(action=actions.USER_ROLE_CHANGED)
    assert row.old_value == {"roles": ["Employee"]} and row.new_value == {"roles": ["HR"]}


def test_name_change_audits_changed_fields_only(admin_client, make_user):
    user = make_user(first_name="Anu", last_name="Jain")
    admin_client.patch(f"{USERS}{user.pk}/", {"first_name": "Anushka", "last_name": "Jain"})
    row = AuditLog.objects.get(action=actions.USER_UPDATED)
    assert row.old_value == {"first_name": "Anu"} and row.new_value == {"first_name": "Anushka"}


def test_patch_with_no_changes_writes_no_audit(admin_client, make_user):
    user = make_user(roles.EMPLOYEE, first_name="Anu")
    unchanged = {"first_name": "Anu", "roles": ["Employee"]}
    response = admin_client.patch(f"{USERS}{user.pk}/", unchanged)
    assert response.status_code == 200
    assert AuditLog.objects.count() == 0


def test_u4_deactivate_ends_sessions(admin_client, client_for, make_user):
    user = make_user(roles.EMPLOYEE)
    user_client = client_for(user)
    assert user_client.get("/api/v1/auth/me/").status_code == 200
    response = admin_client.patch(f"{USERS}{user.pk}/", {"is_active": False})
    assert response.status_code == 200 and response.json()["is_active"] is False
    assert user_client.get("/api/v1/auth/me/").status_code == 401
    row = AuditLog.objects.get(action=actions.USER_DEACTIVATED)
    assert row.old_value == {"is_active": True} and row.new_value == {"is_active": False}

    admin_client.patch(f"{USERS}{user.pk}/", {"is_active": True})
    assert AuditLog.objects.filter(action=actions.USER_ACTIVATED).count() == 1


def test_u5_admin_sets_password(admin_client, api_client, make_user):
    user = make_user(email="setme@example.com")
    response = admin_client.post(f"{USERS}{user.pk}/set-password/", {"password": NEW_PASSWORD})
    assert response.status_code == 204
    row = AuditLog.objects.get(action=actions.USER_PASSWORD_SET_BY_ADMIN)
    assert row.new_value == {"password": "changed"}
    login = api_client.post(
        "/api/v1/auth/login/", {"email": "setme@example.com", "password": NEW_PASSWORD}
    )
    assert login.status_code == 200


def test_u6_no_delete_or_put(admin_client, make_user):
    user = make_user()
    assert admin_client.delete(f"{USERS}{user.pk}/").status_code == 405
    assert admin_client.put(f"{USERS}{user.pk}/", {}).status_code == 405
    assert User.objects.filter(pk=user.pk).exists()


def test_list_filters(admin_client, make_user):
    make_user(roles.HR, email="hr.one@example.com", first_name="Priya")
    make_user(roles.EMPLOYEE, email="emp.one@example.com", is_active=False)
    by_role = admin_client.get(USERS, {"role": "HR"}).json()["results"]
    assert [u["email"] for u in by_role] == ["hr.one@example.com"]
    by_search = admin_client.get(USERS, {"search": "priya"}).json()["results"]
    assert [u["email"] for u in by_search] == ["hr.one@example.com"]
    inactive_rows = admin_client.get(USERS, {"is_active": "false"}).json()["results"]
    inactive = [u["email"] for u in inactive_rows]
    # Updated for Phase 5: the reserved, inactive CFS Scheduler user is listed with the
    # inactive users (Admin is not hidden from it); the test's own users are checked without it.
    assert SCHEDULER_EMAIL in inactive
    assert [e for e in inactive if e != SCHEDULER_EMAIL] == ["emp.one@example.com"]
    assert admin_client.get(USERS, {"role": "Nope"}).status_code == 400


def test_roles_endpoint(admin_client):
    body = admin_client.get("/api/v1/roles/").json()
    # Exact grants of the approved role matrix (Phase 1-5 and 9). Admin assigns and edits tasks
    # through tasks.manage_all_tasks (it has no tasks.assign / tasks.edit_all_tasks).
    assert body == [
        {
            "name": "Employee",
            "permissions": [
                "tasks.assign",
                "tasks.create_task",
            ],
        },
        {
            "name": "Operations Manager",
            "permissions": [
                "org.view_team_employees",
                "overdue.review_team_cases",
                "recurring.manage_team_responsibilities",
                "tasks.assign",
                "tasks.create_task",
                "tasks.manage_team_tasks",
                "tasks.view_team_tasks",
            ],
        },
        {
            "name": "HR",
            "permissions": [
                "audit.view_audit_log",
                "org.manage_employees",
                "org.view_all_employees",
                "overdue.review_all_cases",
                "overdue.view_all_cases",
                "performance.configure_kpis",
                "performance.finalize_performance",
                "performance.manage_performance",
                "performance.reopen_performance",
                "recurring.manage_all_responsibilities",
                "recurring.view_all_responsibilities",
                "tasks.assign",
                "tasks.create_task",
                "tasks.delete_task",
                "tasks.edit_all_tasks",
                "tasks.view_all_tasks",
            ],
        },
        {
            "name": "Admin",
            "permissions": [
                "accounts.manage_users",
                "audit.view_audit_log",
                "calendars.manage_company_calendar",
                "org.link_employee_login",
                "org.manage_departments",
                "org.manage_employees",
                "org.view_all_employees",
                "overdue.review_all_cases",
                "overdue.view_all_cases",
                "performance.approve_kpi_config",
                "performance.reopen_performance",
                "recurring.manage_all_responsibilities",
                "recurring.manage_schedules",
                "recurring.view_all_responsibilities",
                "sla.manage_sla_rules",
                "tasks.create_task",
                "tasks.delete_task",
                "tasks.manage_all_tasks",
                "tasks.manage_task_categories",
                "tasks.view_all_tasks",
            ],
        },
    ]
