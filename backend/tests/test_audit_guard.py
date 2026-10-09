"""L4: every Phase 1 write through the API leaves exactly one audit row per change."""

import pytest

from apps.accounts import roles
from apps.audit import actions
from apps.audit.models import AuditLog

pytestmark = pytest.mark.django_db
STRONG = "Ledger-Guard-Test-2026"


def _new_rows(before_id):
    return list(AuditLog.objects.filter(id__gt=before_id).order_by("id"))


def _last_id():
    last = AuditLog.objects.order_by("-id").first()
    return last.id if last else 0


def _expect(before, *expected_actions, actor=None):
    rows = _new_rows(before)
    assert [r.action for r in rows] == list(expected_actions)
    for r in rows:
        assert r.request_id is not None and r.ip == "127.0.0.1"
        if actor is not None:
            assert r.actor_user_id == actor.pk


def test_l4_each_write_audited_once(api_client, admin_client, admin_user, make_user, password):
    before = _last_id()
    api_client.post("/api/v1/auth/login/", {"email": "admin@example.com", "password": password})
    _expect(before, actions.AUTH_LOGIN, actor=admin_user)

    before = _last_id()
    api_client.post("/api/v1/auth/login/", {"email": "admin@example.com", "password": "bad"})
    _expect(before, actions.AUTH_LOGIN_FAILED)

    before = _last_id()
    created = admin_client.post(
        "/api/v1/users/", {"email": "g@example.com", "password": STRONG, "roles": [roles.EMPLOYEE]}
    ).json()
    _expect(before, actions.USER_CREATED, actor=admin_user)

    url = f"/api/v1/users/{created['id']}/"
    before = _last_id()
    admin_client.patch(url, {"first_name": "Gita", "roles": [roles.HR], "is_active": False})
    _expect(before, actions.USER_UPDATED, actions.USER_ROLE_CHANGED, actions.USER_DEACTIVATED,
            actor=admin_user)

    before = _last_id()
    admin_client.patch(url, {"is_active": True})
    _expect(before, actions.USER_ACTIVATED, actor=admin_user)

    before = _last_id()
    admin_client.post(f"{url}set-password/", {"password": STRONG + "x"})
    _expect(before, actions.USER_PASSWORD_SET_BY_ADMIN, actor=admin_user)

    before = _last_id()
    admin_client.post(
        "/api/v1/auth/password-change/",
        {"current_password": password, "new_password": STRONG + "y"},
    )
    _expect(before, actions.USER_PASSWORD_CHANGED, actor=admin_user)

    before = _last_id()
    admin_client.post("/api/v1/auth/logout/")
    _expect(before, actions.AUTH_LOGOUT, actor=admin_user)


def test_reads_write_no_audit(admin_client):
    before = _last_id()
    admin_client.get("/api/v1/users/")
    admin_client.get("/api/v1/roles/")
    admin_client.get("/api/v1/audit-log/")
    admin_client.get("/api/v1/auth/me/")
    assert _new_rows(before) == []
