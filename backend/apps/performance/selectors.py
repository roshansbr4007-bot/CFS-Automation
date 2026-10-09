"""Read-only performance reporting queries (Phase 7 Stage B).

Reporting CONSUMES the stored monthly snapshots produced by apps.performance.services; it
never recalculates a score. One function (`report_queryset`) decides what a user may see and
applies the filters; the JSON report, the CSV and the Excel export all use it.

Who sees what (existing org visibility, apps.org.selectors.visible_employees), any status:
- org.view_all_employees (HR, Admin)        -> every employee's records
- org.view_team_employees (Ops Manager)    -> own department's records
- anyone else (Employee)                    -> own records only
Client-supplied employee / department filters only ever NARROW that scope.

Known limitation: historical MonthlyPerformance records are reported (and filtered) using the
employee's CURRENT department, because Stage A does not persist a department snapshot on
MonthlyPerformance. After a transfer, earlier months appear under the new department.
"""

from dataclasses import dataclass
from datetime import date

from django.db.models import Exists, OuterRef, Prefetch
from django.utils.dateparse import parse_date

from apps.core.errors import FieldValidationError
from apps.org import perms as org_perms
from apps.org.selectors import visible_employees

from .models import KPI, CalculationModel, MonthlyKPIScore, MonthlyPerformance, PerformanceStatus
from .services import BANDS, UNSATISFACTORY

PERFORMANCE_BANDS = tuple(band for _, band in BANDS) + (UNSATISFACTORY,)
REPORT_ORDER = ("employee__department__code", "employee__full_name", "employee_id",
                "year", "month", "id")


@dataclass(frozen=True)
class ReportFilters:
    year: int | None = None
    month: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    employee: int | None = None
    department: int | None = None
    kpi: str | None = None
    status: str | None = None
    performance_band: str | None = None


def _int(params, name, *, low=None, high=None):
    raw = params.get(name)
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise FieldValidationError(fields={name: ["Enter a whole number."]}) from None
    if (low is not None and value < low) or (high is not None and value > high):
        raise FieldValidationError(fields={name: [f"Use a value from {low} to {high}."]})
    return value


def _date(params, name):
    raw = params.get(name)
    if raw in (None, ""):
        return None
    value = parse_date(raw) if isinstance(raw, str) else None
    if value is None:
        raise FieldValidationError(fields={name: ["Use the format YYYY-MM-DD."]})
    return value


def _choice(params, name, allowed):
    raw = params.get(name)
    if raw in (None, ""):
        return None
    if raw not in allowed:
        raise FieldValidationError(fields={name: [f"Use one of: {', '.join(allowed)}."]})
    return raw


def parse_report_filters(params) -> ReportFilters:
    """Validate query parameters; invalid values raise a 400 (never a 500)."""
    filters = ReportFilters(
        year=_int(params, "year", low=2000, high=2100),
        month=_int(params, "month", low=1, high=12),
        date_from=_date(params, "date_from"),
        date_to=_date(params, "date_to"),
        employee=_int(params, "employee", low=1),
        department=_int(params, "department", low=1),
        kpi=params.get("kpi") or None,
        status=_choice(params, "status", tuple(PerformanceStatus.values)),
        performance_band=_choice(params, "performance_band", PERFORMANCE_BANDS),
    )
    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        raise FieldValidationError(fields={"date_to": ["Must be on or after date_from."]})
    if filters.kpi and not KPI.objects.filter(code=filters.kpi).exists():
        raise FieldValidationError(fields={"kpi": ["Unknown KPI code."]})
    return filters


def can_view_all(user) -> bool:
    return user.has_perm(org_perms.VIEW_ALL_EMPLOYEES)


def can_export(user) -> bool:
    """Exports are for organisation-wide reporting roles (HR, Admin)."""
    return can_view_all(user)


def visible_performance(user):
    """The monthly records `user` may see: those of the employees `user` may see (see module
    docstring). There is no status restriction."""
    return MonthlyPerformance.objects.filter(employee__in=visible_employees(user))


def report_queryset(user, filters: ReportFilters):
    """Visible records, filtered, in a fixed order, with KPI scores prefetched in KPI-code
    order (limited to one KPI when the kpi filter is used). Read-only."""
    # Phase 7.2: this report shows legacy (0-100) records only; KRA points records get their
    # own reports in Phase 7.4 and must never appear here with blank scores.
    qs = visible_performance(user).filter(calculation_model=CalculationModel.LEGACY_WEIGHTED)
    if filters.year is not None:
        qs = qs.filter(year=filters.year)
    if filters.month is not None:
        qs = qs.filter(month=filters.month)
    # The authoritative performance dates are the record's period: keep records whose
    # period overlaps [date_from, date_to].
    if filters.date_from is not None:
        qs = qs.filter(period_end__gte=filters.date_from)
    if filters.date_to is not None:
        qs = qs.filter(period_start__lte=filters.date_to)
    if filters.employee is not None:
        qs = qs.filter(employee_id=filters.employee)
    if filters.department is not None:  # current department (see the known limitation)
        qs = qs.filter(employee__department_id=filters.department)
    if filters.status is not None:
        qs = qs.filter(status=filters.status)
    if filters.performance_band is not None:
        qs = qs.filter(performance_band=filters.performance_band)
    scores = MonthlyKPIScore.objects.select_related("kpi").order_by("kpi__code", "id")
    if filters.kpi is not None:
        qs = qs.filter(Exists(MonthlyKPIScore.objects.filter(
            monthly_performance=OuterRef("pk"), kpi__code=filters.kpi
        )))
        scores = scores.filter(kpi__code=filters.kpi)
    return (
        qs.select_related("employee__department")
        .prefetch_related(Prefetch("kpi_scores", queryset=scores, to_attr="report_scores"))
        .order_by(*REPORT_ORDER)
    )
