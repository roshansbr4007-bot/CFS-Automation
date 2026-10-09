import pytest

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.audit.services import record

AUDIT = "/api/v1/audit-log/"
pytestmark = pytest.mark.django_db


@pytest.fixture
def hr_client(client_for, make_user):
    return client_for(make_user(roles.HR))


def test_l5_hr_reads_newest_first_with_filters(hr_client, make_user):
    target = make_user()
    record(action="user.updated", entity_type="user", entity_id=target.pk, use_request_user=False)
    record(action="user.created", entity_type="user", entity_id=999, use_request_user=False)

    rows = hr_client.get(AUDIT).json()["results"]
    assert [r["action"] for r in rows] == ["user.created", "user.updated"]

    filtered = hr_client.get(AUDIT, {"entity_type": "user", "entity_id": target.pk}).json()
    assert [r["action"] for r in filtered["results"]] == ["user.updated"]
    assert hr_client.get(AUDIT, {"action": "user.created"}).json()["count"] == 1


def test_l5_date_filters_use_ist_days(hr_client):
    record(action="x", entity_type="t", entity_id=1, use_request_user=False)
    assert hr_client.get(AUDIT, {"from": "2000-01-01"}).json()["count"] == 1
    assert hr_client.get(AUDIT, {"to": "2000-01-01"}).json()["count"] == 0
    bad = hr_client.get(AUDIT, {"from": "yesterday"})
    assert bad.status_code == 400 and "from" in bad.json()["fields"]
    impossible = hr_client.get(AUDIT, {"to": "2026-13-01"})
    assert impossible.status_code == 400 and "to" in impossible.json()["fields"]


def test_l5_no_write_methods(hr_client):
    for method in ("post", "put", "patch", "delete"):
        assert getattr(hr_client, method)(AUDIT, {}).status_code == 405
    assert AuditLog.objects.count() == 0


def test_audit_row_carries_request_context(admin_client):
    admin_client.post(
        "/api/v1/users/",
        {"email": "ctx@example.com", "password": "Ledger-Context-2026", "roles": []},
        HTTP_USER_AGENT="pytest-agent",
    )
    row = AuditLog.objects.get(action="user.created")
    assert row.request_id is not None
    assert row.ip == "127.0.0.1"
    assert row.context["user_agent"] == "pytest-agent"
    assert row.context["actor_email"] == "admin@example.com"
