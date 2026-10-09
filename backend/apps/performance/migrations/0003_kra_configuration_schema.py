"""Phase 7.1 KRA configuration schema (additive only).

- KPIWeightVersion gains `calculation_model` (existing rows: LEGACY_WEIGHTED) and the KRA plan
  fields; KPIWeight gains the KRA line fields; EmployeeKPIAssignment gains `reason`.
- MonthlyPerformance / MonthlyKPIScore gain KRA columns; legacy columns keep their meaning.
- 14 new tables: scoring rules (+ steps), band schemes (+ bands), plan defaults, components,
  deduction rules and the monthly KRA result / audit structures.
- The five performance permissions are declared (granted by accounts 0010).
No existing row is changed except for receiving the new columns' defaults.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import F, Q

STATUS = [("DRAFT", "Draft"), ("ACTIVE", "Active"), ("RETIRED", "Retired")]
MODELS = [
    ("LEGACY_WEIGHTED", "Legacy weighted score (0-100)"),
    ("KRA_POINTS", "KRA points (0-10)"),
]
STACKING_METHODS = [
    ("ADDITIVE", "Additive"),
    ("SEQUENTIAL", "Sequential (each applies to the remainder)"),
]


def user_fk():
    return models.ForeignKey(
        blank=True,
        null=True,
        on_delete=django.db.models.deletion.PROTECT,
        related_name="+",
        to=settings.AUTH_USER_MODEL,
    )


def required_user_fk():
    return models.ForeignKey(
        on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL
    )


def points(**extra):
    return models.DecimalField(decimal_places=6, max_digits=12, **extra)


def percent(**extra):
    return models.DecimalField(decimal_places=6, max_digits=9, **extra)


def config_percent(**extra):
    return models.DecimalField(decimal_places=2, max_digits=5, **extra)


def credit(**extra):
    return models.DecimalField(decimal_places=2, max_digits=5, **extra)


def share(**extra):
    return models.DecimalField(decimal_places=6, max_digits=9, **extra)


def credit_range(field):
    return models.CheckConstraint(
        condition=Q(**{f"{field}__isnull": True})
        | (Q(**{f"{field}__gte": 0}) & Q(**{f"{field}__lte": 1})),
        name=f"performance_version_{field}_range",
    )


def period_valid(name):
    return models.CheckConstraint(
        condition=Q(effective_to__isnull=True) | Q(effective_to__gte=F("effective_from")),
        name=name,
    )


def config_header_fields():
    """code / version / name / status / effective period / lifecycle trail."""
    return [
        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("code", models.CharField(max_length=40)),
        ("version", models.PositiveIntegerField()),
        ("name", models.CharField(max_length=120)),
        ("status", models.CharField(choices=STATUS, default="DRAFT", max_length=8)),
        ("effective_from", models.DateField()),
        ("effective_to", models.DateField(blank=True, null=True)),
    ]


def config_trail_fields():
    return [
        ("created_by", user_fk()),
        ("created_at", models.DateTimeField(auto_now_add=True)),
        ("activated_by", user_fk()),
        ("activated_at", models.DateTimeField(blank=True, null=True)),
        ("retired_by", user_fk()),
        ("retired_at", models.DateTimeField(blank=True, null=True)),
        ("retire_reason", models.TextField(blank=True, default="")),
    ]


class Migration(migrations.Migration):
    dependencies = [
        ("performance", "0002_seed_operations_kpis"),
        ("org", "0003_employeedailylogin_is_valid"),
        ("recurring", "0005_responsibility_deadline"),
        ("tasks", "0012_reconciliation_prerequisite"),
        ("auth", "0012_alter_user_first_name_max_length"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # --- scoring rules and band schemes --------------------------------------------------
        migrations.CreateModel(
            name="ScoringRule",
            fields=[
                *config_header_fields(),
                ("below_min_score_pct", config_percent(blank=True, null=True)),
                *config_trail_fields(),
            ],
            options={
                "db_table": "performance_scoring_rule",
                "ordering": ["code", "version"],
                "default_permissions": (),
                "constraints": [
                    models.UniqueConstraint(fields=("code", "version"), name="performance_rule_uniq"),
                    period_valid("performance_rule_period_valid"),
                    models.CheckConstraint(
                        condition=Q(below_min_score_pct__isnull=True)
                        | (Q(below_min_score_pct__gte=0) & Q(below_min_score_pct__lte=100)),
                        name="performance_rule_below_min_range",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ScoringRuleStep",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("min_achievement_pct", config_percent()),
                ("score_pct", config_percent()),
                (
                    "rule",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="steps",
                        to="performance.scoringrule",
                    ),
                ),
            ],
            options={
                "db_table": "performance_scoring_rule_step",
                "ordering": ["rule_id", "-min_achievement_pct"],
                "default_permissions": (),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("rule", "min_achievement_pct"), name="performance_rule_step_uniq"
                    ),
                    models.CheckConstraint(
                        condition=Q(min_achievement_pct__gte=0) & Q(min_achievement_pct__lte=100)
                        & Q(score_pct__gte=0) & Q(score_pct__lte=100),
                        name="performance_rule_step_range",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="BandScheme",
            fields=[*config_header_fields(), *config_trail_fields()],
            options={
                "db_table": "performance_band_scheme",
                "ordering": ["code", "version"],
                "default_permissions": (),
                "constraints": [
                    models.UniqueConstraint(fields=("code", "version"), name="performance_scheme_uniq"),
                    period_valid("performance_scheme_period_valid"),
                ],
            },
        ),
        migrations.CreateModel(
            name="Band",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=60)),
                ("min_points", models.DecimalField(decimal_places=2, max_digits=5)),
                ("position", models.PositiveSmallIntegerField(default=0)),
                (
                    "scheme",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="bands",
                        to="performance.bandscheme",
                    ),
                ),
            ],
            options={
                "db_table": "performance_band",
                "ordering": ["scheme_id", "-min_points"],
                "default_permissions": (),
                "constraints": [
                    models.UniqueConstraint(fields=("scheme", "name"), name="performance_band_name_uniq"),
                    models.UniqueConstraint(
                        fields=("scheme", "min_points"), name="performance_band_min_uniq"
                    ),
                    models.CheckConstraint(
                        condition=Q(min_points__gte=0) & Q(min_points__lte=10),
                        name="performance_band_min_range",
                    ),
                ],
            },
        ),
        # --- KPIWeightVersion: KRA plan version fields and the performance permissions --------
        migrations.AlterModelOptions(
            name="kpiweightversion",
            options={
                "ordering": ["configuration", "version"],
                "default_permissions": (),
                "permissions": [
                    ("configure_kpis", "Prepare KPI plan drafts, plan defaults and employee overrides"),
                    ("approve_kpi_config", "Activate and retire KPI configuration"),
                    ("manage_performance", "Calculate, adjust and review monthly performance"),
                    ("finalize_performance", "Finalize monthly performance"),
                    ("reopen_performance", "Reopen finalized monthly performance"),
                ],
            },
        ),
        migrations.AddField(
            model_name="kpiweightversion",
            name="calculation_model",
            field=models.CharField(choices=MODELS, default="LEGACY_WEIGHTED", max_length=16),
        ),
        migrations.AddField(
            model_name="kpiweightversion",
            name="band_scheme",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="performance.bandscheme",
            ),
        ),
        migrations.AddField(
            model_name="kpiweightversion", name="credit_on_time", field=credit(blank=True, null=True)
        ),
        migrations.AddField(
            model_name="kpiweightversion", name="credit_late", field=credit(blank=True, null=True)
        ),
        migrations.AddField(
            model_name="kpiweightversion", name="credit_overdue", field=credit(blank=True, null=True)
        ),
        migrations.AddField(
            model_name="kpiweightversion",
            name="deduction_stacking_method",
            field=models.CharField(blank=True, choices=STACKING_METHODS, default="", max_length=10),
        ),
        migrations.AddField(model_name="kpiweightversion", name="retired_by", field=user_fk()),
        migrations.AddField(
            model_name="kpiweightversion",
            name="retired_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="kpiweightversion",
            name="retire_reason",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddConstraint(model_name="kpiweightversion", constraint=credit_range("credit_on_time")),
        migrations.AddConstraint(model_name="kpiweightversion", constraint=credit_range("credit_late")),
        migrations.AddConstraint(model_name="kpiweightversion", constraint=credit_range("credit_overdue")),
        # --- KPIWeight: KRA plan line fields -----------------------------------------------
        migrations.AddField(
            model_name="kpiweight",
            name="display_name",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="kpiweight",
            name="scoring_rule",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="performance.scoringrule",
            ),
        ),
        migrations.AddField(
            model_name="kpiweight",
            name="position",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        # --- EmployeeKPIAssignment: HR override reason ---------------------------------------
        migrations.AddField(
            model_name="employeekpiassignment",
            name="reason",
            field=models.TextField(blank=True, default=""),
        ),
        # --- MonthlyPerformance: KRA record structure ----------------------------------------
        migrations.AddField(
            model_name="monthlyperformance",
            name="calculation_model",
            field=models.CharField(choices=MODELS, default="LEGACY_WEIGHTED", max_length=16),
        ),
        migrations.AddField(
            model_name="monthlyperformance",
            name="department",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="org.department",
            ),
        ),
        migrations.AddField(
            model_name="monthlyperformance",
            name="role_name",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="monthlyperformance",
            name="band_scheme",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="performance.bandscheme",
            ),
        ),
        migrations.AddField(
            model_name="monthlyperformance",
            name="band",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="performance.band",
            ),
        ),
        migrations.AddField(
            model_name="monthlyperformance",
            name="band_name",
            field=models.CharField(blank=True, default="", max_length=60),
        ),
        migrations.AddField(
            model_name="monthlyperformance",
            name="band_ceiling",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="performance.band",
            ),
        ),
        migrations.AddField(
            model_name="monthlyperformance", name="max_points_applicable", field=points(blank=True, null=True)
        ),
        migrations.AddField(model_name="monthlyperformance", name="auto_total", field=points(blank=True, null=True)),
        migrations.AddField(
            model_name="monthlyperformance", name="adjustment_total", field=points(blank=True, null=True)
        ),
        migrations.AddField(
            model_name="monthlyperformance", name="deduction_total", field=points(blank=True, null=True)
        ),
        migrations.AddField(model_name="monthlyperformance", name="final_total", field=points(blank=True, null=True)),
        migrations.AddField(
            model_name="monthlyperformance",
            name="reopen_count",
            field=models.PositiveIntegerField(default=0),
        ),
        # --- MonthlyKPIScore: KRA points per KPI ---------------------------------------------
        migrations.AddField(
            model_name="monthlykpiscore",
            name="name_snapshot",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="monthlykpiscore",
            name="scoring_rule",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="performance.scoringrule",
            ),
        ),
        migrations.AddField(
            model_name="monthlykpiscore",
            name="not_applicable",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="monthlykpiscore",
            name="na_reason",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(model_name="monthlykpiscore", name="achievement_pct", field=percent(blank=True, null=True)),
        migrations.AddField(model_name="monthlykpiscore", name="benchmark_pct", field=percent(blank=True, null=True)),
        migrations.AddField(model_name="monthlykpiscore", name="auto_points", field=points(blank=True, null=True)),
        migrations.AddField(model_name="monthlykpiscore", name="adjustment_points", field=points(default=0)),
        migrations.AddField(model_name="monthlykpiscore", name="deduction_points", field=points(default=0)),
        migrations.AddField(model_name="monthlykpiscore", name="final_points", field=points(blank=True, null=True)),
        # --- plan defaults (department + system role) ----------------------------------------
        migrations.CreateModel(
            name="KPIPlanDefault",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("configuration", models.CharField(max_length=40)),
                ("effective_from", models.DateField()),
                ("effective_to", models.DateField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", user_fk()),
                ("ended_by", user_fk()),
                (
                    "department",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="+", to="org.department"
                    ),
                ),
                (
                    "role",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="+", to="auth.group"
                    ),
                ),
            ],
            options={
                "db_table": "performance_plan_default",
                "ordering": ["department_id", "role_id", "effective_from", "id"],
                "default_permissions": (),
                "constraints": [
                    models.UniqueConstraint(
                        condition=Q(effective_to__isnull=True),
                        fields=("department", "role"),
                        name="performance_one_open_default",
                    ),
                    period_valid("performance_default_period_valid"),
                ],
            },
        ),
        # --- components ----------------------------------------------------------------------
        migrations.CreateModel(
            name="KPIComponent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.PositiveSmallIntegerField(default=0)),
                (
                    "source_type",
                    models.CharField(
                        choices=[
                            ("RESPONSIBILITY_TASKS", "Tasks of a responsibility"),
                            ("MANUAL_ENTRY", "Manual entry (scored by HR)"),
                        ],
                        max_length=24,
                    ),
                ),
                ("label", models.CharField(blank=True, default="", max_length=120)),
                ("contribution_share", share(default=1)),
                (
                    "task_scope",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("SCHEDULED", "Scheduled tasks"),
                            ("MANUAL", "Manual tasks"),
                            ("BOTH", "Scheduled and manual tasks"),
                        ],
                        default="",
                        max_length=10,
                    ),
                ),
                (
                    "manual_match",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("NONE", "No automatic rule"),
                            ("TASK_TYPE", "Same task type as the responsibility"),
                            ("CATEGORY", "Same category and department as the responsibility"),
                        ],
                        default="",
                        max_length=10,
                    ),
                ),
                (
                    "verification_policy",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("NOT_REQUIRED", "Completion counts"),
                            ("REQUIRED", "Counts only once verified"),
                        ],
                        default="",
                        max_length=12,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "plan_line",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="components",
                        to="performance.kpiweight",
                    ),
                ),
                (
                    "responsibility",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="recurring.responsibility",
                    ),
                ),
            ],
            options={
                "db_table": "performance_kpi_component",
                "ordering": ["plan_line_id", "position", "id"],
                "default_permissions": (),
                "constraints": [
                    models.UniqueConstraint(
                        condition=Q(responsibility__isnull=False),
                        fields=("plan_line", "responsibility"),
                        name="performance_component_resp_uniq",
                    ),
                    models.CheckConstraint(
                        condition=Q(contribution_share__gt=0),
                        name="performance_component_share_positive",
                    ),
                    models.CheckConstraint(
                        condition=(
                            Q(source_type="RESPONSIBILITY_TASKS") & Q(responsibility__isnull=False)
                        )
                        | (
                            Q(source_type="MANUAL_ENTRY")
                            & Q(responsibility__isnull=True)
                            & ~Q(label="")
                            & Q(task_scope="")
                            & Q(manual_match="")
                        ),
                        name="performance_component_source_chk",
                    ),
                ],
            },
        ),
        # --- deduction rules -----------------------------------------------------------------
        migrations.CreateModel(
            name="DeductionRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=40)),
                ("name", models.CharField(max_length=120)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("PERCENT_RANGE", "Percentage reduction within a range"),
                            ("BAND_CEILING", "Band ceiling (special penalty)"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "scope",
                    models.CharField(
                        blank=True,
                        choices=[("COMPONENT", "Component"), ("KPI", "KPI"), ("OVERALL", "Overall")],
                        default="",
                        max_length=10,
                    ),
                ),
                ("min_pct", config_percent(blank=True, null=True)),
                ("max_pct", config_percent(blank=True, null=True)),
                (
                    "stacking",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("STACK", "Stacks with other deductions"),
                            ("NON_STACKING", "Does not stack"),
                        ],
                        default="",
                        max_length=12,
                    ),
                ),
                ("cap_pct", config_percent(blank=True, null=True)),
                ("uncapped", models.BooleanField(default=False)),
                ("priority", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("description", models.TextField(blank=True, default="")),
                (
                    "ceiling_band",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="performance.band",
                    ),
                ),
                (
                    "plan_version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="deduction_rules",
                        to="performance.kpiweightversion",
                    ),
                ),
            ],
            options={
                "db_table": "performance_deduction_rule",
                "ordering": ["plan_version_id", "priority", "code"],
                "default_permissions": (),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("plan_version", "code"), name="performance_deduction_code_uniq"
                    ),
                    models.CheckConstraint(
                        condition=(
                            Q(kind="PERCENT_RANGE")
                            & Q(min_pct__isnull=False)
                            & Q(max_pct__isnull=False)
                            & Q(min_pct__gte=0)
                            & Q(max_pct__lte=100)
                            & Q(min_pct__lte=F("max_pct"))
                        )
                        | (Q(kind="BAND_CEILING") & Q(min_pct__isnull=True) & Q(max_pct__isnull=True)),
                        name="performance_deduction_kind_chk",
                    ),
                    models.CheckConstraint(
                        condition=Q(cap_pct__isnull=True)
                        | (Q(cap_pct__gt=0) & Q(cap_pct__lte=100) & Q(uncapped=False)),
                        name="performance_deduction_cap_chk",
                    ),
                ],
            },
        ),
        # --- monthly KRA results and audit structures ----------------------------------------
        migrations.CreateModel(
            name="MonthlyComponentResult",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("label_snapshot", models.CharField(blank=True, default="", max_length=120)),
                ("share_snapshot", share(blank=True, null=True)),
                ("normalized_share", points(blank=True, null=True)),
                ("applicable", models.BooleanField(default=True)),
                ("na_reason", models.CharField(blank=True, default="", max_length=200)),
                ("on_time_count", models.PositiveIntegerField(default=0)),
                ("late_count", models.PositiveIntegerField(default=0)),
                ("overdue_count", models.PositiveIntegerField(default=0)),
                ("excluded_count", models.PositiveIntegerField(default=0)),
                ("credit_sum", points(blank=True, null=True)),
                ("achievement_pct", percent(blank=True, null=True)),
                ("manual_achievement_pct", percent(blank=True, null=True)),
                ("entered_at", models.DateTimeField(blank=True, null=True)),
                (
                    "component",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="performance.kpicomponent",
                    ),
                ),
                ("entered_by", user_fk()),
                (
                    "kpi_score",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="component_results",
                        to="performance.monthlykpiscore",
                    ),
                ),
            ],
            options={
                "db_table": "performance_monthly_component",
                "ordering": ["kpi_score_id", "id"],
                "default_permissions": (),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("kpi_score", "component"), name="performance_component_result_uniq"
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="MonthlyTaskCredit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("task_id", models.BigIntegerField()),
                ("task_reference", models.CharField(blank=True, default="", max_length=200)),
                (
                    "outcome",
                    models.CharField(
                        choices=[
                            ("ON_TIME", "On time"),
                            ("LATE", "Late"),
                            ("OVERDUE", "Overdue or incomplete at cutoff"),
                            ("NA", "Not applicable"),
                        ],
                        max_length=8,
                    ),
                ),
                ("na_reason", models.CharField(blank=True, default="", max_length=200)),
                ("credit", credit(blank=True, null=True)),
                ("deadline_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "component_result",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="task_credits",
                        to="performance.monthlycomponentresult",
                    ),
                ),
            ],
            options={
                "db_table": "performance_monthly_task_credit",
                "ordering": ["component_result_id", "task_id"],
                "default_permissions": (),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("component_result", "task_id"), name="performance_task_credit_uniq"
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ScoreAdjustment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("before_points", points()),
                ("adjustment_points", points()),
                ("after_points", points()),
                ("reason", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", required_user_fk()),
                (
                    "kpi_score",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="adjustments",
                        to="performance.monthlykpiscore",
                    ),
                ),
            ],
            options={
                "db_table": "performance_score_adjustment",
                "ordering": ["kpi_score_id", "created_at", "id"],
                "default_permissions": (),
                "constraints": [
                    models.CheckConstraint(condition=~Q(reason=""), name="performance_adjustment_reason_chk"),
                ],
            },
        ),
        migrations.CreateModel(
            name="DeductionApplication",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("percent", config_percent(blank=True, null=True)),
                ("evidence", models.TextField(blank=True, default="")),
                ("reason", models.TextField()),
                ("applied_at", models.DateTimeField(auto_now_add=True)),
                ("applied_by", required_user_fk()),
                (
                    "ceiling_band",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="performance.band",
                    ),
                ),
                (
                    "component_result",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="performance.monthlycomponentresult",
                    ),
                ),
                (
                    "kpi_score",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="performance.monthlykpiscore",
                    ),
                ),
                (
                    "monthly_performance",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="deduction_applications",
                        to="performance.monthlyperformance",
                    ),
                ),
                (
                    "reverses",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="performance.deductionapplication",
                    ),
                ),
                (
                    "rule",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="performance.deductionrule",
                    ),
                ),
            ],
            options={
                "db_table": "performance_deduction_application",
                "ordering": ["monthly_performance_id", "applied_at", "id"],
                "default_permissions": (),
                "constraints": [
                    models.CheckConstraint(condition=~Q(reason=""), name="performance_deduction_reason_chk"),
                ],
            },
        ),
        migrations.CreateModel(
            name="GenerationGapDecision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "decision",
                    models.CharField(
                        choices=[
                            ("SYSTEM_ISSUE_EXCLUDE", "Confirmed system issue (excluded)"),
                            ("EMPLOYEE_RESPONSIBLE", "Employee responsible (counts)"),
                        ],
                        max_length=20,
                    ),
                ),
                ("reason", models.TextField()),
                ("decided_at", models.DateTimeField(auto_now_add=True)),
                ("decided_by", required_user_fk()),
                (
                    "occurrence",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="recurring.scheduleoccurrence",
                    ),
                ),
            ],
            options={
                "db_table": "performance_generation_gap_decision",
                "ordering": ["occurrence_id"],
                "default_permissions": (),
                "constraints": [
                    models.CheckConstraint(condition=~Q(reason=""), name="performance_gap_reason_chk"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ApprovedLeave",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("start_date", models.DateField()),
                ("end_date", models.DateField()),
                ("reason", models.TextField(blank=True, default="")),
                ("recorded_at", models.DateTimeField(auto_now_add=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_by", user_fk()),
                (
                    "employee",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="+", to="org.employee"
                    ),
                ),
                ("recorded_by", required_user_fk()),
            ],
            options={
                "db_table": "performance_approved_leave",
                "ordering": ["employee_id", "start_date", "id"],
                "default_permissions": (),
                "constraints": [
                    models.CheckConstraint(
                        condition=Q(end_date__gte=F("start_date")), name="performance_leave_period_valid"
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ManualTaskOverride",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("INCLUDE", "Count for the responsibility"),
                            ("EXCLUDE", "Do not count for the responsibility"),
                        ],
                        max_length=8,
                    ),
                ),
                ("reason", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", required_user_fk()),
                (
                    "responsibility",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="recurring.responsibility",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="+", to="tasks.task"
                    ),
                ),
            ],
            options={
                "db_table": "performance_manual_task_override",
                "ordering": ["task_id", "responsibility_id"],
                "default_permissions": (),
                "constraints": [
                    models.UniqueConstraint(fields=("task", "responsibility"), name="performance_override_uniq"),
                    models.CheckConstraint(condition=~Q(reason=""), name="performance_override_reason_chk"),
                ],
            },
        ),
    ]
