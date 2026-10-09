"""Seeds the approved Operations KPIs and the Operations weight version v1 (total 10.0).

- Deterministic: v1 is ACTIVE from a fixed date (EFFECTIVE_FROM), not from the day the migration
  happens to run, so every database gets the same configuration.
- Assigned to NOBODY (approved P6): Admin assigns it to employees.
- Idempotent: existing KPIs or an existing OPERATIONS v1 are never modified.
- Reversing is a no-op: configuration that people may have changed or used is never deleted
  (the 0001 reverse drops the tables if the whole app is rolled back).
Codes are stable; ids are not relied on.
"""

from datetime import date
from decimal import Decimal

from django.db import migrations
from django.utils import timezone

EFFECTIVE_FROM = date(2026, 1, 1)
KPIS = [
    # code, name, source, weight
    ("ACCURACY", "Accuracy", "MANAGER", "3.00"),
    ("TIMELINESS", "Timeliness", "SYSTEM", "2.00"),
    ("CLIENT_SERVICING", "Client Servicing", "MANAGER", "1.50"),
    ("FINANCIAL_ACCURACY", "Financial Accuracy", "MANAGER", "1.50"),
    ("DATA_SYSTEM", "Data/System", "MANAGER", "1.00"),
    ("COMPLIANCE", "Compliance", "MANAGER", "1.00"),
]


def seed(apps, schema_editor):
    KPI = apps.get_model("performance", "KPI")
    Version = apps.get_model("performance", "KPIWeightVersion")
    Weight = apps.get_model("performance", "KPIWeight")
    kpis = {}
    for code, name, source, _ in KPIS:
        kpis[code], _ = KPI.objects.get_or_create(
            code=code, defaults={"name": name, "score_source": source}
        )
    assert sum(Decimal(w) for *_, w in KPIS) == Decimal("10.00")
    version, created = Version.objects.get_or_create(
        configuration="OPERATIONS",
        version=1,
        defaults={
            "name": "Operations KPIs",
            "effective_from": EFFECTIVE_FROM,
            "status": "ACTIVE",
            "activated_at": timezone.now(),
        },
    )
    if created:
        for code, _, _, weight in KPIS:
            Weight.objects.create(weight_version=version, kpi=kpis[code], weight=Decimal(weight))


class Migration(migrations.Migration):
    dependencies = [("performance", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
