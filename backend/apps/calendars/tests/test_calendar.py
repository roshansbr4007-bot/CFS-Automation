"""Company Calendar rules (approved v4 / Phase 5) and its Admin API."""

from datetime import date, datetime

import pytest
import time_machine

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.calendars import services
from apps.calendars.models import BusinessCalendar, CalendarDay
from apps.core.timeutils import IST
from apps.org.models import EmployeeDailyLogin
from apps.org.tests.factories import EmployeeFactory

pytestmark = pytest.mark.django_db
URL = "/api/v1/calendars/company/"


def _day(day, kind, name="x"):
    return CalendarDay.objects.create(
        calendar=services.company_calendar(), date=day, kind=kind, name=name
    )


@pytest.mark.parametrize(
    ("day", "working"),
    [
        (date(2026, 10, 5), True),  # Monday
        (date(2026, 10, 9), True),  # Friday
        (date(2026, 10, 4), False),  # Sunday
        (date(2026, 10, 3), True),  # 1st Saturday
        (date(2026, 10, 10), False),  # 2nd Saturday
        (date(2026, 10, 17), True),  # 3rd Saturday
        (date(2026, 10, 24), False),  # 4th Saturday
        (date(2026, 10, 31), False),  # 5th Saturday
    ],
)
def test_weekly_pattern(day, working):
    assert services.is_working_day(day) is working


def test_holiday_and_special_working_day_override_the_pattern():
    _day(date(2026, 10, 5), "HOLIDAY", "Festival")
    _day(date(2026, 10, 4), "SPECIAL_WORKING_DAY", "Year-end closing")
    assert services.is_working_day(date(2026, 10, 5)) is False
    assert services.is_working_day(date(2026, 10, 4)) is True


def test_next_and_previous_working_day():
    assert services.next_working_day(date(2026, 10, 10)) == date(2026, 10, 12)  # 2nd Sat -> Mon
    assert services.previous_working_day(date(2026, 10, 11)) == date(2026, 10, 9)  # -> Fri
    _day(date(2026, 10, 12), "HOLIDAY")
    assert services.next_working_day(date(2026, 10, 10)) == date(2026, 10, 13)


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ("NEXT_WORKING_DAY", date(2026, 9, 21)),  # Sunday 20 Sep -> Monday 21 Sep (R24)
        ("PREVIOUS_WORKING_DAY", date(2026, 9, 19)),  # 3rd Saturday is a working day
        ("SKIP", None),
    ],
)
def test_resolve_scheduled_date_on_a_non_working_day(policy, expected):
    assert services.resolve_scheduled_date(date(2026, 9, 20), policy) == expected


def test_resolve_keeps_a_working_day_including_a_3rd_saturday():
    third_saturday = date(2026, 6, 20)
    assert services.resolve_scheduled_date(third_saturday, "NEXT_WORKING_DAY") == third_saturday


def test_search_guard_against_a_misconfigured_calendar():
    BusinessCalendar.objects.filter(code="COMPANY").update(weekly_off_weekdays=list(range(7)))
    with pytest.raises(services.CalendarError):
        services.next_working_day(date(2026, 10, 5))


def test_occurrence_in_month():
    assert [services.occurrence_in_month(date(2026, 10, d)) for d in (3, 10, 17, 24, 31)] == [
        1, 2, 3, 4, 5,
    ]


# --- valid login (approved R20, decided with the calendar) ------------------------------------


def test_login_on_a_non_working_day_is_kept_but_not_valid(api_client, make_user, password):
    user = make_user()
    EmployeeFactory(user=user, email=user.email)
    sunday = datetime(2026, 10, 4, 10, 0, tzinfo=IST)
    monday = datetime(2026, 10, 5, 10, 0, tzinfo=IST)
    for moment in (sunday, monday):
        with time_machine.travel(moment, tick=False):
            api_client.post("/api/v1/auth/login/", {"email": user.email, "password": password})
    facts = {f.work_date.isoformat(): f.is_valid for f in EmployeeDailyLogin.objects.all()}
    assert facts == {"2026-10-04": False, "2026-10-05": True}  # Sunday kept, not valid


# --- API --------------------------------------------------------------------------------------


def test_everyone_reads_the_calendar(client_for, make_user):
    body = client_for(make_user(roles.EMPLOYEE)).get(URL).json()
    assert body["weekly_off_weekdays"] == [6] and body["saturday_working_occurrences"] == [1, 3]
    assert body["days"] == []


def test_admin_adds_and_removes_days_with_audit(admin_client):
    created = admin_client.post(
        f"{URL}days/", {"date": "2026-10-05", "kind": "HOLIDAY", "name": "Festival"}
    )
    assert created.status_code == 201
    duplicate = admin_client.post(
        f"{URL}days/", {"date": "2026-10-05", "kind": "HOLIDAY", "name": "Again"}
    )
    assert duplicate.status_code == 409
    blank_name = {"date": "2026-10-06", "kind": "HOLIDAY", "name": " "}
    blank = admin_client.post(f"{URL}days/", blank_name)
    assert blank.status_code == 400
    assert admin_client.delete(f"{URL}days/{created.json()['id']}/").status_code == 204
    assert not CalendarDay.objects.exists()
    rows = AuditLog.objects.filter(entity_type="calendar_day")
    actions = sorted(rows.values_list("action", flat=True))
    assert actions == ["calendar.day_added", "calendar.day_removed"]


@pytest.mark.parametrize("role", [roles.EMPLOYEE, roles.OPERATIONS_MANAGER, roles.HR])
def test_only_admin_changes_the_calendar(client_for, make_user, role):
    client = client_for(make_user(role))
    body = {"date": "2026-10-05", "kind": "HOLIDAY", "name": "x"}
    assert client.post(f"{URL}days/", body).status_code == 403
    entry = _day(date(2026, 10, 6), "HOLIDAY")
    assert client.delete(f"{URL}days/{entry.pk}/").status_code == 403


def test_working_days_endpoint(client_for, make_user):
    client = client_for(make_user())
    body = client.get(f"{URL}working-days/", {"from": "2026-10-03", "to": "2026-10-11"}).json()
    assert body["working_days"] == [
        "2026-10-03", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09",
    ]
    assert client.get(f"{URL}working-days/", {"from": "2026-10-03"}).status_code == 400
    assert client.get(f"{URL}working-days/", {"from": "x", "to": "2026-10-03"}).status_code == 400
    too_long = client.get(f"{URL}working-days/", {"from": "2026-01-01", "to": "2027-06-01"})
    assert too_long.status_code == 400
    assert str(services.company_calendar()) == "Company Calendar"
    assert str(_day(date(2026, 10, 7), "HOLIDAY")) == "2026-10-07 HOLIDAY"
