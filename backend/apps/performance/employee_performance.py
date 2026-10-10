"""Phase 7.4: what an employee may see of their OWN KRA performance (Employee Home). Read-only.

The employee is always the caller's own linked employee record (the same rule as
/employees/me/): no permission, filter or parameter ever widens this to anyone else, also not
for HR or Admin. Without a linked employee record the answer is 404 `no_employee_record`.

What the employee sees of a KRA month depends only on its stored state (approved decisions
1 / D / E; the review workflow itself is not touched):
    CALCULATED, still provisional (cutoff before month end) -> PROVISIONAL: scores, marked so
    CALCULATED, month closed (incl. after a return)         -> PENDING_REVIEW: no numbers
    UNDER_REVIEW (incl. reopened)                            -> PENDING_REVIEW: no numbers
    FINALIZED                                                -> FINALIZED: full detail
    anything else                                            -> PENDING_REVIEW: no numbers
The raw status and the reopen count are never returned.

N/A reasons travel only as labels from NA_LABELS; an internal or unknown code is never sent
(approved decision 3). Deductions are the E19 lines (rule, KPI / component, points) from
review_services.employee_view; evidence, reasons, HR users and adjustment reasons are never
returned. Task-level rows are not shown (decision A: component counts only). Legacy (0-100)
months are not part of this module (Home reads the existing legacy report).
"""

from django.db.models import Prefetch
from django.http import Http404

from apps.core.errors import AppError
from apps.core.timeutils import now_ist
from apps.org.selectors import own_employee

from . import annual, review_services
from .kra_engine import NA
from .models import CalculationModel, MonthlyComponentResult, MonthlyPerformance, PerformanceStatus

PROVISIONAL = "PROVISIONAL"
PENDING_REVIEW = "PENDING_REVIEW"
FINALIZED = "FINALIZED"

# Approved employee-safe N/A labels. Anything not listed gets GENERIC_NA_LABEL.
NA_LABELS = {
    NA.NO_APPLICABLE_TASKS: "No applicable tasks this month",
    NA.AWAITING_HR_ENTRY: "Awaiting HR assessment",
    NA.HR_MARKED_NA: "Not applicable this month",
    NA.ALL_COMPONENTS_NA: "Not applicable this month",
}
GENERIC_NA_LABEL = "Not applicable"


class NoEmployeeRecord(AppError):
    """Same contract as GET /employees/me/ for a login without an employee record."""

    status_code = 404
    code = "no_employee_record"
    message = "You do not have an employee record yet."


def na_label(code: str) -> str:
    return NA_LABELS.get(code or "", GENERIC_NA_LABEL)


def employee_for(user):
    employee = own_employee(user)
    if employee is None:
        raise NoEmployeeRecord()
    return employee


def employee_state(performance) -> str:
    if performance.status == PerformanceStatus.FINALIZED:
        return FINALIZED
    if (performance.status == PerformanceStatus.CALCULATED
            and not review_services.month_closed(performance)):
        return PROVISIONAL
    return PENDING_REVIEW


def _own_months(employee):
    """Every KRA month of the employee. The history starts at the joining date because the
    engine never calculates a month that ends before it (NotEmployedInPeriod); no extra filter
    is applied, so the history always matches the approved annual figures."""
    return MonthlyPerformance.objects.filter(
        employee=employee, calculation_model=CalculationModel.KRA_POINTS
    )


def _scores(state, performance) -> dict:
    """The month-level numbers, only for PROVISIONAL and FINALIZED months."""
    shown = state in (PROVISIONAL, FINALIZED)
    return {
        "final_total": performance.final_total if shown else None,
        "max_points_applicable": performance.max_points_applicable if shown else None,
        "band": performance.band_name if shown else None,
    }


def history(user) -> dict:
    employee = employee_for(user)
    today = now_ist().date()
    rows = []
    for performance in _own_months(employee).order_by("-year", "-month"):
        state = employee_state(performance)
        rows.append({"id": performance.pk, "year": performance.year,
                     "month": performance.month, "state": state,
                     **_scores(state, performance)})
    return {
        "employee": {"id": employee.pk, "full_name": employee.full_name,
                     "date_of_joining": employee.date_of_joining},
        "current": {"year": today.year, "month": today.month},
        "months": rows,
    }


def month_detail(user, pk: int) -> dict:
    employee = employee_for(user)
    performance = _own_months(employee).filter(pk=pk).first()
    if performance is None:  # someone else's month or a legacy month: never shown
        raise Http404
    state = employee_state(performance)
    body = {"id": performance.pk, "year": performance.year, "month": performance.month,
            "state": state}
    if state == PENDING_REVIEW:
        return body
    components = MonthlyComponentResult.objects.order_by("id")
    scores = (performance.kpi_scores.select_related("kpi").order_by("id")
              .prefetch_related(Prefetch("component_results", queryset=components)))
    body.update({
        "auto_total": performance.auto_total,
        **_scores(state, performance),
        "kpis": [
            {
                "name": s.name_snapshot or s.kpi.name,
                "weight": s.weight,
                "not_applicable": s.not_applicable,
                "na_label": na_label(s.na_reason) if s.not_applicable else None,
                "achievement_pct": s.achievement_pct,
                "auto_points": s.auto_points,
                "final_points": s.final_points,
                "components": [
                    {"label": c.label_snapshot, "applicable": c.applicable,
                     "na_label": None if c.applicable else na_label(c.na_reason),
                     "achievement_pct": c.achievement_pct, "on_time_count": c.on_time_count,
                     "late_count": c.late_count, "overdue_count": c.overdue_count}
                    for c in s.component_results.all()
                ],
            }
            for s in scores
        ],
        # E19 lines; only a finalized month can carry HR deductions (they are applied under
        # review, after the month has closed).
        "deductions": (review_services.employee_view(performance)["deductions"]
                       if state == FINALIZED else []),
    })
    return body


def annual_summary(user, year: int) -> dict:
    """The approved annual figures (annual.annual_summary, unchanged) of the caller's own
    employee record, without the record id and the raw status of excluded months."""
    summary = annual.annual_summary(employee_for(user), year)
    return {
        "year": summary["year"],
        "applicable_months": summary["applicable_months"],
        "annual_total": summary["annual_total"],
        "annual_average": summary["annual_average"],
        "maximum_total": summary["maximum_total"],
        "months": summary["months"],
        "excluded": [{"month": row["month"], "reason": row["reason"]}
                     for row in summary["excluded"]],
    }

