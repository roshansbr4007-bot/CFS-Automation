"""Phase 7.3 scheduled KRA calculation (approved P12, E17). Thin: every month goes through
services.calculate_month (KRA engine + settlement), exactly as an HR calculation does.

- Daily run (Celery Beat, about 01:00 IST): the current month (provisional, cutoff = now) for
  every active employee, and the previous month again for its records still CALCULATED (late HR
  inputs such as approved leave are picked up; the cutoff stays the month end).
- Month close (Celery Beat, the 1st at about 02:00 IST): the previous month for every active
  employee (and any CALCULATED record of an employee who has since left), cutoff = month end.
Neither run touches a month UNDER_REVIEW or FINALIZED, submits anything for review, or
calculates a LEGACY month or an existing legacy record (legacy calculation stays on demand,
P7). One employee's refusal or failure is counted (and logged) and never stops the others.
"""

import logging
from collections import Counter

from django.utils import timezone

from apps.core.timeutils import to_ist
from apps.org.models import Employee

from . import kra_engine
from .models import CalculationModel, MonthlyPerformance, PerformanceStatus
from .services import (
    NoKPIAssignment,
    PerformanceError,
    assignment_for,
    calculate_month,
    month_bounds,
)

LOCKED = (PerformanceStatus.UNDER_REVIEW, PerformanceStatus.FINALIZED)
logger = logging.getLogger(__name__)


def _previous(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _calculate(employee, year: int, month: int, now, counts: Counter) -> None:
    _, period_end = month_bounds(year, month)
    assignment = assignment_for(employee, period_end)
    if (assignment is not None
            and assignment.weight_version.calculation_model == CalculationModel.LEGACY_WEIGHTED):
        counts["legacy_not_scheduled"] += 1
        return
    status, model = (MonthlyPerformance.objects.filter(employee=employee, year=year, month=month)
                     .values_list("status", "calculation_model").first() or (None, None))
    if model == CalculationModel.LEGACY_WEIGHTED:  # an existing legacy record: HR's call only
        counts["legacy_not_scheduled"] += 1
        return
    if status in LOCKED:
        counts["under_review" if status == PerformanceStatus.UNDER_REVIEW else "finalized"] += 1
        return
    try:
        calculate_month(actor=None, employee=employee, year=year, month=month, now=now)
    except NoKPIAssignment:  # no plan, or an ambiguous role (HR must set an override)
        counts["no_plan"] += 1
    except (kra_engine.MonthNotStarted, kra_engine.NotEmployedInPeriod):
        counts["not_in_period"] += 1
    except PerformanceError as exc:  # e.g. a verification configuration error (D5)
        counts["refused"] += 1
        logger.warning("KRA %s-%02d not calculated for employee %s: %s", year, month,
                       employee.pk, exc.message)
    except Exception:  # each calculation is atomic: log it and go on with the others
        counts["error"] += 1
        logger.exception("KRA %s-%02d calculation failed for employee %s", year, month,
                         employee.pk)
    else:
        counts["calculated"] += 1


def _employees_for(year: int, month: int):
    calculated = MonthlyPerformance.objects.filter(
        year=year, month=month, status=PerformanceStatus.CALCULATED,
        calculation_model=CalculationModel.KRA_POINTS,
    ).values_list("employee_id", flat=True)
    return Employee.objects.filter(pk__in=calculated)


def run_daily(now=None) -> dict:
    now = now or timezone.now()
    today = to_ist(now).date()
    current, previous = Counter(), Counter()
    for employee in Employee.objects.filter(is_active=True).order_by("pk"):
        _calculate(employee, today.year, today.month, now, current)
    year, month = _previous(today.year, today.month)
    for employee in _employees_for(year, month).order_by("pk"):
        _calculate(employee, year, month, now, previous)
    return {"current": dict(current), "previous": dict(previous)}


def run_month_close(now=None) -> dict:
    now = now or timezone.now()
    today = to_ist(now).date()
    year, month = _previous(today.year, today.month)
    counts = Counter()
    people = (Employee.objects.filter(is_active=True) | _employees_for(year, month)).distinct()
    for employee in people.order_by("pk"):
        _calculate(employee, year, month, now, counts)
    return {"year": year, "month": month, **counts}
