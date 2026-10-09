import pytest
from rest_framework.test import APIClient

from apps.accounts import roles
from apps.audit import actions
from apps.audit.models import AuditLog

LOGIN = "/api/v1/auth/login/"
pytestmark = pytest.mark.django_db


def test_a1_login_success_with_any_letter_case(api_client, make_user, password):
    user = make_user(roles.HR, email="meera@example.com")
    response = api_client.post(LOGIN, {"email": "Meera@Example.COM", "password": password})
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "meera@example.com"
    assert body["roles"] == ["HR"]
    assert body["permissions"] == [
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
    ]
    row = AuditLog.objects.get(action=actions.AUTH_LOGIN)
    assert row.actor_user_id == user.pk and row.entity_id == str(user.pk)
    assert row.request_id is not None


def test_a2_same_message_for_wrong_password_and_unknown_email(api_client, make_user):
    make_user(email="known@example.com")
    wrong = api_client.post(LOGIN, {"email": "known@example.com", "password": "nope"})
    unknown = api_client.post(LOGIN, {"email": "nobody@example.com", "password": "nope"})
    assert wrong.status_code == unknown.status_code == 400
    assert wrong.json() == unknown.json()
    assert wrong.json()["code"] == "invalid_credentials"
    rows = AuditLog.objects.filter(action=actions.AUTH_LOGIN_FAILED).order_by("id")
    attempted = [r.context["attempted_email"] for r in rows]
    assert attempted == ["known@example.com", "nobody@example.com"]
    assert rows[1].actor_user_id is None and rows[1].entity_id == "unknown"


def test_a3_deactivated_user_cannot_log_in(api_client, make_user, password):
    make_user(email="gone@example.com", is_active=False)
    response = api_client.post(LOGIN, {"email": "gone@example.com", "password": password})
    assert response.status_code == 400
    assert AuditLog.objects.filter(action=actions.AUTH_LOGIN_FAILED).count() == 1


def test_a4_csrf_required_on_login(make_user, password):
    make_user(email="csrf@example.com")
    client = APIClient(enforce_csrf_checks=True)
    refused = client.post(LOGIN, {"email": "csrf@example.com", "password": password})
    assert refused.status_code == 403
    assert refused.json()["code"] == "csrf_failed"

    client.get("/api/v1/auth/csrf/")
    token = client.cookies["csrftoken"].value
    accepted = client.post(
        LOGIN, {"email": "csrf@example.com", "password": password}, HTTP_X_CSRFTOKEN=token
    )
    assert accepted.status_code == 200


def test_a4_csrf_required_for_logged_in_writes(make_user):
    user = make_user()
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(user)
    response = client.post("/api/v1/auth/logout/")
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


def test_a5_logout(client_for, make_user):
    user = make_user()
    client = client_for(user)
    assert client.post("/api/v1/auth/logout/").status_code == 204
    assert client.get("/api/v1/auth/me/").status_code == 401
    assert AuditLog.objects.filter(action=actions.AUTH_LOGOUT, actor_user=user).count() == 1


def test_a6_password_change_validation(client_for, make_user):
    client = client_for(make_user())
    wrong = client.post(
        "/api/v1/auth/password-change/",
        {"current_password": "wrong", "new_password": "Another-Strong-Pass-99"},
    )
    assert wrong.status_code == 400 and "current_password" in wrong.json()["fields"]
    weak = client.post(
        "/api/v1/auth/password-change/",
        {"current_password": "Ledger-Sandstone-2026", "new_password": "123"},
    )
    assert weak.status_code == 400 and "new_password" in weak.json()["fields"]
    assert not AuditLog.objects.filter(action=actions.USER_PASSWORD_CHANGED).exists()


def test_a7_password_change_success(client_for, make_user, password):
    user = make_user()
    client = client_for(user)
    response = client.post(
        "/api/v1/auth/password-change/",
        {"current_password": password, "new_password": "Another-Strong-Pass-99"},
    )
    assert response.status_code == 204
    user.refresh_from_db()
    assert user.check_password("Another-Strong-Pass-99")
    assert client.get("/api/v1/auth/me/").status_code == 200  # session kept
    row = AuditLog.objects.get(action=actions.USER_PASSWORD_CHANGED)
    assert row.new_value == {"password": "changed"}
    assert user.password not in str(row.new_value) + str(row.context)


def test_me_returns_roles_and_permissions(client_for, make_user):
    user = make_user(roles.ADMIN, roles.HR)
    body = client_for(user).get("/api/v1/auth/me/").json()
    assert body["roles"] == ["Admin", "HR"]
    assert body["permissions"] == [
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
        "performance.configure_kpis",
        "performance.finalize_performance",
        "performance.manage_performance",
        "performance.reopen_performance",
        "recurring.manage_all_responsibilities",
        "recurring.manage_schedules",
        "recurring.view_all_responsibilities",
        "sla.manage_sla_rules",
        "tasks.assign",
        "tasks.create_task",
        "tasks.delete_task",
        "tasks.edit_all_tasks",
        "tasks.manage_all_tasks",
        "tasks.manage_task_categories",
        "tasks.view_all_tasks",
    ]
