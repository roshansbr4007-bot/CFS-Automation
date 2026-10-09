import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("tasks", "0002_tasktemplate_task_template_trigger_at"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SlaRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=40)),
                ("version", models.PositiveIntegerField(default=1)),
                ("name", models.CharField(max_length=120)),
                (
                    "rule_type",
                    models.CharField(
                        choices=[
                            ("DURATION", "Fixed duration"),
                            ("END_OF_DAY", "End of the company working day"),
                            ("TRANSACTION_CUTOFF", "Transaction cutoff (Phase 7)"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "clock",
                    models.CharField(
                        choices=[
                            ("CALENDAR", "Calendar hours (24/7)"),
                            ("BUSINESS", "Business hours (calendar engine, later phase)"),
                        ],
                        default="CALENDAR",
                        max_length=10,
                    ),
                ),
                ("duration_minutes", models.PositiveIntegerField(blank=True, null=True)),
                ("warning_pct", models.PositiveSmallIntegerField(default=50)),
                ("critical_pct", models.PositiveSmallIntegerField(default=75)),
                ("overdue_pct", models.PositiveSmallIntegerField(default=100)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
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
                    "supersedes",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="sla.slarule",
                    ),
                ),
            ],
            options={
                "db_table": "sla_rule",
                "ordering": ["code", "-version"],
                "default_permissions": (),
                "permissions": [("manage_sla_rules", "Supersede SLA rules and edit SLA settings")],
                "constraints": [
                    models.UniqueConstraint(fields=("code", "version"), name="sla_rule_code_version_uniq"),
                    models.UniqueConstraint(
                        condition=models.Q(("is_active", True)),
                        fields=("code",),
                        name="sla_rule_one_active_version",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("rule_type", "DURATION"), _negated=True)
                        | models.Q(("duration_minutes__gt", 0)),
                        name="sla_rule_duration_needs_minutes",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("warning_pct__gt", 0))
                        & models.Q(("warning_pct__lt", models.F("critical_pct")))
                        & models.Q(("critical_pct__lt", models.F("overdue_pct"))),
                        name="sla_rule_thresholds_ordered",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="SlaSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("company_work_end", models.TimeField(blank=True, null=True)),
                ("login_fallback_time", models.TimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "sla_setting", "default_permissions": ()},
        ),
        migrations.CreateModel(
            name="TaskSla",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("ACK", "Acknowledgment"), ("RESOLUTION", "Resolution")], max_length=10)),
                ("rule_snapshot", models.JSONField()),
                (
                    "trigger",
                    models.CharField(
                        choices=[
                            ("ASSIGNMENT", "Assignment"),
                            ("LOGIN", "Employee login"),
                            ("FIXED_TIME", "Fixed time"),
                            ("EVENT", "Event"),
                            ("DEPENDENCY", "Dependency completion"),
                        ],
                        max_length=12,
                    ),
                ),
                ("start_at", models.DateTimeField(blank=True, null=True)),
                ("due_at", models.DateTimeField(blank=True, null=True)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("NOT_STARTED", "Not started"),
                            ("ON_TRACK", "On track"),
                            ("WARNING", "Warning"),
                            ("CRITICAL", "Critical"),
                            ("OVERDUE", "Overdue"),
                        ],
                        default="NOT_STARTED",
                        max_length=12,
                    ),
                ),
                ("warning_at", models.DateTimeField(blank=True, null=True)),
                ("critical_at", models.DateTimeField(blank=True, null=True)),
                ("overdue_at", models.DateTimeField(blank=True, null=True)),
                ("stopped_at", models.DateTimeField(blank=True, null=True)),
                (
                    "stop_reason",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("ACKNOWLEDGED", "Acknowledged"),
                            ("COMPLETED", "Task completed"),
                            ("CANCELLED", "Task cancelled"),
                            ("REASSIGNED", "Task reassigned"),
                            ("NOT_REQUIRED", "Acknowledgment no longer required"),
                        ],
                        max_length=14,
                    ),
                ),
                (
                    "outcome",
                    models.CharField(blank=True, choices=[("MET", "Met"), ("MISSED", "Missed")], max_length=8, null=True),
                ),
                ("is_current", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "assignment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="tasks.taskassignment",
                    ),
                ),
                (
                    "rule",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="clocks", to="sla.slarule"
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="sla_clocks", to="tasks.task"
                    ),
                ),
            ],
            options={
                "db_table": "task_sla",
                "ordering": ["created_at", "id"],
                "default_permissions": (),
                "indexes": [models.Index(fields=["stopped_at", "start_at"], name="sla_running_idx")],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("is_current", True)),
                        fields=("task", "kind"),
                        name="sla_one_current_clock",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("due_at__isnull", True), ("start_at__isnull", True))
                        | models.Q(("due_at__isnull", False), ("start_at__isnull", False)),
                        name="sla_start_and_due_together",
                    ),
                ],
            },
        ),
    ]
