import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

CAUSES = [
    ("DEPENDENCY", "Dependency"),
    ("EMPLOYEE", "Employee"),
    ("SYSTEM", "System"),
    ("CLIENT", "Client"),
    ("OTHER", "Other"),
]


def user_fk(null=True):
    return models.ForeignKey(
        blank=null,
        null=null,
        on_delete=django.db.models.deletion.PROTECT,
        related_name="+",
        to=settings.AUTH_USER_MODEL,
    )


class Migration(migrations.Migration):
    """Phase 9 overdue cases. Creates the table only: NO historical backfill."""

    initial = True

    dependencies = [
        ("sla", "0004_prioritysla"),
        ("tasks", "0010_phase5_template_rules"),
        ("org", "0003_employeedailylogin_is_valid"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OverdueCase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("task_title", models.CharField(max_length=200)),
                ("task_assigned_at", models.DateTimeField()),
                ("priority", models.CharField(max_length=8)),
                ("category_name", models.CharField(blank=True, max_length=100)),
                ("sla_start_at", models.DateTimeField()),
                ("sla_due_at", models.DateTimeField()),
                ("sla_rule_code", models.CharField(max_length=40)),
                ("sla_rule_name", models.CharField(max_length=120)),
                ("overdue_at", models.DateTimeField()),
                ("opened_at", models.DateTimeField(auto_now_add=True)),
                (
                    "opened_via",
                    models.CharField(
                        choices=[
                            ("TICK", "SLA checker"),
                            ("COMPLETION", "Completed after the deadline"),
                            ("REPAIR", "Repair command"),
                        ],
                        max_length=10,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("OPEN", "Reason needed"),
                            ("REASON_SUBMITTED", "Reason submitted"),
                            ("REVIEWED", "Reviewed"),
                        ],
                        default="OPEN",
                        max_length=16,
                    ),
                ),
                ("reason_category", models.CharField(blank=True, choices=CAUSES, max_length=10)),
                ("explanation", models.TextField(blank=True)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("cause", models.CharField(blank=True, choices=CAUSES, max_length=10)),
                ("review_remark", models.TextField(blank=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "clock",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="overdue_case",
                        to="sla.tasksla",
                    ),
                ),
                (
                    "department",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="+", to="org.department"
                    ),
                ),
                (
                    "employee",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="overdue_cases",
                        to="org.employee",
                    ),
                ),
                ("reviewed_by", user_fk()),
                ("submitted_by", user_fk()),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="overdue_cases",
                        to="tasks.task",
                    ),
                ),
                ("task_creator", user_fk(null=False)),
            ],
            options={
                "db_table": "overdue_case",
                "ordering": ["-opened_at", "-id"],
                "default_permissions": (),
                "permissions": [
                    ("view_all_cases", "Can view every overdue case"),
                    ("review_team_cases", "Can review overdue cases of tasks in own department"),
                    ("review_all_cases", "Can review every overdue case"),
                ],
                "indexes": [models.Index(fields=["status", "department"], name="overdue_queue_idx")],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("status", "OPEN"))
                        | (~models.Q(("reason_category", "")) & models.Q(("submitted_at__isnull", False))),
                        name="overdue_reason_before_review",
                    ),
                    models.CheckConstraint(
                        condition=~models.Q(("status", "REVIEWED"))
                        | (
                            ~models.Q(("cause", ""))
                            & ~models.Q(("review_remark", ""))
                            & models.Q(("reviewed_at__isnull", False))
                        ),
                        name="overdue_review_complete",
                    ),
                ],
            },
        ),
    ]
