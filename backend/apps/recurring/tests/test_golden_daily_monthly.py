"""Phase B golden regression: the EXISTING DAILY and MONTHLY due_occurrences() behaviour, pinned
before WEEKLY / ONCE were added. Any drift in these two branches fails here.

Seeded company calendar: Sundays off; only the 1st and 3rd Saturdays are working days.
Dates used (2026): 3 Oct = 1st Saturday (working), 10 Oct = 2nd Saturday (off), 4/11 Oct and
20 Sep = Sundays (off); 20 Sep -> next working day 21 Sep, previous working day 19 Sep.
"""

from datetime import date, datetime, time

import pytest

from apps.core.timeutils import IST
from apps.org.models import Department
from apps.recurring.generator import due_occurrences
from apps.recurring.models import RecurringSchedule, Responsibility, ScheduleOccurrence
from apps.tasks.models import TaskCategory

pytestmark = pytest.mark.django_db


def _at(*parts):
    return datetime(*parts, tzinfo=IST)


@pytest.fixture
def make_schedule():
    responsibility = Responsibility.objects.create(
        code="GOLDEN", name="Golden", department=Department.objects.get(code="OPS"),
        category=TaskCategory.objects.filter(is_active=True).first(),
    )

    def _make(frequency, *, day_of_month=None, policy="SKIP", start=date(2026, 10, 1), end=None):
        return RecurringSchedule.objects.create(
            responsibility=responsibility, title="Golden", frequency=frequency,
            run_time=time(10, 0), day_of_month=day_of_month, non_working_day_policy=policy,
            effective_from=start, effective_to=end,
        )

    return _make


def D(day, month=10, year=2026):  # noqa: N802  (short date helper for readable expectations)
    return date(year, month, day)


# --- DAILY ---------------------------------------------------------------------------------------


def test_daily_working_days_and_missed_days(make_schedule):
    schedule = make_schedule("DAILY")
    # Before today's run time: earlier working days are MISSED (False); today is not due yet.
    assert due_occurrences(schedule, _at(2026, 10, 12, 9, 0)) == [
        (D(1), False), (D(2), False), (D(3), False), (D(5), False),
        (D(6), False), (D(7), False), (D(8), False), (D(9), False),
    ]
    # At the run time, today is generated (True); Sundays and the 2nd Saturday never appear.
    assert due_occurrences(schedule, _at(2026, 10, 12, 10, 0))[-1] == (D(12), True)


def test_daily_skips_dates_already_in_the_ledger(make_schedule):
    schedule = make_schedule("DAILY")
    ScheduleOccurrence.objects.create(schedule=schedule, occurrence_date=D(5), status="MISSED")
    due = [day for day, _ in due_occurrences(schedule, _at(2026, 10, 12, 9, 0))]
    assert D(5) not in due and due == [D(1), D(2), D(3), D(6), D(7), D(8), D(9)]


def test_daily_look_back_is_31_days(make_schedule):
    schedule = make_schedule("DAILY", start=D(1, 8))
    assert due_occurrences(schedule, _at(2026, 10, 12, 9, 0))[0] == (D(11, 9), False)


def test_daily_effective_window_is_inclusive(make_schedule):
    schedule = make_schedule("DAILY", start=D(6), end=D(8))
    assert due_occurrences(schedule, _at(2026, 10, 12, 10, 0)) == [
        (D(6), False), (D(7), False), (D(8), False),
    ]


# --- MONTHLY -------------------------------------------------------------------------------------


def test_monthly_normal_generation_waits_for_the_run_time(make_schedule):
    schedule = make_schedule("MONTHLY", day_of_month=20, start=D(1))
    assert due_occurrences(schedule, _at(2026, 10, 20, 9, 59)) == []
    assert due_occurrences(schedule, _at(2026, 10, 20, 10, 0)) == [(D(20), True)]


def test_monthly_catch_up_and_skip(make_schedule):
    schedule = make_schedule("MONTHLY", day_of_month=20, start=D(1, 8))
    # Aug 20 (missed) is still generated; Sep 20 is a Sunday and SKIP drops it.
    assert due_occurrences(schedule, _at(2026, 10, 20, 10, 0)) == [
        (D(20, 8), True), (D(20), True),
    ]


@pytest.mark.parametrize(("policy", "september"), [
    ("NEXT_WORKING_DAY", D(21, 9)),
    ("PREVIOUS_WORKING_DAY", D(19, 9)),
])
def test_monthly_non_working_day_policies(make_schedule, policy, september):
    schedule = make_schedule("MONTHLY", day_of_month=20, policy=policy, start=D(1, 8))
    assert due_occurrences(schedule, _at(2026, 10, 20, 10, 0)) == [
        (D(20, 8), True), (september, True), (D(20), True),
    ]


def test_monthly_effective_window_uses_the_nominal_date(make_schedule):
    # Starting the day after Sep 20: September's nominal date is out, though it would move to 21st.
    late_start = make_schedule(
        "MONTHLY", day_of_month=20, policy="NEXT_WORKING_DAY", start=D(21, 9)
    )
    assert due_occurrences(late_start, _at(2026, 10, 20, 10, 0)) == [(D(20), True)]
    # Start and end exactly on nominal dates are inclusive.
    window = make_schedule("MONTHLY", day_of_month=20, policy="NEXT_WORKING_DAY", start=D(20, 9),
                           end=D(20, 9))
    assert due_occurrences(window, _at(2026, 10, 20, 10, 0)) == [(D(21, 9), True)]


def test_monthly_look_back_is_12_months(make_schedule):
    schedule = make_schedule("MONTHLY", day_of_month=20, start=D(1, 1, 2025))
    due = due_occurrences(schedule, _at(2026, 10, 20, 10, 0))
    # Oct 2025 .. Oct 2026 = 13 months, minus Sep 2026 (the 20th is a Sunday, SKIP) = 12. A 20th is
    # always the 3rd weekday of its month, so a Saturday 20th (Dec 2025, Jun 2026) is working.
    assert due[0] == (D(20, 10, 2025), True) and len(due) == 12
    assert (D(20, 12, 2025), True) in due and (D(20, 6), True) in due
