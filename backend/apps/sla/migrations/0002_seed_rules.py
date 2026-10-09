"""Seeds the approved v4 SLA rules (version 1, calendar clock). Admin supersedes later.

MAIL_SAME_DAY is seeded but cannot run until Admin sets company work_end (no default).
TXN_CUTOFF (transaction rule) arrives with the Transaction Calendar in Phase 7.
"""

from django.db import migrations

RULES = [
    # code, name, rule_type, duration_minutes
    ("ACK_2H", "Acknowledgment clock", "DURATION", 120),
    ("FEED_UPLOAD_2H", "Feed upload", "DURATION", 120),
    ("BIRTHDAY_2H", "Birthday wishes", "DURATION", 120),
    ("SIP_STP_CHECK_3H", "SIP/STP switch check", "DURATION", 180),
    ("BROKER_MAPPING_24H", "RM broker mapping", "DURATION", 1440),
    ("RECON_24H", "Reconciliation", "DURATION", 1440),
    ("MAIL_SAME_DAY", "Mail checking", "END_OF_DAY", None),
    ("SIP_FAILURE_24H", "SIP failure resolution", "DURATION", 1440),
]


def seed(apps, schema_editor):
    SlaRule = apps.get_model("sla", "SlaRule")
    for code, name, rule_type, minutes in RULES:
        if not SlaRule.objects.filter(code=code).exists():
            SlaRule.objects.create(
                code=code,
                version=1,
                name=name,
                rule_type=rule_type,
                clock="CALENDAR",
                duration_minutes=minutes,
            )


def unseed(apps, schema_editor):
    SlaRule = apps.get_model("sla", "SlaRule")
    SlaRule.objects.filter(code__in=[r[0] for r in RULES], clocks__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("sla", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]
