"""First successful login per employee per IST day (approved Q6, spec s.5 and s.8.1)."""

from datetime import UTC, datetime

import pytest
import time_machine
from django.db import IntegrityError

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.org.models import EmployeeDailyLogin

LOGIN = "/api/v1/auth/login/"
pytestmark = pytest.mark.django_db


def _login(api_client, user, password):
    return api_client.post(LOGIN, {"email": user.email, "password": password})


def test_first_login_of_the_ist_day_is_recorded_once(api_client, person, password):
    user, employee = person(roles.EMPLOYEE)
    with time_machine.travel(datetime(2026, 10, 5, 4, 0, tzinfo=UTC), tick=False):
        assert _login(api_client, user, password).status_code == 200
    with time_machine.travel(datetime(2026, 10, 5, 9, 0, tzinfo=UTC), tick=False):
        assert _login(api_client, user, password).status_code == 200
    facts = list(EmployeeDailyLogin.objects.filter(employee=employee))
    assert len(facts) == 1
    assert facts[0].work_date.isoformat() == "2026-10-05"
    assert facts[0].first_login_at == datetime(2026, 10, 5, 4, 0, tzinfo=UTC)
    row = AuditLog.objects.get(action="employee.daily_login_recorded")
    assert row.actor_user == user and row.entity_id == str(employee.pk)
    assert row.new_value == {
        "work_date": "2026-10-05",
        "first_login_at": "2026-10-05T04:00:00+00:00",
        "is_valid": True,  # Phase 5: 5 Oct 2026 is a Monday, a Company Calendar working day
    }
    assert row.context["department_id"] == employee.department_id


def test_ist_midnight_splits_days(api_client, person, password):
    user, employee = person()
    # 23:50 IST on 5 Oct = 18:20 UTC; 00:10 IST on 6 Oct = 18:40 UTC
    with time_machine.travel(datetime(2026, 10, 5, 18, 20, tzinfo=UTC), tick=False):
        _login(api_client, user, password)
    with time_machine.travel(datetime(2026, 10, 5, 18, 40, tzinfo=UTC), tick=False):
        _login(api_client, user, password)
    days = sorted(f.work_date.isoformat() for f in employee.daily_logins.all())
    assert days == ["2026-10-05", "2026-10-06"]


def test_no_fact_without_employee_record_or_for_inactive_employee(
    api_client, make_user, person, password
):
    plain = make_user()
    inactive_user, _ = person(is_active=False)
    assert _login(api_client, plain, password).status_code == 200
    assert _login(api_client, inactive_user, password).status_code == 200
    assert not EmployeeDailyLogin.objects.exists()
    assert not AuditLog.objects.filter(action="employee.daily_login_recorded").exists()


def test_failed_login_records_nothing(api_client, person):
    user, _ = person()
    assert api_client.post(LOGIN, {"email": user.email, "password": "wrong"}).status_code == 400
    assert not EmployeeDailyLogin.objects.exists()


def test_race_for_the_same_day_never_breaks_login(api_client, person, password, monkeypatch):
    user, _ = person()

    def lost_race(**kwargs):
        raise IntegrityError("another login recorded this day first")

    monkeypatch.setattr(EmployeeDailyLogin.objects, "get_or_create", lost_race)
    assert _login(api_client, user, password).status_code == 200
    assert not AuditLog.objects.filter(action="employee.daily_login_recorded").exists()


def test_my_login_history_with_date_filters(client_for, person):
    user, employee = person()
    for day in ("2020-01-01", "2020-01-02", "2020-01-03"):
        EmployeeDailyLogin.objects.create(
            employee=employee, work_date=day, first_login_at=f"{day}T04:00:00Z"
        )
    client = client_for(user)  # force_login records today's fact as well; the filter excludes it
    params = {"from": "2020-01-02", "to": "2020-01-03"}
    body = client.get("/api/v1/employees/me/logins/", params).json()
    assert [f["work_date"] for f in body["results"]] == ["2020-01-03", "2020-01-02"]


@pytest.mark.parametrize("value", ["yesterday", "2026-13-01"])
def test_invalid_login_date_filters(client_for, person, value):
    user, _ = person()
    response = client_for(user).get("/api/v1/employees/me/logins/", {"from": value})
    assert response.status_code == 400 and "from" in response.json()["fields"]


def test_my_login_history_without_employee_record(client_for, make_user):
    response = client_for(make_user()).get("/api/v1/employees/me/logins/")
    assert response.status_code == 404 and response.json()["code"] == "no_employee_record"
