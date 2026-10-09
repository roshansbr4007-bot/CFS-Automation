"""Error-handler branches and client-IP detection not covered by other tests."""

import pytest
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied

from apps.audit.models import AuditLog
from apps.core.exceptions import exception_handler

SHAPE = {"code", "message", "fields"}


def test_django_permission_denied_becomes_403_in_the_api_shape():
    response = exception_handler(DjangoPermissionDenied(), {})
    assert response.status_code == 403
    assert response.data == {
        "code": "permission_denied",
        "message": "You do not have permission to do this.",
        "fields": {},
    }


def test_unexpected_errors_are_left_to_django():
    # Returning None lets Django answer 500 and log the traceback instead of hiding it.
    assert exception_handler(RuntimeError("boom"), {}) is None


@pytest.mark.django_db
def test_malformed_json_keeps_the_error_shape(api_client):
    response = api_client.post(
        "/api/v1/auth/login/", data="{not json", content_type="application/json"
    )
    assert response.status_code == 400
    assert set(response.json()) == SHAPE
    assert response.json()["code"] == "parse_error"


@pytest.mark.django_db
def test_audit_ip_comes_from_first_x_forwarded_for_address(admin_client):
    admin_client.post(
        "/api/v1/users/",
        {"email": "proxied@example.com", "password": "Ledger-Proxy-2026", "roles": []},
        HTTP_X_FORWARDED_FOR="203.0.113.7, 10.0.0.1",
    )
    assert AuditLog.objects.get(action="user.created").ip == "203.0.113.7"
