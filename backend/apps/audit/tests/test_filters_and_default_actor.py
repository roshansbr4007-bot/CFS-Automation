"""Audit-log filters not covered elsewhere, and record()'s default actor."""

from datetime import date, time
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.audit.services import record
from apps.core.request_context import RequestContext, reset_request_context, set_request_context
from apps.core.timeutils import ist_datetime

AUDIT = "/api/v1/audit-log/"
pytestmark = pytest.mark.django_db


@pytest.fixture
def hr_client(client_for, make_user):
    return client_for(make_user(roles.HR))


def _row(action, when, actor=None):
    return AuditLog.objects.create(
        action=action, entity_type="t", entity_id="1", occurred_at=when, actor_user=actor
    )


def _actions(response):
    assert response.status_code == 200, response.json()
    return [r["action"] for r in response.json()["results"]]


def test_actor_filter_returns_only_that_users_rows(hr_client, make_user):
    alice, bob = make_user(email="alice@example.com"), make_user(email="bob@example.com")
    record(action="by.alice", entity_type="t", entity_id=1, actor=alice)
    record(action="by.bob", entity_type="t", entity_id=1, actor=bob)
    assert _actions(hr_client.get(AUDIT, {"actor": alice.pk})) == ["by.alice"]


def test_actor_filter_must_be_a_user_id(hr_client):
    response = hr_client.get(AUDIT, {"actor": "alice"})
    assert response.status_code == 400
    assert response.json()["fields"] == {"actor": ["Must be a user id."]}


def test_datetime_bounds_with_offset_are_used_exactly(hr_client):
    _row("morning", ist_datetime(date(2026, 10, 5), time(10, 0)))
    _row("evening", ist_datetime(date(2026, 10, 5), time(18, 0)))
    after_noon = hr_client.get(AUDIT, {"from": "2026-10-05T12:00:00+05:30"})
    assert _actions(after_noon) == ["evening"]
    before_noon = hr_client.get(AUDIT, {"to": "2026-10-05T12:00:00+05:30"})
    assert _actions(before_noon) == ["morning"]


def test_date_bounds_cover_the_whole_ist_day(hr_client):
    # 23:30 IST on 5 Oct is 18:00 UTC: it belongs to 5 Oct in IST.
    _row("late_on_5th", ist_datetime(date(2026, 10, 5), time(23, 30)))
    _row("early_on_6th", ist_datetime(date(2026, 10, 6), time(0, 30)))
    assert _actions(hr_client.get(AUDIT, {"from": "2026-10-05", "to": "2026-10-05"})) == [
        "late_on_5th"
    ]
    assert _actions(hr_client.get(AUDIT, {"from": "2026-10-06"})) == ["early_on_6th"]


def test_datetime_without_offset_is_rejected(hr_client):
    response = hr_client.get(AUDIT, {"from": "2026-10-05T10:00:00"})
    assert response.status_code == 400
    assert response.json()["fields"] == {"from": ["Include a timezone offset."]}


def _with_request_user(user):
    return set_request_context(RequestContext(request=SimpleNamespace(user=user)))


def test_record_defaults_to_the_signed_in_request_user(make_user):
    user = make_user(email="signed.in@example.com")
    token = _with_request_user(user)
    try:
        row = record(action="t.default_actor", entity_type="t", entity_id=1)
    finally:
        reset_request_context(token)
    assert row.actor_user == user
    assert row.context["actor_email"] == "signed.in@example.com"


def test_record_has_no_actor_for_anonymous_requests():
    token = _with_request_user(AnonymousUser())
    try:
        row = record(action="t.anonymous", entity_type="t", entity_id=1)
    finally:
        reset_request_context(token)
    assert row.actor_user is None
    assert "actor_email" not in row.context


def test_record_ignores_request_user_for_system_actions(make_user):
    token = _with_request_user(make_user())
    try:
        row = record(action="t.system", entity_type="t", entity_id=1, use_request_user=False)
    finally:
        reset_request_context(token)
    assert row.actor_user is None
