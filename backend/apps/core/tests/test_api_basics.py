from datetime import date, time

import pytest
from django.db import DatabaseError, connection

from apps.accounts import roles
from apps.accounts.tests.factories import UserFactory
from apps.core.timeutils import ist_datetime, to_ist

pytestmark = pytest.mark.django_db
SHAPE = {"code", "message", "fields"}


def test_h1_health_ok(api_client):
    response = api_client.get("/api/v1/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
    assert response["X-Request-ID"]


def test_h1_health_database_down(api_client, monkeypatch):
    def broken_cursor(*args, **kwargs):
        raise DatabaseError("down")

    monkeypatch.setattr(connection, "cursor", broken_cursor)
    response = api_client.get("/api/v1/health/")
    assert response.status_code == 503
    assert response.json()["database"] == "error"


def test_e1_error_shape(api_client, client_for, make_user, admin_client):
    cases = [
        (api_client.post("/api/v1/auth/login/", {}), 400, "validation_error"),
        (api_client.get("/api/v1/auth/me/"), 401, "not_authenticated"),
        (client_for(make_user(roles.EMPLOYEE)).get("/api/v1/users/"), 403, "permission_denied"),
        (admin_client.get("/api/v1/users/999999/"), 404, "not_found"),
        (admin_client.delete("/api/v1/users/999999/"), 405, "method_not_allowed"),
    ]
    for response, status_code, code in cases:
        assert response.status_code == status_code
        assert set(response.json()) == SHAPE
        assert response.json()["code"] == code


def test_t1_stored_utc_shown_ist():
    user = UserFactory(date_joined=ist_datetime(date(2026, 10, 5), time(23, 59)))
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT to_char(date_joined AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') "
            "FROM accounts_user WHERE id = %s",
            [user.pk],
        )
        assert cursor.fetchone()[0] == "2026-10-05 18:29"
    user.refresh_from_db()
    assert to_ist(user.date_joined).strftime("%H:%M") == "23:59"


def test_t2_api_timestamps_have_ist_offset(client_for, make_user):
    body = client_for(make_user()).get("/api/v1/auth/me/").json()
    assert body["date_joined"].endswith("+05:30")
