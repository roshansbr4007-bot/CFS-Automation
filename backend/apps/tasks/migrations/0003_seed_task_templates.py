"""Seeds the approved task types (locked Phase 6 decisions). Admin-changeable data, not code.

All in OPS; verification not required; acknowledgment required only for Broker Mapping.
Safe to run more than once; existing rows are never overwritten.
"""

from datetime import time

from django.db import migrations

TEMPLATES = [
    # code, name, rule code, trigger, fixed_time, acknowledgment_required
    ("FEED_UPLOAD", "Feed Upload", "FEED_UPLOAD_2H", "FIXED_TIME", time(10, 0), False),
    ("BIRTHDAY_WISHES", "Birthday Wishes", "BIRTHDAY_2H", "LOGIN", None, False),
    ("SIP_STP_CHECK", "SIP/STP Check", "SIP_STP_CHECK_3H", "LOGIN", None, False),
    ("BROKER_MAPPING", "Broker Mapping", "BROKER_MAPPING_24H", "ASSIGNMENT", None, True),
    ("RECONCILIATION", "Reconciliation", "RECON_24H", "DEPENDENCY", None, False),
    ("MAIL_CHECKING", "Mail Checking", "MAIL_SAME_DAY", "LOGIN", None, False),
    ("SIP_FAILURE", "SIP Failure", "SIP_FAILURE_24H", "EVENT", None, False),
]


def seed(apps, schema_editor):
    Department = apps.get_model("org", "Department")
    TaskTemplate = apps.get_model("tasks", "TaskTemplate")
    ops = Department.objects.get(code="OPS")
    for code, name, rule, trigger, fixed, ack in TEMPLATES:
        TaskTemplate.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "department": ops,
                "resolution_rule_code": rule,
                "trigger": trigger,
                "fixed_time": fixed,
                "acknowledgment_required": ack,
                "verification_required": False,
            },
        )


def unseed(apps, schema_editor):
    TaskTemplate = apps.get_model("tasks", "TaskTemplate")
    TaskTemplate.objects.filter(code__in=[t[0] for t in TEMPLATES], tasks__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("tasks", "0002_tasktemplate_task_template_trigger_at")]

    operations = [migrations.RunPython(seed, unseed)]
