"""Phase B — specific-date (ONCE) schedules, schedule validation and the four-type constraint.

Seeded calendar: Sundays off; 1st and 3rd Saturdays working. 10 Oct 2026 = 2nd Saturday (off).
"""

from datetime import date, datetime, time

import pytest
import time_machine
from django.db import IntegrityError, transaction

from apps.core.errors import FieldValidationError
from apps.core.timeutils import IST
from apps.org.models import Department
from apps.recurring import generator, services
from apps.recurring.models import RecurringSchedule, Responsibility
from apps.tasks.models import Task, TaskCategory

pytestmark = pytest.mark.django_db


def D(day, month=10):  # noqa: N802
    return date(2026, month, day)


def _at(*parts):
    return time_machine.travel(datetime(*parts, tzinfo=IST), tick=False)


@pytest.fixture
def duty(ops, own):
    r = Responsibility.objects.create(
        code="ONE_OFF", name="Quarterly audit pack", department=Department.objects.get(code="OPS"),
        category=TaskCategory.objects.filter(is_active=True).first(), priority="LOW",
    )
    own(r, ops["rahul_emp"])
    return r


def _once(actor, duty, run_date, policy="SKIP", **extra):
    return services.create_schedule(
        actor=actor, responsibility=duty, title="Audit pack", frequency="ONCE",
        run_time=time(10, 0), run_date=run_date, non_working_day_policy=policy, **extra,
    )


def _fields(error_info):
    return set(error_info.value.fields)


# --- generation ----------------------------------------------------------------------------------


def test_generates_once_at_its_run_time_and_never_again(admin_user, duty, ops):
    with _at(2026, 10, 5, 9, 0):
        schedule = _once(admin_user, duty, D(14))
    for at in ((2026, 10, 14, 9, 59), (2026, 10, 14, 10, 0), (2026, 10, 14, 15, 0),
               (2026, 10, 21, 10, 0)):
        with _at(*at):
            generator.generate_due_occurrences()
    occurrences = list(schedule.occurrences.values_list("occurrence_date", "status"))
    assert occurrences == [(D(14), "GENERATED")]
    task = Task.objects.get(schedule=schedule)
    assert (task.source, task.occurrence_date, task.responsibility_id) == (
        "SCHEDULED", D(14), duty.pk,
    )
    assert task.assigned_to == ops["rahul_emp"] and task.assignments.exists()


def test_generated_late_when_the_scheduler_was_down(admin_user, duty):
    with _at(2026, 10, 5, 9, 0):
        schedule = _once(admin_user, duty, D(14))
    with _at(2026, 10, 20, 8, 0):  # first run after the date
        generator.generate_due_occurrences()
    assert list(schedule.occurrences.values_list("occurrence_date", "status")) == [
        (D(14), "GENERATED"),
    ]


@pytest.mark.parametrize(("policy", "business_date", "window"), [
    ("NEXT_WORKING_DAY", D(12), (D(10), D(12))),
    ("PREVIOUS_WORKING_DAY", D(9), (D(9), D(10))),
])
def test_a_non_working_date_moves_by_its_policy(admin_user, duty, policy, business_date, window):
    with _at(2026, 10, 5, 9, 0):
        schedule = _once(admin_user, duty, D(10), policy)
    assert (schedule.effective_from, schedule.effective_to) == window  # derived by the server
    with _at(2026, 10, 12, 10, 0):
        generator.generate_due_occurrences()
    assert list(schedule.occurrences.values_list("occurrence_date", flat=True)) == [business_date]


# --- validation ----------------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["admin", "manager"])
def test_past_dates_are_rejected_for_every_role(admin_user, ops, duty, who):
    actor = {"admin": admin_user, "manager": ops["manager"]}[who]
    with _at(2026, 10, 5, 9, 0), pytest.raises(FieldValidationError) as error:
        _once(actor, duty, D(2))
    assert _fields(error) == {"run_date"}


def test_today_and_future_dates_are_accepted_and_the_window_is_derived(admin_user, duty):
    with _at(2026, 10, 5, 9, 0):
        today = _once(admin_user, duty, D(5), effective_from=D(1), effective_to=D(30))
        future = _once(admin_user, duty, D(14))
    assert (today.effective_from, today.effective_to) == (D(5), D(5))  # client window ignored
    assert (future.effective_from, future.effective_to, future.weekdays) == (D(14), D(14), [])


def test_skip_on_a_non_working_date_and_a_passed_previous_day_are_rejected(admin_user, duty):
    with _at(2026, 10, 5, 9, 0), pytest.raises(FieldValidationError) as skipped:
        _once(admin_user, duty, D(10))  # 2nd Saturday, SKIP
    assert _fields(skipped) == {"run_date"}
    with _at(2026, 10, 10, 9, 0), pytest.raises(FieldValidationError) as passed:
        _once(admin_user, duty, D(11), "PREVIOUS_WORKING_DAY")  # Sunday -> Friday 9th, gone
    assert _fields(passed) == {"run_date"}


def test_the_date_can_change_only_until_it_has_been_processed(admin_user, duty):
    with _at(2026, 10, 5, 9, 0):
        schedule = _once(admin_user, duty, D(14))
        schedule = services.update_schedule(actor=admin_user, schedule=schedule,
                                            version=schedule.version, run_date=D(16))
    assert (schedule.run_date, schedule.effective_from, schedule.effective_to) == (
        D(16), D(16), D(16),
    )
    with _at(2026, 10, 16, 10, 0):
        generator.generate_due_occurrences()
    with _at(2026, 10, 17, 9, 0):
        with pytest.raises(FieldValidationError) as error:
            services.update_schedule(actor=admin_user, schedule=schedule,
                                     version=schedule.version, run_date=D(23))
        assert _fields(error) == {"run_date"}
        renamed = services.update_schedule(actor=admin_user, schedule=schedule,
                                           version=schedule.version, title="Audit pack (Q3)")
    assert renamed.title == "Audit pack (Q3)" and renamed.run_date == D(16)


@pytest.mark.parametrize(("old", "new", "allowed"), [
    ("DAILY", "MONTHLY", True), ("DAILY", "WEEKLY", False), ("WEEKLY", "DAILY", False),
    ("ONCE", "DAILY", False),
])
def test_frequency_changes(admin_user, duty, old, new, allowed):
    with _at(2026, 10, 5, 9, 0):
        extra = {"DAILY": {}, "WEEKLY": {"weekdays": [0]}, "ONCE": {"run_date": D(14)}}[old]
        schedule = services.create_schedule(
            actor=admin_user, responsibility=duty, title="x", frequency=old, run_time=time(10, 0),
            effective_from=D(5), **extra,
        )
        change = {"frequency": new, "day_of_month": 20 if new == "MONTHLY" else None}
        if allowed:
            assert services.update_schedule(actor=admin_user, schedule=schedule,
                                            version=schedule.version, **change).frequency == new
        else:
            with pytest.raises(FieldValidationError) as error:
                services.update_schedule(actor=admin_user, schedule=schedule,
                                         version=schedule.version, **change)
            assert _fields(error) == {"frequency"}


@pytest.mark.parametrize(("frequency", "extra", "field"), [
    ("WEEKLY", {"weekdays": []}, "weekdays"),
    ("WEEKLY", {"weekdays": [7]}, "weekdays"),
    ("WEEKLY", {"weekdays": [1, 1]}, "weekdays"),
    ("WEEKLY", {"weekdays": [1], "day_of_month": 3}, "day_of_month"),
    ("DAILY", {"weekdays": [1]}, "weekdays"),
    ("MONTHLY", {"day_of_month": 20, "run_date": date(2026, 10, 20)}, "run_date"),
    ("ONCE", {}, "run_date"),
])
def test_each_frequency_accepts_only_its_own_fields(admin_user, duty, frequency, extra, field):
    with _at(2026, 10, 5, 9, 0), pytest.raises(FieldValidationError) as error:
        services.create_schedule(actor=admin_user, responsibility=duty, title="x",
                                 frequency=frequency, run_time=time(10, 0), effective_from=D(5),
                                 **extra)
    assert _fields(error) == {field}


def test_weekdays_are_stored_sorted(admin_user, duty):
    with _at(2026, 10, 5, 9, 0):
        schedule = services.create_schedule(
            actor=admin_user, responsibility=duty, title="x", frequency="WEEKLY",
            run_time=time(10, 0), effective_from=D(5), weekdays=[3, 0],
        )
    assert RecurringSchedule.objects.get(pk=schedule.pk).weekdays == [0, 3]


@pytest.mark.parametrize("fields", [
    {"frequency": "WEEKLY", "weekdays": []},
    {"frequency": "WEEKLY", "weekdays": [7]},
    {"frequency": "DAILY", "run_date": date(2026, 10, 5)},
    {"frequency": "ONCE", "run_date": date(2026, 10, 9), "effective_to": date(2026, 10, 8)},
])
def test_the_database_constraint_rejects_malformed_rows(duty, fields):
    values = {"responsibility": duty, "title": "x", "run_time": time(10, 0),
              "effective_from": date(2026, 10, 5), **fields}
    with pytest.raises(IntegrityError), transaction.atomic():
        RecurringSchedule.objects.create(**values)
