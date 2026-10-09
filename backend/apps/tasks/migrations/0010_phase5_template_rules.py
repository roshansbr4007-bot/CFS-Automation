"""Approved Phase 5 rules for the task types used by responsibilities:

- Mail Checking: fixed time 10:00, 2 hours (MAIL_CHECKING_2H) - overrides login / end-of-day.
- SIP/STP/Switch Checking: fixed time 10:00, 3 hours (rule unchanged) - overrides login.
- Brokerage Calculation: new task type, no resolution SLA (no rule).
"""

from datetime import time

from django.db import migrations

OLD = {
    "MAIL_CHECKING": {"trigger": "LOGIN", "fixed_time": None, "resolution_rule_code": "MAIL_SAME_DAY"},
    "SIP_STP_CHECK": {"trigger": "LOGIN", "fixed_time": None, "resolution_rule_code": "SIP_STP_CHECK_3H"},
}
NEW = {
    "MAIL_CHECKING": {"trigger": "FIXED_TIME", "fixed_time": time(10, 0), "resolution_rule_code": "MAIL_CHECKING_2H"},
    "SIP_STP_CHECK": {"trigger": "FIXED_TIME", "fixed_time": time(10, 0), "resolution_rule_code": "SIP_STP_CHECK_3H"},
}


def apply(apps, schema_editor):
    TaskTemplate = apps.get_model("tasks", "TaskTemplate")
    Department = apps.get_model("org", "Department")
    for code, values in NEW.items():
        TaskTemplate.objects.filter(code=code).update(**values)
    TaskTemplate.objects.get_or_create(
        code="BROKERAGE_CALCULATION",
        defaults={
            "name": "Brokerage Calculation",
            "department": Department.objects.get(code="OPS"),
            "resolution_rule_code": "",  # no resolution SLA (approved)
            "trigger": "ASSIGNMENT",
            "acknowledgment_required": False,
            "verification_required": False,
        },
    )


def revert(apps, schema_editor):
    TaskTemplate = apps.get_model("tasks", "TaskTemplate")
    for code, values in OLD.items():
        TaskTemplate.objects.filter(code=code).update(**values)
    TaskTemplate.objects.filter(code="BROKERAGE_CALCULATION", tasks__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0009_task_source_responsibility_schedule"),
        ("sla", "0003_seed_mail_checking_2h"),
    ]

    operations = [migrations.RunPython(apply, revert)]
