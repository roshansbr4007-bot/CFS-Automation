"""Approved Phase 5 rule: Mail Checking is 10:00 -> 12:00 (2 hours), replacing the earlier
same-day/end-of-day rule. MAIL_SAME_DAY is left in place (rules are never deleted)."""

from django.db import migrations


def seed(apps, schema_editor):
    SlaRule = apps.get_model("sla", "SlaRule")
    if not SlaRule.objects.filter(code="MAIL_CHECKING_2H").exists():
        SlaRule.objects.create(
            code="MAIL_CHECKING_2H",
            version=1,
            name="Mail checking",
            rule_type="DURATION",
            clock="CALENDAR",
            duration_minutes=120,
        )


def unseed(apps, schema_editor):
    SlaRule = apps.get_model("sla", "SlaRule")
    SlaRule.objects.filter(code="MAIL_CHECKING_2H", clocks__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("sla", "0002_seed_rules")]

    operations = [migrations.RunPython(seed, unseed)]
