"""Seeds the approved Operations responsibilities and their schedules, and the CFS Scheduler
identity that creates system-generated tasks.

No owner is seeded: Admin assigns each responsibility to an employee. Until then each
occurrence is SKIPPED, audited and reported - never given to a guessed employee.
Schedules start on the day this migration runs, so no backlog is created for earlier days.
"""

from datetime import time

from django.contrib.auth.hashers import make_password
from django.db import migrations
from django.utils import timezone

SCHEDULER_EMAIL = "scheduler@cfs.system"
RESPONSIBILITIES = [
    # code, name, template, frequency, day_of_month, policy
    ("FEED_UPLOAD", "Feed Upload", "FEED_UPLOAD", "DAILY", None, "SKIP"),
    ("MAIL_CHECKING", "Mail Checking", "MAIL_CHECKING", "DAILY", None, "SKIP"),
    ("SIP_STP_SWITCH_CHECKING", "SIP/STP/Switch Checking", "SIP_STP_CHECK", "DAILY", None, "SKIP"),
    ("BROKERAGE_CALCULATION", "Brokerage Calculation", "BROKERAGE_CALCULATION", "MONTHLY", 20,
     "NEXT_WORKING_DAY"),
]


def seed(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Department = apps.get_model("org", "Department")
    TaskCategory = apps.get_model("tasks", "TaskCategory")
    TaskTemplate = apps.get_model("tasks", "TaskTemplate")
    Responsibility = apps.get_model("recurring", "Responsibility")
    RecurringSchedule = apps.get_model("recurring", "RecurringSchedule")

    User.objects.get_or_create(
        email=SCHEDULER_EMAIL,
        defaults={
            "first_name": "CFS",
            "last_name": "Scheduler",
            "is_active": False,  # cannot sign in; it only authors system-generated tasks
            "password": make_password(None),
        },
    )
    ops = Department.objects.get(code="OPS")
    category, _ = TaskCategory.objects.get_or_create(
        code="OPERATIONS", defaults={"name": "Operations"}
    )
    today = timezone.localdate()  # business date (TIME_ZONE is Asia/Kolkata)
    for code, name, template_code, frequency, day, policy in RESPONSIBILITIES:
        responsibility, created = Responsibility.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "department": ops,
                "category": category,
                "template": TaskTemplate.objects.get(code=template_code),
            },
        )
        if created:
            RecurringSchedule.objects.create(
                responsibility=responsibility,
                title=name,
                frequency=frequency,
                run_time=time(10, 0),
                day_of_month=day,
                non_working_day_policy=policy,
                effective_from=today,
            )


def unseed(apps, schema_editor):
    Responsibility = apps.get_model("recurring", "Responsibility")
    RecurringSchedule = apps.get_model("recurring", "RecurringSchedule")
    codes = [r[0] for r in RESPONSIBILITIES]
    RecurringSchedule.objects.filter(
        responsibility__code__in=codes, occurrences__isnull=True
    ).delete()
    Responsibility.objects.filter(code__in=codes, schedules__isnull=True, owners__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("recurring", "0001_initial"),
        ("tasks", "0010_phase5_template_rules"),
        ("accounts", "0001_initial"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
