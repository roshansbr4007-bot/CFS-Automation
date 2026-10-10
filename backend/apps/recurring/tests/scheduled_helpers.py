"""Shared helpers for the scheduled-generation SLA tests (scheduling fix S1-S7, C1/C2).

Plain functions, not fixtures: each test builds exactly the responsibility, schedule and task
type it needs, then drives the REAL generator and SLA checker at fixed IST times.
"""

from datetime import date, datetime, time

import time_machine

from apps.core.timeutils import IST
from apps.org.models import Department
from apps.recurring import generator
from apps.recurring.models import RecurringSchedule, Responsibility, ResponsibilityOwner
from apps.sla import services as sla_services
from apps.sla.models import TaskSla
from apps.tasks.models import Task, TaskCategory, TaskTemplate

_counter = iter(range(1, 10_000))


def at(*parts):
    return time_machine.travel(datetime(*parts, tzinfo=IST), tick=False)


def ist(*parts):
    return datetime(*parts, tzinfo=IST)


def ops_department():
    return Department.objects.get(code="OPS")


def task_type(trigger, *, rule="FEED_UPLOAD_2H", fixed_time=None, ack=False, **extra):
    """A task type (template) of the OPS department with the given trigger and SLA rule."""
    if trigger == "FIXED_TIME" and fixed_time is None:
        fixed_time = time(10, 0)
    return TaskTemplate.objects.create(
        code=f"TT_{trigger}_{next(_counter)}", name=f"{trigger.title()} type",
        department=ops_department(), resolution_rule_code=rule, trigger=trigger,
        fixed_time=fixed_time, acknowledgment_required=ack, **extra,
    )


def duty(owner, assigned_by, *, template=None, frequency="DAILY", run_time=time(10, 0),
         start=date(2026, 10, 5), end=None, weekdays=None, day_of_month=None, run_date=None,
         policy="SKIP", priority="LOW", owner_from=date(2026, 1, 1)):
    """A responsibility owned by `owner` (None: nobody) with one schedule. Created directly so a
    test can use dates in the past of its own simulated clock."""
    responsibility = Responsibility.objects.create(
        code=f"DUTY_{next(_counter)}", name="Scheduled duty", department=ops_department(),
        category=TaskCategory.objects.filter(is_active=True).first(), template=template,
        priority=priority,
    )
    if owner is not None:
        ResponsibilityOwner.objects.create(responsibility=responsibility, employee=owner,
                                           effective_from=owner_from, assigned_by=assigned_by)
    schedule = RecurringSchedule.objects.create(
        responsibility=responsibility, title="Scheduled duty", frequency=frequency,
        run_time=run_time, weekdays=weekdays or [], day_of_month=day_of_month, run_date=run_date,
        non_working_day_policy=policy, effective_from=start, effective_to=end,
    )
    return schedule


def only(schedule):
    """Keep the seeded schedules out of the way: only `schedule` stays active."""
    RecurringSchedule.objects.exclude(pk=schedule.pk).update(is_active=False)
    return schedule


def run(*parts):
    with at(*parts):
        return generator.generate_due_occurrences()


def tick(*parts):
    with at(*parts):
        return sla_services.evaluate_clocks()


def task_of(schedule, day=None):
    qs = Task.objects.filter(schedule=schedule)
    return qs.get(occurrence_date=day) if day else qs.get()


def clock(task, kind="RESOLUTION"):
    return TaskSla.objects.get(task=task, kind=kind, is_current=True)
