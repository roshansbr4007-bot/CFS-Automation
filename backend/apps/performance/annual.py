"""Phase 7.3 annual KRA figures (approved E18, original section 16). Computed on demand from
the stored months; nothing is stored, so the figures always follow the finalized history.

A month counts when it is FINALIZED, is a KRA (points) month of the calendar year, and has an
applicable maximum above 0. Missing, provisional, under-review, legacy (0-100 scale) and
all-N/A months are NOT counted - never as 0.
    annual total   = sum of the counted months' final totals (at most 12 x 10 = 120)
    annual average = annual total / number of counted months (none when no month counts)
"""

from decimal import ROUND_HALF_UP, Decimal

from .models import CalculationModel, MonthlyPerformance, PerformanceStatus

SIX = Decimal("0.000001")
MAXIMUM_TOTAL = Decimal("120")
NO_RECORD = "NO_RECORD"
NOT_FINALIZED = "NOT_FINALIZED"
LEGACY = "LEGACY_SCALE"
NOT_APPLICABLE = "NOTHING_APPLICABLE"


def _exclusion(performance) -> str | None:
    if performance.calculation_model != CalculationModel.KRA_POINTS:
        return LEGACY
    if performance.status != PerformanceStatus.FINALIZED:
        return NOT_FINALIZED
    if not performance.max_points_applicable or performance.max_points_applicable <= 0:
        return NOT_APPLICABLE
    return None


def annual_summary(employee, year: int) -> dict:
    records = {
        p.month: p
        for p in MonthlyPerformance.objects.filter(employee=employee, year=year)
    }
    counted, excluded = [], []
    for month in range(1, 13):
        performance = records.get(month)
        reason = NO_RECORD if performance is None else _exclusion(performance)
        if reason is None:
            counted.append(performance)
        else:
            excluded.append({"month": month, "reason": reason,
                             "status": performance.status if performance else ""})
    total = sum((p.final_total for p in counted), Decimal("0"))
    average = (total / len(counted)).quantize(SIX, ROUND_HALF_UP) if counted else None
    return {
        "employee": employee.pk,
        "year": year,
        "applicable_months": len(counted),
        "annual_total": total if counted else None,
        "annual_average": average,
        "maximum_total": MAXIMUM_TOTAL,
        "months": [
            {"id": p.pk, "month": p.month, "final_total": p.final_total,
             "max_points_applicable": p.max_points_applicable, "band": p.band_name}
            for p in counted
        ],
        "excluded": excluded,
    }
