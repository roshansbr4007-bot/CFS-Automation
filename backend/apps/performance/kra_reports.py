"""Phase 7.4 KRA month listing and exports (HR / Admin). Read-only: every value is read from the
stored KRA month; nothing is recalculated (no settlement is run, approved decision G).

One filter definition (parse_kra_filters) and one query (kra_report_queryset) serve the JSON
list, the CSV and the Excel export, so a file always contains exactly the filtered list - all
matching rows, never just one page.

Semantics:
- Only KRA (points) months. Legacy (0-100) months stay in the legacy report.
- Department = the department recorded on the month when it was calculated
  (MonthlyPerformance.department), for the filter and for the column - never the employee's
  current department.
- Provisional = calculated only up to a cutoff before the month end: the IST date of the
  cutoff is on or before the month's last day (the same rule as
  review_services.month_closed). A month without a cutoff counts as provisional.
- The visible set starts from selectors.visible_performance(user); the API additionally
  requires the KRA reader permission (HR, Admin).
- Files never contain evidence, reasons, adjustment reasons or HR users. The Excel
  "Deduction Records" sheet lists the deductions HR RECORDED (rule, kind, scope, KPI,
  component, percentage, ceiling band, status); it is not a per-rule points breakdown. The
  stored deduction points are in "KPI Details" (per KPI, and the part taken at component
  level).
"""

import csv
import io
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import BooleanField, Case, F, Prefetch, Q, Value, When
from django.db.models.functions import TruncDate
from openpyxl import Workbook

from apps.core.errors import FieldValidationError
from apps.core.timeutils import IST

from . import reports
from .models import (
    Band,
    CalculationModel,
    DeductionApplication,
    MonthlyComponentResult,
    MonthlyKPIScore,
    PerformanceStatus,
)
from .selectors import _choice, _int, visible_performance
from .settlement import active_application_ids

ORDER = ("department__code", "employee__full_name", "employee_id", "year", "month", "id")
_TRUE, _FALSE = ("true", "1", "yes"), ("false", "0", "no")


@dataclass(frozen=True)
class KraReportFilters:
    year: int | None = None
    month: int | None = None
    employee: int | None = None
    department: int | None = None
    status: str | None = None
    band: str | None = None
    provisional: bool | None = None


def _bool(params, name):
    raw = params.get(name)
    if raw in (None, ""):
        return None
    if str(raw).lower() in _TRUE:
        return True
    if str(raw).lower() in _FALSE:
        return False
    raise FieldValidationError(fields={name: ["Use true or false."]})


def parse_kra_filters(params) -> KraReportFilters:
    """Validate query parameters; an invalid value is a 400 (never a 500)."""
    band = params.get("band") or None
    if band is not None and ("\x00" in band or len(band) > 60
                             or not Band.objects.filter(name=band).exists()):
        raise FieldValidationError(fields={"band": ["Unknown band."]})
    return KraReportFilters(
        year=_int(params, "year", low=2000, high=2100),
        month=_int(params, "month", low=1, high=12),
        employee=_int(params, "employee", low=1),
        department=_int(params, "department", low=1),
        status=_choice(params, "status", tuple(PerformanceStatus.values)),
        band=band,
        provisional=_bool(params, "provisional"),
    )


def kra_report_queryset(user, filters: KraReportFilters):
    qs = (
        visible_performance(user)
        .filter(calculation_model=CalculationModel.KRA_POINTS)
        .annotate(
            cutoff_date=TruncDate("cutoff_at", tzinfo=IST),
            provisional=Case(
                When(Q(cutoff_at__isnull=True) | Q(cutoff_date__lte=F("period_end")),
                     then=Value(True)),
                default=Value(False), output_field=BooleanField(),
            ),
        )
    )
    for name in ("year", "month", "status"):
        value = getattr(filters, name)
        if value is not None:
            qs = qs.filter(**{name: value})
    if filters.employee is not None:
        qs = qs.filter(employee_id=filters.employee)
    if filters.department is not None:  # recorded at calculation time
        qs = qs.filter(department_id=filters.department)
    if filters.band is not None:
        qs = qs.filter(band_name=filters.band)
    if filters.provisional is not None:
        qs = qs.filter(provisional=filters.provisional)
    return qs.select_related("employee", "department", "band_ceiling").order_by(*ORDER)


def with_export_details(qs):
    """The KPI rows (with their component results) and the recorded deductions of each month."""
    components = MonthlyComponentResult.objects.order_by("id")
    scores = (MonthlyKPIScore.objects.select_related("kpi").order_by("id")
              .prefetch_related(Prefetch("component_results", queryset=components)))
    applications = (DeductionApplication.objects
                    .select_related("rule", "ceiling_band", "kpi_score__kpi",
                                    "component_result")
                    .order_by("applied_at", "id"))
    return qs.prefetch_related(
        Prefetch("kpi_scores", queryset=scores, to_attr="export_scores"),
        Prefetch("deduction_applications", queryset=applications, to_attr="export_applications"),
    )


# --- rows ------------------------------------------------------------------------------------


SUMMARY_COLUMNS = (
    "Employee ID", "Employee Name", "Department (at calculation)", "Year", "Month", "Status",
    "Provisional", "Max Applicable Points", "Auto Total", "Adjustment Total",
    "Deduction Total", "Final Total", "Band", "Band Ceiling",
)
KPI_COLUMNS = (
    "Employee ID", "Employee Name", "Year", "Month", "KPI Code", "KPI Name", "KPI Max Points",
    "Not Applicable", "Achievement %", "Benchmark %", "Auto Points", "Adjustment Points",
    "Deduction Points (stored)", "Of which Component Deductions (stored)", "Final Points",
)
DEDUCTION_COLUMNS = (
    "Employee ID", "Employee Name", "Year", "Month", "Record ID", "Reverses Record ID",
    "Rule Code", "Rule Name", "Kind", "Scope", "KPI", "Component", "Percentage Applied",
    "Ceiling Band", "Record Status",
)
CSV_COLUMNS = SUMMARY_COLUMNS + KPI_COLUMNS[4:]
ABOUT_ROWS = (
    ("Sheet", "Contents"),
    ("KRA Summary", "One row per employee-month: the stored KRA totals and band."),
    ("KPI Details", "One row per employee-month and KPI: stored points. 'Deduction Points "
                    "(stored)' is everything deducted from the KPI (component, KPI and its share "
                    "of overall deductions); 'Of which Component Deductions' is the component "
                    "part."),
    ("Deduction Records", "The deductions HR recorded: rule, scope, target, the percentage "
                          "applied and the record status. It is NOT a per-rule breakdown of "
                          "points; the per-rule split is shown only in the HR month detail."),
    ("Department", "The department recorded when the month was calculated."),
    ("Not included", "Evidence, reasons, adjustment reasons and HR user names."),
)


def _yes_no(value) -> str:
    return "Yes" if value else "No"


def summary_row(record) -> tuple:
    employee = record.employee
    return (
        reports.employee_id(employee), employee.full_name,
        record.department.code if record.department_id else "", record.year, record.month,
        record.status, _yes_no(record.provisional), record.max_points_applicable,
        record.auto_total, record.adjustment_total, record.deduction_total,
        record.final_total, record.band_name,
        record.band_ceiling.name if record.band_ceiling_id else "",
    )


def kpi_rows(record):
    employee = record.employee
    for score in record.export_scores:
        component_part = sum((c.deduction_points for c in score.component_results.all()),
                             Decimal("0"))
        yield (
            reports.employee_id(employee), employee.full_name, record.year, record.month,
            score.kpi.code, score.name_snapshot or score.kpi.name, score.weight,
            _yes_no(score.not_applicable), score.achievement_pct, score.benchmark_pct,
            score.auto_points, score.adjustment_points, score.deduction_points,
            component_part, score.final_points,
        )


def record_status(application, reversed_ids, active_ids) -> str:
    if application.reverses_id is not None:
        return "Reversal"
    if application.pk in reversed_ids:
        return "Reversed"
    if application.pk in active_ids:
        return "Active"
    return "Not counted (another plan version)"


def deduction_rows(record):
    employee = record.employee
    applications = record.export_applications
    reversed_ids = {a.reverses_id for a in applications if a.reverses_id is not None}
    active_ids = active_application_ids(record, applications)
    for item in applications:
        score, component = item.kpi_score, item.component_result
        yield (
            reports.employee_id(employee), employee.full_name, record.year, record.month,
            item.pk, item.reverses_id, item.rule.code, item.rule.name, item.rule.kind,
            item.rule.scope, (score.name_snapshot or score.kpi.name) if score else "",
            component.label_snapshot if component else "", item.percent,
            item.ceiling_band.name if item.ceiling_band_id else "",
            record_status(item, reversed_ids, active_ids),
        )


# --- files -------------------------------------------------------------------------------------


def _text(value) -> str:
    return "" if value is None else str(value)


def write_csv(records) -> bytes:
    """One row per (employee-month, KPI), the month's summary repeated on each row (as in the
    legacy CSV). UTF-8 with a byte-order mark; full stored precision."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for record in records:
        summary = [_text(v) for v in summary_row(record)]
        rows = list(kpi_rows(record))
        for kpi in rows or [("",) * len(KPI_COLUMNS)]:
            writer.writerow([*summary, *(_text(v) for v in kpi[4:])])
    return buffer.getvalue().encode("utf-8-sig")


def write_xlsx(records) -> bytes:
    records = list(records)
    workbook = Workbook()
    reports._sheet(workbook, "KRA Summary", SUMMARY_COLUMNS,
                   (summary_row(r) for r in records), numeric_columns={8, 9, 10, 11, 12},
                   first=True)
    reports._sheet(workbook, "KPI Details", KPI_COLUMNS,
                   (row for r in records for row in kpi_rows(r)),
                   numeric_columns={7, 9, 10, 11, 12, 13, 14, 15})
    reports._sheet(workbook, "Deduction Records", DEDUCTION_COLUMNS,
                   (row for r in records for row in deduction_rows(r)), numeric_columns={13})
    about = workbook.create_sheet("About")
    for row in ABOUT_ROWS:
        about.append(list(row))
    about.column_dimensions["A"].width = 22
    about.column_dimensions["B"].width = 110
    for name in ("KRA Summary", "KPI Details", "Deduction Records"):
        workbook[name].column_dimensions["B"].width = 28
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()
