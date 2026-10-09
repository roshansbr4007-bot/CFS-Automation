"""Seeds the Phase 7.1 KRA configuration as DRAFTS. Nothing is activated here.

- KRA_BENCHMARK v1 (scoring rule, DRAFT): 100 -> 100%, 85 -> 80%, 70 -> 60%, 50 -> 40%;
  below 50% -> 0% (configurable).
- KRA_BANDS v1 (band scheme, DRAFT): High Performer >= 9.0, Consistent Performer >= 7.5,
  Needs Improvement >= 6.0, Performance Concern >= 0 (lower bounds, 10-point scale).
- OPERATIONS_KRA v1 (KRA_POINTS plan, DRAFT): six lines on the existing KPI master records
  (never renamed; KRA names are line display names where they differ), weights
  3.0 / 2.0 / 1.5 / 1.5 / 1.0 / 1.0, task credits 1.00 / 0.25 / 0.00, no components (HR maps
  responsibilities), and only the KRA-defined deduction categories. Scope, stacking, cap,
  priority, ceiling bands and the stacking method are left for HR; activation refuses them
  until complete.
- The existing OPERATIONS v1 (LEGACY_WEIGHTED, ACTIVE) and the KPI master are NOT touched.
- Effective dates are a draft placeholder (1 Nov 2026) that HR sets before Admin activates;
  activation requires a future date.
- Idempotent: rows that already exist are never modified. Reversing is a no-op.
"""

from datetime import date
from decimal import Decimal

from django.db import migrations

DRAFT_FROM = date(2026, 11, 1)
BENCHMARK_STEPS = [("100", "100"), ("85", "80"), ("70", "60"), ("50", "40")]
BANDS = [
    ("High Performer", "9.0", 1),
    ("Consistent Performer", "7.5", 2),
    ("Needs Improvement", "6.0", 3),
    ("Performance Concern", "0", 4),
]
LINES = [
    # KPI code, weight (max points), display name ("" = the KPI's own name)
    ("ACCURACY", "3.00", ""),
    ("TIMELINESS", "2.00", "Timeliness & Task Discipline"),
    ("CLIENT_SERVICING", "1.50", ""),
    ("FINANCIAL_ACCURACY", "1.50", ""),
    ("DATA_SYSTEM", "1.00", "Data & System Management"),
    ("COMPLIANCE", "1.00", "Compliance & Documentation"),
]
DEDUCTIONS = [
    # code, name, kind, min %, max %
    ("MISSED_RECONCILIATION", "Missed reconciliation", "PERCENT_RANGE", "20", "50"),
    ("DELAYED_SYSTEM_UPDATE", "Delayed system update", "PERCENT_RANGE", "10", "30"),
    ("SERVICE_DELAY_BEYOND_TAT", "Service delay beyond TAT", "PERCENT_RANGE", "10", "25"),
    ("DATA_INCONSISTENCY", "Data inconsistency", "PERCENT_RANGE", "20", "40"),
    ("TRANSACTION_ERROR", "Transaction error", "BAND_CEILING", None, None),
    ("REVENUE_LEAKAGE", "Revenue leakage", "BAND_CEILING", None, None),
]


def seed(apps, schema_editor):
    KPI = apps.get_model("performance", "KPI")
    Version = apps.get_model("performance", "KPIWeightVersion")
    Weight = apps.get_model("performance", "KPIWeight")
    ScoringRule = apps.get_model("performance", "ScoringRule")
    Step = apps.get_model("performance", "ScoringRuleStep")
    BandScheme = apps.get_model("performance", "BandScheme")
    Band = apps.get_model("performance", "Band")
    DeductionRule = apps.get_model("performance", "DeductionRule")

    rule, created = ScoringRule.objects.get_or_create(
        code="KRA_BENCHMARK",
        version=1,
        defaults={
            "name": "KRA benchmark",
            "status": "DRAFT",
            "effective_from": DRAFT_FROM,
            "below_min_score_pct": Decimal("0"),
        },
    )
    if created:
        for minimum, score in BENCHMARK_STEPS:
            Step.objects.create(
                rule=rule, min_achievement_pct=Decimal(minimum), score_pct=Decimal(score)
            )

    scheme, created = BandScheme.objects.get_or_create(
        code="KRA_BANDS",
        version=1,
        defaults={"name": "KRA performance bands", "status": "DRAFT", "effective_from": DRAFT_FROM},
    )
    if created:
        for name, minimum, position in BANDS:
            Band.objects.create(
                scheme=scheme, name=name, min_points=Decimal(minimum), position=position
            )

    assert sum(Decimal(w) for _, w, _ in LINES) == Decimal("10.00")
    plan, created = Version.objects.get_or_create(
        configuration="OPERATIONS_KRA",
        version=1,
        defaults={
            "name": "Operations KRA",
            "effective_from": DRAFT_FROM,
            "status": "DRAFT",
            "calculation_model": "KRA_POINTS",
            "band_scheme": scheme,
            "credit_on_time": Decimal("1.00"),
            "credit_late": Decimal("0.25"),
            "credit_overdue": Decimal("0.00"),
        },
    )
    if created:
        for position, (code, weight, display_name) in enumerate(LINES, start=1):
            Weight.objects.create(
                weight_version=plan,
                kpi=KPI.objects.get(code=code),
                weight=Decimal(weight),
                display_name=display_name,
                scoring_rule=rule,
                position=position,
            )
        for code, name, kind, low, high in DEDUCTIONS:
            DeductionRule.objects.create(
                plan_version=plan,
                code=code,
                name=name,
                kind=kind,
                min_pct=Decimal(low) if low is not None else None,
                max_pct=Decimal(high) if high is not None else None,
            )


class Migration(migrations.Migration):
    dependencies = [("performance", "0003_kra_configuration_schema")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
