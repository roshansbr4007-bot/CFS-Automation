"""Scheduling fix x KRA (no KRA code or policy change): how a catch-up occurrence, whose clocks
now start at its SCHEDULED time, meets the existing month rules. Documents and protects:

- a FINALIZED month never changes (scores, credits, totals, band); the catch-up is then counted
  in NO month unless HR deliberately reopens and recalculates that month (known risk);
- a CALCULATED previous month picks it up at the next daily run (existing rule), as OVERDUE;
- an UNDER_REVIEW month is not recalculated until HR returns it for recalculation;
- a same-month catch-up is scored against its scheduled deadline (late, not on time).

Weekly duty: Mondays 10:00, Feed Upload type (fixed time 10:00, 2 h). Plan: OPERATIONS_KRA from
1 Nov 2026, Accuracy = this duty's scheduled tasks; the other KPIs are HR manual entries.
"""

from datetime import date
from decimal import Decimal

import pytest
import time_machine

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.performance import review_services as review
from apps.performance import scheduling, services
from apps.performance.annual import annual_summary
from apps.performance.models import (
    MonthlyComponentResult,
    MonthlyKPIScore,
    MonthlyPerformance,
    MonthlyTaskCredit,
)
from apps.recurring.tests.scheduled_helpers import duty, only, run
from apps.sla.models import TaskSla
from apps.tasks import services as task_services
from apps.tasks.models import Task, TaskTemplate

from .test_kra_calculation import CLOSE, _category
from .test_kra_review import _activate_plan, _calc, _fill_manual, _fresh

pytestmark = pytest.mark.django_db
D = Decimal


@pytest.fixture
def hr(make_user):
    return make_user(roles.HR)


@pytest.fixture
def weekly(admin_user, hr, ops, ist):
    """The duty and the plan; returns a helper object."""
    schedule = only(duty(ops["rahul_emp"], admin_user, frequency="WEEKLY", weekdays=[0],
                         template=TaskTemplate.objects.get(code="FEED_UPLOAD"),
                         start=date(2026, 11, 2)))
    _activate_plan(admin_user, hr, ist, _category(schedule.responsibility, scope="SCHEDULED"))

    class Weekly:
        emp = ops["rahul_emp"]

        def task(self, day):
            return Task.objects.get(schedule=schedule, occurrence_date=day)

        def complete(self, task, *at):
            with time_machine.travel(ist(*at), tick=False):
                task.refresh_from_db()
                task_services.start_task(actor=ops["rahul"], task=task, version=task.version)
                task.refresh_from_db()
                task_services.complete_task(
                    actor=ops["rahul"], task=task, version=task.version, work_response="Work done."
                )

        def at(self, *parts):
            return time_machine.travel(ist(*parts), tick=False)

    w = Weekly()
    w.schedule = schedule
    return w


def _on_time_mondays(w):
    """2, 9, 16 and 23 Nov generated at 10:00 and done at 11:00; 30 Nov: scheduler down."""
    for day in (2, 9, 16, 23):
        run(2026, 11, day, 10, 0)
        w.complete(w.task(date(2026, 11, day)), 2026, 11, day, 11, 0)


def _closed_november(w, hr, ist):
    _on_time_mondays(w)
    return _fill_manual(_calc(w.emp, ist, *CLOSE), hr)  # CLOSE = 2 Dec 09:00


def _catch_up(w):
    """2 Dec 11:00: the scheduler is back; Monday 30 Nov is generated (within one cycle)."""
    run(2026, 12, 2, 11, 0)
    task = w.task(date(2026, 11, 30))
    resolution = TaskSla.objects.get(task=task, kind="RESOLUTION")
    assert resolution.due_at.isoformat() == "2026-11-30T06:30:00+00:00"  # 12:00 IST, November
    return task


def _credits(record):
    return dict(
        MonthlyTaskCredit.objects.filter(component_result__kpi_score__monthly_performance=record,
                                         task_id__isnull=False)
        .values_list("task_id", "outcome")
    )


def _snapshot(record):
    record = _fresh(record)
    fields = ("status", "version", "final_total", "auto_total", "adjustment_total",
              "deduction_total", "max_points_applicable", "band_id", "band_name", "cutoff_at",
              "finalized_at", "finalized_by_id")
    scores = MonthlyKPIScore.objects.filter(monthly_performance=record).order_by("pk")
    results = MonthlyComponentResult.objects.filter(
        kpi_score__monthly_performance=record).order_by("pk")
    credits = MonthlyTaskCredit.objects.filter(
        component_result__kpi_score__monthly_performance=record).order_by("pk")
    return (
        {f: getattr(record, f) for f in fields},
        list(scores.values()),
        list(results.values()),
        list(credits.values()),
    )


def _finalize(record, hr, when):
    with time_machine.travel(when, tick=False):
        record = review.submit(actor=hr, performance=record, version=_fresh(record).version)
        return review.finalize(actor=hr, performance=record, version=_fresh(record).version)


def test_a_finalized_month_never_changes_and_the_catch_up_counts_in_no_month(
    weekly, hr, ist
):
    w = weekly
    record = _finalize(_closed_november(w, hr, ist), hr, ist(2026, 12, 2, 9, 30))
    assert record.status == "FINALIZED"
    before = _snapshot(record)
    annual_before = annual_summary(w.emp, 2026)
    task = _catch_up(w)

    daily = scheduling.run_daily(now=ist(2026, 12, 3, 1, 0))
    close = scheduling.run_month_close(now=ist(2026, 12, 3, 2, 0))
    assert daily["previous"].get("finalized", 0) == 0  # not even selected: not CALCULATED
    assert close["finalized"] == 1
    with pytest.raises(services.PerformanceError):
        _calc(w.emp, ist, 2026, 12, 3, 9, 0)  # an HR calculation of November is refused
    assert _snapshot(record) == before
    annual_after = annual_summary(w.emp, 2026)  # December now exists, provisional (excluded)
    for key in ("applicable_months", "annual_total", "annual_average", "months"):
        assert annual_after[key] == annual_before[key]
    assert task.pk not in _credits(record)

    december = MonthlyPerformance.objects.get(employee=w.emp, year=2026, month=12)
    assert task.pk not in _credits(december)  # its deadline is in November: no month counts it
    assert not AuditLog.objects.filter(action="performance.kra_reopened").exists()


def test_reopening_and_recalculating_a_finalized_month_picks_the_catch_up_up(weekly, hr, ist):
    w = weekly
    record = _finalize(_closed_november(w, hr, ist), hr, ist(2026, 12, 2, 9, 30))
    finalized_total = _fresh(record).final_total
    task = _catch_up(w)
    with w.at(2026, 12, 3, 10, 0):
        record = review.reopen(actor=hr, performance=record, version=_fresh(record).version,
                               reason="Late-generated occurrence of 30 Nov")
    assert task.pk not in _credits(record)  # reopening alone recalculates nothing
    reopened = AuditLog.objects.get(action="performance.kra_reopened")
    assert D(reopened.old_value["final_total"]) == finalized_total  # history kept in the audit
    with w.at(2026, 12, 3, 10, 5):
        record = review.return_for_recalculation(
            actor=hr, performance=record, version=_fresh(record).version,
            reason="Include the late occurrence", now=ist(2026, 12, 3, 10, 5),
        )
    assert _credits(record)[task.pk] == "OVERDUE"


def test_a_calculated_previous_month_picks_the_catch_up_up_at_the_next_daily_run(
    weekly, hr, ist
):
    w = weekly
    record = _closed_november(w, hr, ist)
    assert set(_credits(record).values()) == {"ON_TIME"}
    task = _catch_up(w)
    scheduling.run_daily(now=ist(2026, 12, 3, 1, 0))
    record = _fresh(record)
    assert record.status == "CALCULATED"
    assert _credits(record)[task.pk] == "OVERDUE"  # created after the cutoff: never "on time"


def test_an_under_review_month_waits_for_a_return_for_recalculation(weekly, hr, ist):
    w = weekly
    record = _closed_november(w, hr, ist)
    with w.at(2026, 12, 2, 9, 30):
        record = review.submit(actor=hr, performance=record, version=_fresh(record).version)
    before = _snapshot(record)
    task = _catch_up(w)
    daily = scheduling.run_daily(now=ist(2026, 12, 3, 1, 0))
    assert daily["previous"].get("under_review", 0) == 0  # not selected: not CALCULATED
    assert _snapshot(record) == before
    with w.at(2026, 12, 3, 10, 0):
        record = review.return_for_recalculation(
            actor=hr, performance=record, version=_fresh(record).version,
            reason="Include the late occurrence", now=ist(2026, 12, 3, 10, 0),
        )
    assert _credits(record)[task.pk] == "OVERDUE"


def test_a_same_month_catch_up_is_scored_against_its_scheduled_deadline(weekly, hr, ist):
    w = weekly
    run(2026, 11, 2, 10, 0)
    w.complete(w.task(date(2026, 11, 2)), 2026, 11, 2, 11, 0)
    run(2026, 11, 11, 11, 0)  # Wednesday: Monday 9 Nov is generated two days late
    late = w.task(date(2026, 11, 9))
    w.complete(late, 2026, 11, 11, 11, 30)  # done 30 minutes after it arrived
    record = _calc(w.emp, ist, *CLOSE)
    credits = _credits(record)
    assert credits[w.task(date(2026, 11, 2)).pk] == "ON_TIME"
    assert credits[late.pk] == "LATE"  # due 9 Nov 12:00 (system-caused; HR may adjust)
