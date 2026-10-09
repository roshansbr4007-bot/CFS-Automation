import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

SOURCES = [
    ("SYSTEM", "System calculated"),
    ("MANAGER", "Manager review"),
    ("HYBRID", "Hybrid (reserved; not calculated in Phase 7)"),
]


def user_fk():
    return models.ForeignKey(
        blank=True,
        null=True,
        on_delete=django.db.models.deletion.PROTECT,
        related_name="+",
        to=settings.AUTH_USER_MODEL,
    )


def count():
    return models.PositiveIntegerField(default=0)


def rate():
    return models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("org", "0003_employeedailylogin_is_valid"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="KPI",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=40, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("description", models.TextField(blank=True)),
                ("score_source", models.CharField(choices=SOURCES, max_length=8)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "performance_kpi", "ordering": ["code"], "default_permissions": ()},
        ),
        migrations.CreateModel(
            name="KPIWeightVersion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("configuration", models.CharField(max_length=40)),
                ("version", models.PositiveIntegerField()),
                ("name", models.CharField(max_length=120)),
                ("effective_from", models.DateField()),
                ("effective_to", models.DateField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[("DRAFT", "Draft"), ("ACTIVE", "Active"), ("RETIRED", "Retired")],
                        default="DRAFT",
                        max_length=8,
                    ),
                ),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("activated_by", user_fk()),
                ("created_by", user_fk()),
            ],
            options={
                "db_table": "performance_weight_version",
                "ordering": ["configuration", "version"],
                "default_permissions": (),
                "constraints": [
                    models.UniqueConstraint(fields=("configuration", "version"), name="performance_version_uniq"),
                    models.CheckConstraint(
                        condition=models.Q(("effective_to__isnull", True))
                        | models.Q(("effective_to__gte", models.F("effective_from"))),
                        name="performance_version_period_valid",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="KPIWeight",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("weight", models.DecimalField(decimal_places=2, max_digits=5)),
                (
                    "kpi",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="weights", to="performance.kpi"
                    ),
                ),
                (
                    "weight_version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="weights",
                        to="performance.kpiweightversion",
                    ),
                ),
            ],
            options={
                "db_table": "performance_kpi_weight",
                "ordering": ["weight_version_id", "kpi__code"],
                "default_permissions": (),
                "constraints": [
                    models.UniqueConstraint(fields=("weight_version", "kpi"), name="performance_weight_uniq"),
                    models.CheckConstraint(
                        condition=models.Q(("weight__gte", 0)), name="performance_weight_non_negative"
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="EmployeeKPIAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("effective_from", models.DateField()),
                ("effective_to", models.DateField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("assigned_by", user_fk()),
                (
                    "employee",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="kpi_assignments",
                        to="org.employee",
                    ),
                ),
                (
                    "weight_version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="assignments",
                        to="performance.kpiweightversion",
                    ),
                ),
            ],
            options={
                "db_table": "performance_kpi_assignment",
                "ordering": ["employee_id", "effective_from", "id"],
                "default_permissions": (),
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("effective_to__isnull", True)),
                        fields=("employee",),
                        name="performance_one_open_assignment",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("effective_to__isnull", True))
                        | models.Q(("effective_to__gte", models.F("effective_from"))),
                        name="performance_assignment_period_valid",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="MonthlyPerformance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("year", models.PositiveSmallIntegerField()),
                ("month", models.PositiveSmallIntegerField()),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("DRAFT", "Draft"),
                            ("CALCULATED", "Calculated"),
                            ("UNDER_REVIEW", "Under review"),
                            ("FINALIZED", "Finalized"),
                        ],
                        default="DRAFT",
                        max_length=12,
                    ),
                ),
                ("assigned_tasks", count()),
                ("scheduled_tasks", count()),
                ("manual_tasks", count()),
                ("completed_tasks", count()),
                ("pending_tasks", count()),
                ("overdue_tasks", count()),
                ("sla_met_tasks", count()),
                ("sla_breached_tasks", count()),
                ("on_time_completed_tasks", count()),
                ("completed_sla_tasks", count()),
                ("completion_rate", rate()),
                ("sla_compliance_rate", rate()),
                ("overall_score", rate()),
                ("performance_band", models.CharField(blank=True, max_length=20)),
                ("manager_remark", models.TextField(blank=True)),
                ("calculated_at", models.DateTimeField(blank=True, null=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("finalized_at", models.DateTimeField(blank=True, null=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("calculated_by", user_fk()),
                (
                    "employee",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="monthly_performance",
                        to="org.employee",
                    ),
                ),
                ("finalized_by", user_fk()),
                ("reviewed_by", user_fk()),
                (
                    "weight_version",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="performance.kpiweightversion",
                    ),
                ),
            ],
            options={
                "db_table": "performance_monthly",
                "ordering": ["-year", "-month", "employee_id"],
                "default_permissions": (),
                "constraints": [
                    models.UniqueConstraint(fields=("employee", "year", "month"), name="performance_monthly_uniq"),
                    models.CheckConstraint(
                        condition=models.Q(("month__gte", 1)) & models.Q(("month__lte", 12)),
                        name="performance_month_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("overall_score__isnull", True))
                        | (models.Q(("overall_score__gte", 0)) & models.Q(("overall_score__lte", 100))),
                        name="performance_overall_in_range",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="MonthlyKPIScore",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("weight", models.DecimalField(decimal_places=2, max_digits=5)),
                ("score", models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)),
                ("source", models.CharField(choices=SOURCES, max_length=8)),
                ("manager_remark", models.TextField(blank=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "kpi",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="+", to="performance.kpi"
                    ),
                ),
                (
                    "monthly_performance",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="kpi_scores",
                        to="performance.monthlyperformance",
                    ),
                ),
                ("reviewed_by", user_fk()),
            ],
            options={
                "db_table": "performance_monthly_kpi_score",
                "ordering": ["monthly_performance_id", "kpi__code"],
                "default_permissions": (),
                "constraints": [
                    models.UniqueConstraint(fields=("monthly_performance", "kpi"), name="performance_score_uniq"),
                    models.CheckConstraint(
                        condition=models.Q(("score__isnull", True))
                        | (models.Q(("score__gte", 0)) & models.Q(("score__lte", 100))),
                        name="performance_score_in_range",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("weight__gte", 0)), name="performance_score_weight_non_negative"
                    ),
                ],
            },
        ),
    ]
