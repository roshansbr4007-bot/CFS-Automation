"""Eligibility (P1), operational metrics and rates (P2), Timeliness, overall score and bands."""

from datetime import date
from decimal import Decimal

import pytest
import time_machine

from apps.core.errors import FieldValidationError
from apps.performance import services
from apps.performance.models import MonthlyKPIScore
from apps.recurring import generator
from apps.sla.models import TaskSla

pytestmark = pytest.mark.django_db
NOV_5 = (2026, 11, 5, 9, 0)  # calculation time for October: the month is over


def _metrics(employee, ist, year=2026, month=10, at=NOV_5):
    start, end = services.month_bounds(year, month)
    return services.calculate_operational_metrics(employee, start, end, now=ist(*at))


@pytest.fixture
def october(ops, work, sla_24h):
    """Rahul's October, built only through the existing task services."""
    rahul, rahul_emp = ops["rahul"], ops["rahul_emp"]
    on_time = work.raise_task(rahul_emp, 2026, 10, 5, 10, 0, title="On time")
    work.complete(on_time, rahul, 2026, 10, 5, 12, 0)  # due 6 Oct 10:00 -> MET
    late = work.raise_task(rahul_emp, 2026, 10, 7, 10, 0, title="Late")
    work.complete(late, rahul, 2026, 10, 9, 10, 0)  # due 8 Oct 10:00 -> MISSED
    work.raise_task(rahul_emp, 2026, 10, 10, 10, 0, title="Open overdue")  # due 11 Oct
    work.raise_task(rahul_emp, 2026, 10, 31, 22, 0, title="Due in November")  # due 1 Nov
    cancelled = work.raise_task(rahul_emp, 2026, 10, 12, 10, 0, title="Cancelled")
    work.cancel(cancelled, 2026, 10, 12, 11, 0)
    adhoc = work.raise_task(rahul_emp, 2026, 10, 15, 10, 0, priority="MEDIUM", title="No SLA")
    work.complete(adhoc, rahul, 2026, 10, 16, 10, 0)
    moved = work.raise_task(rahul_emp, 2026, 10, 20, 10, 0, title="Moved to Amit")
    work.reassign(moved, ops["amit_emp"], 2026, 10, 20, 11, 0)


def test_eligibility_follows_the_approved_rules(ops, october):
    start, end = services.month_bounds(2026, 10)
    titles = sorted(t.title for t in services.eligible_tasks(ops["rahul_emp"], start, end))
    assert titles == ["Late", "No SLA", "On time", "Open overdue"]
    nov_start, nov_end = services.month_bounds(2026, 11)
    assert [t.title for t in services.eligible_tasks(ops["rahul_emp"], nov_start, nov_end)] == [
        "Due in November"  # an SLA task counts in its deadline month
    ]
    amit = [t.title for t in services.eligible_tasks(ops["amit_emp"], start, end)]
    assert amit == ["Moved to Amit"]  # the current assignee gets it


def test_monthly_metrics_and_rates(ops, october, ist):
    m = _metrics(ops["rahul_emp"], ist)
    assert (m.assigned_tasks, m.completed_tasks, m.pending_tasks, m.overdue_tasks) == (4, 3, 1, 1)
    assert (m.sla_met_tasks, m.sla_breached_tasks) == (1, 2)  # Late (MISSED) + Open overdue
    assert (m.on_time_completed_tasks, m.completed_sla_tasks) == (1, 2)
    assert (m.manual_tasks, m.scheduled_tasks) == (4, 0)
    assert m.completion_rate == Decimal("75.00")
    assert m.sla_compliance_rate == Decimal("33.33")
    assert m.timeliness == Decimal("50.00")  # "No SLA" is not part of timeliness


def test_open_task_is_overdue_only_once_its_deadline_has_passed(ops, work, sla_24h, ist):
    work.raise_task(ops["rahul_emp"], 2026, 10, 10, 10, 0)
    before = _metrics(ops["rahul_emp"], ist, at=(2026, 10, 11, 9, 0))  # current month, 1 h left
    assert (before.overdue_tasks, before.sla_breached_tasks) == (0, 0)
    after = _metrics(ops["rahul_emp"], ist, at=(2026, 10, 11, 11, 0))
    assert (after.overdue_tasks, after.sla_breached_tasks) == (1, 1)


def test_no_tasks_and_all_cancelled_give_not_applicable_rates(ops, work, sla_24h, ist):
    empty = _metrics(ops["rahul_emp"], ist)
    assert empty.assigned_tasks == 0
    rates = (empty.completion_rate, empty.sla_compliance_rate, empty.timeliness)
    assert rates == (None, None, None)
    task = work.raise_task(ops["rahul_emp"], 2026, 10, 5, 10, 0)
    work.cancel(task, 2026, 10, 5, 11, 0)
    cancelled = _metrics(ops["rahul_emp"], ist)
    assert cancelled.assigned_tasks == 0 and cancelled.completion_rate is None


def test_no_completed_eligible_tasks(ops, work, sla_24h, ist):
    work.raise_task(ops["rahul_emp"], 2026, 10, 5, 10, 0)
    m = _metrics(ops["rahul_emp"], ist)
    assert m.completion_rate == Decimal("0.00")  # there was work; none completed
    assert m.timeliness is None  # nothing completed with an SLA: not applicable
    assert m.sla_compliance_rate == Decimal("0.00")  # the open task breached its SLA


def test_all_tasks_completed_on_time(ops, work, sla_24h, ist):
    for day in (5, 6):
        task = work.raise_task(ops["rahul_emp"], 2026, 10, day, 10, 0)
        work.complete(task, ops["rahul"], 2026, 10, day, 11, 0)
    m = _metrics(ops["rahul_emp"], ist)
    assert (m.completion_rate, m.sla_compliance_rate, m.timeliness) == (
        Decimal("100.00"), Decimal("100.00"), Decimal("100.00"),
    )


def test_all_tasks_overdue(ops, work, sla_24h, ist):
    for day in (5, 6):
        work.raise_task(ops["rahul_emp"], 2026, 10, day, 10, 0)
    m = _metrics(ops["rahul_emp"], ist)
    assert (m.overdue_tasks, m.sla_breached_tasks) == (2, 2)
    assert m.sla_compliance_rate == Decimal("0.00") and m.timeliness is None


def test_daily_activities_count_with_their_own_subtotal(ops, resp, own, ist):
    own(resp("FEED_UPLOAD"), ops["rahul_emp"])
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        generator.generate_due_occurrences()
    m = _metrics(ops["rahul_emp"], ist)
    assert (m.scheduled_tasks, m.manual_tasks, m.assigned_tasks) == (1, 0, 1)
    assert m.overdue_tasks == 1  # Feed Upload was due at 12:00 on 5 Oct and never completed


def test_zero_denominator_helper():
    assert services.percent(0, 0) is None
    assert services.percent(1, 3) == Decimal("33.33")
    assert services.percent(2, 3) == Decimal("66.67")


def test_weighted_overall_score_example():
    pairs = [(Decimal(s), Decimal(w)) for s, w in
             (("92", "3.0"), ("95", "2.0"), ("90", "1.5"), ("94", "1.5"), ("96", "1.0"),
              ("98", "1.0"))]
    assert services.overall_score(pairs) == Decimal("93.60")
    assert services.overall_score([(None, Decimal("3"))] + pairs[1:]) is None  # never treated as 0
    assert services.overall_score([(Decimal("80"), Decimal("0"))]) is None
    assert services.overall_score([]) is None


@pytest.mark.parametrize(
    ("score", "band"),
    [("100", "Excellent"), ("90", "Excellent"), ("89.99", "Very Good"), ("80", "Very Good"),
     ("79.99", "Good"), ("70", "Good"), ("69.99", "Needs Improvement"),
     ("60", "Needs Improvement"), ("59.99", "Unsatisfactory"), ("0", "Unsatisfactory")],
)
def test_performance_band(score, band):
    assert services.performance_band(Decimal(score)) == band


def test_band_of_no_score_is_empty():
    assert services.performance_band(None) == ""


def test_month_bounds():
    assert services.month_bounds(2026, 2) == (date(2026, 2, 1), date(2026, 2, 28))
    assert services.month_bounds(2028, 2) == (date(2028, 2, 1), date(2028, 2, 29))
    with pytest.raises(FieldValidationError):
        services.month_bounds(2026, 13)


def test_calculation_creates_the_snapshot_with_timeliness_from_the_system(
    ops, october, assign, ist
):
    assign(ops["rahul_emp"])
    with time_machine.travel(ist(*NOV_5), tick=False):
        performance = services.calculate_monthly_performance(
            actor=None, employee=ops["rahul_emp"], year=2026, month=10
        )
    assert performance.status == "CALCULATED"
    assert (performance.assigned_tasks, performance.completed_tasks) == (4, 3)
    assert performance.completion_rate == Decimal("75.00")
    assert performance.sla_compliance_rate == Decimal("33.33")
    scores = {s.kpi.code: s for s in MonthlyKPIScore.objects.filter(
        monthly_performance=performance).select_related("kpi")}
    assert set(scores) == {"ACCURACY", "TIMELINESS", "CLIENT_SERVICING", "FINANCIAL_ACCURACY",
                           "DATA_SYSTEM", "COMPLIANCE"}
    assert scores["TIMELINESS"].score == Decimal("50.00")
    assert scores["TIMELINESS"].source == "SYSTEM"
    assert scores["ACCURACY"].score is None and scores["ACCURACY"].weight == Decimal("3.00")
    assert performance.overall_score is None and performance.performance_band == ""


def test_calculation_needs_a_kpi_assignment(ops):
    with pytest.raises(services.NoKPIAssignment):
        services.calculate_monthly_performance(actor=None, employee=ops["rahul_emp"],
                                               year=2026, month=10)


# --- one authoritative resolution clock -------------------------------------------------------


def test_reassignment_never_adds_a_resolution_clock(ops, work, sla_24h):
    task = work.raise_task(ops["rahul_emp"], 2026, 10, 5, 10, 0)
    work.reassign(task, ops["amit_emp"], 2026, 10, 5, 11, 0)
    assert TaskSla.objects.filter(task=task, kind="RESOLUTION").count() == 1
    start, end = services.month_bounds(2026, 10)
    assert services.eligible_tasks(ops["amit_emp"], start, end) == [task]


def test_eligibility_and_metrics_read_the_same_authoritative_clock(ops, work, sla_24h, ist):
    """Defensive: the SLA module creates one resolution clock per task today. If data ever held
    two, the month AND the outcome both come from the clock the SLA engine reads (the latest)."""
    task = work.raise_task(ops["rahul_emp"], 2026, 10, 5, 10, 0)  # first clock: due 6 Oct
    first = TaskSla.objects.get(task=task, kind="RESOLUTION")
    with time_machine.travel(ist(2026, 10, 20, 10, 0), tick=False):
        TaskSla.objects.create(
            task=task, kind="RESOLUTION", rule=first.rule, rule_snapshot=first.rule_snapshot,
            trigger=first.trigger, start_at=ist(2026, 10, 20, 10, 0),
            due_at=ist(2026, 11, 3, 10, 0), is_current=False,
        )
    oct_start, oct_end = services.month_bounds(2026, 10)
    nov_start, nov_end = services.month_bounds(2026, 11)
    assert services.eligible_tasks(ops["rahul_emp"], oct_start, oct_end) == []
    assert services.eligible_tasks(ops["rahul_emp"], nov_start, nov_end) == [task]
    november = _metrics(ops["rahul_emp"], ist, 2026, 11, at=(2026, 12, 5, 9, 0))
    assert (november.assigned_tasks, november.overdue_tasks) == (1, 1)  # judged on 3 Nov
    assert _metrics(ops["rahul_emp"], ist).assigned_tasks == 0
