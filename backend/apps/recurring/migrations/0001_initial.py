import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

PRIORITIES = [("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High"), ("URGENT", "Urgent")]


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("org", "0002_seed_departments"),
        ("tasks", "0008_seed_task_categories"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Responsibility",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=40, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("description", models.TextField(blank=True)),
                ("priority", models.CharField(choices=PRIORITIES, default="MEDIUM", max_length=8)),
                ("is_active", models.BooleanField(default=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "category",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="responsibilities",
                        to="tasks.taskcategory",
                    ),
                ),
                (
                    "department",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="responsibilities",
                        to="org.department",
                    ),
                ),
                (
                    "template",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="responsibilities",
                        to="tasks.tasktemplate",
                    ),
                ),
            ],
            options={
                "db_table": "recurring_responsibility",
                "ordering": ["name", "id"],
                "default_permissions": (),
                "permissions": [
                    ("view_all_responsibilities", "See every responsibility and its schedules"),
                    ("manage_team_responsibilities", "Manage responsibilities of one's own department"),
                    ("manage_all_responsibilities", "Manage every responsibility"),
                    ("manage_schedules", "Create and edit recurring schedules"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ResponsibilityOwner",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("effective_from", models.DateField()),
                ("effective_to", models.DateField(blank=True, null=True)),
                ("note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "assigned_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL
                    ),
                ),
                (
                    "employee",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="owned_responsibilities",
                        to="org.employee",
                    ),
                ),
                (
                    "responsibility",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="owners",
                        to="recurring.responsibility",
                    ),
                ),
            ],
            options={
                "db_table": "recurring_responsibility_owner",
                "ordering": ["effective_from", "id"],
                "default_permissions": (),
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("effective_to__isnull", True)),
                        fields=("responsibility",),
                        name="recurring_one_open_owner",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("effective_to__isnull", True))
                        | models.Q(("effective_to__gte", models.F("effective_from"))),
                        name="recurring_owner_period_valid",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="RecurringSchedule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                (
                    "frequency",
                    models.CharField(
                        choices=[("DAILY", "Daily (working days)"), ("MONTHLY", "Monthly")], max_length=8
                    ),
                ),
                ("run_time", models.TimeField()),
                ("day_of_month", models.PositiveSmallIntegerField(blank=True, null=True)),
                (
                    "non_working_day_policy",
                    models.CharField(
                        choices=[
                            ("SKIP", "Skip the occurrence"),
                            ("NEXT_WORKING_DAY", "Move to the next working day"),
                            ("PREVIOUS_WORKING_DAY", "Move to the previous working day"),
                        ],
                        default="SKIP",
                        max_length=20,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("effective_from", models.DateField()),
                ("effective_to", models.DateField(blank=True, null=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "responsibility",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="schedules",
                        to="recurring.responsibility",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "recurring_schedule",
                "ordering": ["responsibility_id", "id"],
                "default_permissions": (),
                "constraints": [
                    models.CheckConstraint(
                        condition=(models.Q(("frequency", "DAILY")) & models.Q(("day_of_month__isnull", True)))
                        | (
                            models.Q(("frequency", "MONTHLY"))
                            & models.Q(("day_of_month__gte", 1))
                            & models.Q(("day_of_month__lte", 28))
                        ),
                        name="recurring_schedule_day_of_month_chk",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("effective_to__isnull", True))
                        | models.Q(("effective_to__gte", models.F("effective_from"))),
                        name="recurring_schedule_period_valid",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ScheduleOccurrence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("occurrence_date", models.DateField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("GENERATED", "Generated"),
                            ("SKIPPED", "Skipped (no responsible employee)"),
                            ("MISSED", "Missed (scheduler unavailable)"),
                            ("FAILED", "Failed"),
                        ],
                        max_length=10,
                    ),
                ),
                ("generated_at", models.DateTimeField(blank=True, null=True)),
                ("detail", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "assignee",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="org.employee",
                    ),
                ),
                (
                    "schedule",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="occurrences",
                        to="recurring.recurringschedule",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="tasks.task",
                    ),
                ),
            ],
            options={
                "db_table": "recurring_occurrence",
                "ordering": ["-occurrence_date", "-id"],
                "default_permissions": (),
                "indexes": [models.Index(fields=["status", "occurrence_date"], name="recurring_occ_status_idx")],
                "constraints": [
                    models.UniqueConstraint(fields=("schedule", "occurrence_date"), name="recurring_occurrence_uniq")
                ],
            },
        ),
    ]
