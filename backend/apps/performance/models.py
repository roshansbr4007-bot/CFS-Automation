"""Performance, KPI/KRA (Phase 7). A CONSUMER of Task / SLA data: it reads them and stores
monthly snapshots; it never owns or changes task or SLA state.

A finalized MonthlyPerformance (and its MonthlyKPIScore rows) is a historical record: weights,
scores and metrics are copied into it, and saving a finalized record is refused.

Phase 7.1 (KRA configuration): KPIWeightVersion is shared by the legacy weighted engine and the
KRA points system, told apart by `calculation_model`. Legacy rows and columns keep their meaning;
KRA values live in their own columns and tables. KRA configuration (plan versions, lines,
components, deduction rules, scoring rules, band schemes) is editable only while DRAFT.
"""

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.core.errors import ConflictError, FieldValidationError

POINTS = {"max_digits": 12, "decimal_places": 6}  # internal precision of KRA points
PERCENT = {"max_digits": 9, "decimal_places": 6}  # internal precision of KRA percentages
CONFIG_PERCENT = {"max_digits": 5, "decimal_places": 2}  # configured percentages (0-100)
CREDIT = {"max_digits": 5, "decimal_places": 2}  # task credits (0-1)
SHARE = {"max_digits": 9, "decimal_places": 6}  # component contribution shares


class ScoreSource(models.TextChoices):
    SYSTEM = "SYSTEM", "System calculated"
    MANAGER = "MANAGER", "Manager review"
    HYBRID = "HYBRID", "Hybrid (reserved; not calculated in Phase 7)"


class KPI(models.Model):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    score_source = models.CharField(max_length=8, choices=ScoreSource.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "performance_kpi"
        ordering = ["code"]
        default_permissions = ()

    def save(self, *args, **kwargs):
        """A KPI used by an ACTIVE weight version cannot be deactivated: that would silently
        drop it from the configuration. Change the configuration with a new version instead.
        (QuerySet.update() bypasses this, as it bypasses every model save.)"""
        if self.pk and not self.is_active:
            was_active = KPI.objects.filter(pk=self.pk, is_active=True).exists()
            in_use = KPIWeight.objects.filter(
                kpi_id=self.pk, weight_version__status=WeightVersionStatus.ACTIVE
            ).exists()
            if was_active and in_use:
                raise FieldValidationError(
                    "This KPI is part of an active weight version.",
                    fields={"is_active": [
                        "Used by an active weight version; change it through a new version."
                    ]},
                )
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class WeightVersionStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ACTIVE = "ACTIVE", "Active"
    RETIRED = "RETIRED", "Retired"


class CalculationModel(models.TextChoices):
    """Which engine scores months under a version. Existing versions are LEGACY_WEIGHTED."""

    LEGACY_WEIGHTED = "LEGACY_WEIGHTED", "Legacy weighted score (0-100)"
    KRA_POINTS = "KRA_POINTS", "KRA points (0-10)"


class PlanSource(models.TextChoices):
    """Why a KRA plan applied to a month (Phase 7.2): the HR override or the default."""

    OVERRIDE = "OVERRIDE", "HR override"
    DEFAULT = "DEFAULT", "Department + system role default"


class StackingMethod(models.TextChoices):
    """How several stacking deductions on one target combine (chosen by HR per plan)."""

    ADDITIVE = "ADDITIVE", "Additive"
    SEQUENTIAL = "SEQUENTIAL", "Sequential (each applies to the remainder)"


class ConfigurationLocked(ConflictError):
    """ACTIVE or RETIRED KRA configuration was about to be changed."""

    code = "configuration_locked"
    message = "Active or retired configuration cannot be changed. Create a new version."


# Fields that the activation / retirement transitions themselves may write.
LIFECYCLE_FIELDS = frozenset(
    {"status", "effective_to", "activated_by", "activated_at", "retired_by", "retired_at",
     "retire_reason"}
)


def _assert_lifecycle_only(model, pk, update_fields) -> None:
    """A non-draft configuration row may only be saved by a lifecycle transition."""
    status = model.objects.filter(pk=pk).values_list("status", flat=True).first()
    if status in (None, WeightVersionStatus.DRAFT):
        return
    if update_fields is None or not set(update_fields) <= LIFECYCLE_FIELDS:
        raise ConfigurationLocked()


def _assert_kra_plan_draft(version_id) -> None:
    """Children of a KRA plan version change only while it is DRAFT. Legacy versions keep
    their original (service-level) rules."""
    row = (
        KPIWeightVersion.objects.filter(pk=version_id)
        .values_list("calculation_model", "status")
        .first()
    )
    if row and row[0] == CalculationModel.KRA_POINTS and row[1] != WeightVersionStatus.DRAFT:
        raise ConfigurationLocked()


def _assert_draft(model, pk) -> None:
    status = model.objects.filter(pk=pk).values_list("status", flat=True).first()
    if status not in (None, WeightVersionStatus.DRAFT):
        raise ConfigurationLocked()


def _credit_range(field: str) -> models.CheckConstraint:
    return models.CheckConstraint(
        condition=Q(**{f"{field}__isnull": True})
        | (Q(**{f"{field}__gte": 0}) & Q(**{f"{field}__lte": 1})),
        name=f"performance_version_{field}_range",
    )


class KPIWeightVersion(models.Model):
    """One version of a KPI weight configuration (e.g. OPERATIONS v1). Weights must total
    exactly 10.0 to be activated; once activated the weights never change.

    Phase 7.1: also the KRA plan version (calculation_model KRA_POINTS): weight = the line's
    maximum points; task credits, band scheme and deduction stacking belong to the version."""

    configuration = models.CharField(max_length=40)  # e.g. OPERATIONS; one per department type
    version = models.PositiveIntegerField()
    name = models.CharField(max_length=120)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=8, choices=WeightVersionStatus.choices, default=WeightVersionStatus.DRAFT
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # --- Phase 7.1 (KRA plan version); legacy rows keep the defaults ---
    calculation_model = models.CharField(
        max_length=16, choices=CalculationModel.choices, default=CalculationModel.LEGACY_WEIGHTED
    )
    band_scheme = models.ForeignKey(
        "BandScheme", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    credit_on_time = models.DecimalField(null=True, blank=True, **CREDIT)
    credit_late = models.DecimalField(null=True, blank=True, **CREDIT)
    credit_overdue = models.DecimalField(null=True, blank=True, **CREDIT)
    deduction_stacking_method = models.CharField(
        max_length=10, choices=StackingMethod.choices, blank=True, default=""
    )
    retired_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    retired_at = models.DateTimeField(null=True, blank=True)
    retire_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "performance_weight_version"
        ordering = ["configuration", "version"]
        default_permissions = ()
        permissions = [
            ("configure_kpis", "Prepare KPI plan drafts, plan defaults and employee overrides"),
            ("approve_kpi_config", "Activate and retire KPI configuration"),
            ("manage_performance", "Calculate, adjust and review monthly performance"),
            ("finalize_performance", "Finalize monthly performance"),
            ("reopen_performance", "Reopen finalized monthly performance"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["configuration", "version"], name="performance_version_uniq"
            ),
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True) | Q(effective_to__gte=F("effective_from")),
                name="performance_version_period_valid",
            ),
            _credit_range("credit_on_time"),
            _credit_range("credit_late"),
            _credit_range("credit_overdue"),
        ]

    def __str__(self):
        return f"{self.configuration} v{self.version}"

    def save(self, *args, **kwargs):
        if self.pk and self._stored_model() == CalculationModel.KRA_POINTS:
            _assert_lifecycle_only(KPIWeightVersion, self.pk, kwargs.get("update_fields"))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._stored_model() == CalculationModel.KRA_POINTS:
            _assert_draft(KPIWeightVersion, self.pk)
        return super().delete(*args, **kwargs)

    def _stored_model(self):
        return KPIWeightVersion.objects.filter(pk=self.pk).values_list(
            "calculation_model", flat=True
        ).first()


class KPIWeight(models.Model):
    """A line of a version: KPI + weight. For a KRA plan the weight is the line's maximum
    points, display_name the KRA name (blank = the KPI's own name) and scoring_rule the pinned
    KRA benchmark rule."""

    weight_version = models.ForeignKey(
        KPIWeightVersion, on_delete=models.PROTECT, related_name="weights"
    )
    kpi = models.ForeignKey(KPI, on_delete=models.PROTECT, related_name="weights")
    weight = models.DecimalField(max_digits=5, decimal_places=2)
    # --- Phase 7.1 (KRA plan line) ---
    display_name = models.CharField(max_length=120, blank=True, default="")
    scoring_rule = models.ForeignKey(
        "ScoringRule", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "performance_kpi_weight"
        ordering = ["weight_version_id", "kpi__code"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["weight_version", "kpi"], name="performance_weight_uniq"
            ),
            models.CheckConstraint(
                condition=Q(weight__gte=0), name="performance_weight_non_negative"
            ),
        ]

    def __str__(self):
        return f"{self.weight_version} {self.kpi.code}={self.weight}"

    def save(self, *args, **kwargs):
        _assert_kra_plan_draft(self.weight_version_id)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _assert_kra_plan_draft(self.weight_version_id)
        return super().delete(*args, **kwargs)

    @property
    def label(self) -> str:
        return self.display_name or self.kpi.name


class EmployeeKPIAssignment(models.Model):
    """Which weight version applies to an employee over a period (history is kept).

    Phase 7.1: for KRA plans this is the HR override, which wins over the department + role
    default; the override API requires a reason (legacy assignments have none)."""

    employee = models.ForeignKey(
        "org.Employee", on_delete=models.PROTECT, related_name="kpi_assignments"
    )
    weight_version = models.ForeignKey(
        KPIWeightVersion, on_delete=models.PROTECT, related_name="assignments"
    )
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "performance_kpi_assignment"
        ordering = ["employee_id", "effective_from", "id"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["employee"],
                condition=Q(effective_to__isnull=True),
                name="performance_one_open_assignment",
            ),
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True) | Q(effective_to__gte=F("effective_from")),
                name="performance_assignment_period_valid",
            ),
        ]

    def __str__(self):
        return f"{self.employee_id} -> {self.weight_version} from {self.effective_from}"


class PerformanceStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    CALCULATED = "CALCULATED", "Calculated"
    UNDER_REVIEW = "UNDER_REVIEW", "Under review"
    FINALIZED = "FINALIZED", "Finalized"


class PerformanceLocked(Exception):
    """A finalized performance record (or one of its scores) was about to be changed."""


def _assert_not_finalized(performance_pk) -> None:
    rows = MonthlyPerformance.objects.filter(pk=performance_pk)
    status = rows.values_list("status", flat=True).first()
    if status == PerformanceStatus.FINALIZED:
        raise PerformanceLocked("Finalized performance cannot be changed.")


# Phase 7.3: the ONLY fields the audited reopen transition (FINALIZED -> UNDER_REVIEW of a KRA
# month) may write. Every other save of a finalized record is still refused.
REOPEN_FIELDS = frozenset(
    {"status", "reopen_count", "version", "updated_at", "finalized_by", "finalized_at"}
)


def _assert_reopen_transition(performance, update_fields) -> None:
    stored = (
        MonthlyPerformance.objects.filter(pk=performance.pk)
        .values_list("status", "calculation_model")
        .first()
    )
    if (
        stored != (PerformanceStatus.FINALIZED, CalculationModel.KRA_POINTS)
        or performance.status != PerformanceStatus.UNDER_REVIEW
        or update_fields is None
        or not set(update_fields) <= REOPEN_FIELDS
    ):
        raise PerformanceLocked("Only the audited reopen may change a finalized KRA month.")


class MonthlyPerformance(models.Model):
    employee = models.ForeignKey(
        "org.Employee", on_delete=models.PROTECT, related_name="monthly_performance"
    )
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    period_start = models.DateField()
    period_end = models.DateField()
    weight_version = models.ForeignKey(
        KPIWeightVersion, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    status = models.CharField(
        max_length=12, choices=PerformanceStatus.choices, default=PerformanceStatus.DRAFT
    )
    # Operational metrics snapshot (from Task / SLA data; never edited by people).
    assigned_tasks = models.PositiveIntegerField(default=0)
    scheduled_tasks = models.PositiveIntegerField(default=0)  # daily activities (subtotal)
    manual_tasks = models.PositiveIntegerField(default=0)  # assigned tasks (subtotal)
    completed_tasks = models.PositiveIntegerField(default=0)
    pending_tasks = models.PositiveIntegerField(default=0)
    overdue_tasks = models.PositiveIntegerField(default=0)
    sla_met_tasks = models.PositiveIntegerField(default=0)
    sla_breached_tasks = models.PositiveIntegerField(default=0)
    on_time_completed_tasks = models.PositiveIntegerField(default=0)
    completed_sla_tasks = models.PositiveIntegerField(default=0)
    completion_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    sla_compliance_rate = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    overall_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    performance_band = models.CharField(max_length=20, blank=True)
    manager_remark = models.TextField(blank=True)
    calculated_at = models.DateTimeField(null=True, blank=True)
    calculated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # --- Phase 7.1: KRA points record structure (filled by the KRA engine from Phase 7.2).
    # Legacy records keep the defaults; overall_score / performance_band stay legacy-only. ---
    calculation_model = models.CharField(
        max_length=16, choices=CalculationModel.choices, default=CalculationModel.LEGACY_WEIGHTED
    )
    department = models.ForeignKey(
        "org.Department", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    role_name = models.CharField(max_length=40, blank=True, default="")
    band_scheme = models.ForeignKey(
        "BandScheme", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    band = models.ForeignKey(
        "Band", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    band_name = models.CharField(max_length=60, blank=True, default="")
    band_ceiling = models.ForeignKey(
        "Band", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Sum of the weights of the applicable KPIs (a whole-KPI N/A is excluded, never rescaled).
    max_points_applicable = models.DecimalField(null=True, blank=True, **POINTS)
    auto_total = models.DecimalField(null=True, blank=True, **POINTS)
    adjustment_total = models.DecimalField(null=True, blank=True, **POINTS)
    deduction_total = models.DecimalField(null=True, blank=True, **POINTS)
    final_total = models.DecimalField(null=True, blank=True, **POINTS)
    reopen_count = models.PositiveIntegerField(default=0)
    # --- Phase 7.2: the instant the month was judged at (min(now, month end)) and why the plan
    # applied. A record whose cutoff_at is before the month end is provisional. ---
    cutoff_at = models.DateTimeField(null=True, blank=True)
    plan_source = models.CharField(
        max_length=10, choices=PlanSource.choices, blank=True, default=""
    )

    class Meta:
        db_table = "performance_monthly"
        ordering = ["-year", "-month", "employee_id"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "year", "month"], name="performance_monthly_uniq"
            ),
            models.CheckConstraint(
                condition=Q(month__gte=1) & Q(month__lte=12), name="performance_month_valid"
            ),
            models.CheckConstraint(
                condition=Q(overall_score__isnull=True)
                | (Q(overall_score__gte=0) & Q(overall_score__lte=100)),
                name="performance_overall_in_range",
            ),
        ]

    def save(self, *args, reopen: bool = False, **kwargs):
        """A finalized record is never saved, except by the Phase 7.3 reopen transition
        (apps.performance.review_services.reopen), which passes reopen=True and writes only
        REOPEN_FIELDS of a KRA month moving to UNDER_REVIEW."""
        if self.pk:
            if reopen:
                _assert_reopen_transition(self, kwargs.get("update_fields"))
            else:
                _assert_not_finalized(self.pk)  # the finalize transition itself starts non-final
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee_id} {self.year}-{self.month:02d} {self.status}"


class MonthlyKPIScore(models.Model):
    """One KPI of one monthly record. `weight` is a COPY taken from the weight version, so a
    later configuration change never alters a finalized month."""

    monthly_performance = models.ForeignKey(
        MonthlyPerformance, on_delete=models.PROTECT, related_name="kpi_scores"
    )
    kpi = models.ForeignKey(KPI, on_delete=models.PROTECT, related_name="+")
    weight = models.DecimalField(max_digits=5, decimal_places=2)
    score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    source = models.CharField(max_length=8, choices=ScoreSource.choices)
    manager_remark = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    # --- Phase 7.1: KRA points per KPI (filled from Phase 7.2). `score` stays legacy-only;
    # `weight` keeps its meaning: the copied maximum points of the line. ---
    name_snapshot = models.CharField(max_length=120, blank=True, default="")
    scoring_rule = models.ForeignKey(
        "ScoringRule", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    not_applicable = models.BooleanField(default=False)
    na_reason = models.CharField(max_length=200, blank=True, default="")
    achievement_pct = models.DecimalField(null=True, blank=True, **PERCENT)
    benchmark_pct = models.DecimalField(null=True, blank=True, **PERCENT)
    auto_points = models.DecimalField(null=True, blank=True, **POINTS)
    adjustment_points = models.DecimalField(default=0, **POINTS)
    deduction_points = models.DecimalField(default=0, **POINTS)
    final_points = models.DecimalField(null=True, blank=True, **POINTS)

    class Meta:
        db_table = "performance_monthly_kpi_score"
        ordering = ["monthly_performance_id", "kpi__code"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["monthly_performance", "kpi"], name="performance_score_uniq"
            ),
            models.CheckConstraint(
                condition=Q(score__isnull=True) | (Q(score__gte=0) & Q(score__lte=100)),
                name="performance_score_in_range",
            ),
            models.CheckConstraint(
                condition=Q(weight__gte=0), name="performance_score_weight_non_negative"
            ),
        ]

    def save(self, *args, **kwargs):
        _assert_not_finalized(self.monthly_performance_id)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.monthly_performance_id} {self.kpi_id}={self.score}"


# =============================================================================================
# Phase 7.1: KRA configuration (versioned; editable only while DRAFT)
# =============================================================================================


class ScoringRule(models.Model):
    """A versioned KRA benchmark: achievement % -> score % by lower-bound steps; below the
    lowest step `below_min_score_pct` applies (configurable; seeded 0)."""

    code = models.CharField(max_length=40)
    version = models.PositiveIntegerField()
    name = models.CharField(max_length=120)
    status = models.CharField(
        max_length=8, choices=WeightVersionStatus.choices, default=WeightVersionStatus.DRAFT
    )
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    below_min_score_pct = models.DecimalField(null=True, blank=True, **CONFIG_PERCENT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    retired_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    retired_at = models.DateTimeField(null=True, blank=True)
    retire_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "performance_scoring_rule"
        ordering = ["code", "version"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["code", "version"], name="performance_rule_uniq"),
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True) | Q(effective_to__gte=F("effective_from")),
                name="performance_rule_period_valid",
            ),
            models.CheckConstraint(
                condition=Q(below_min_score_pct__isnull=True)
                | (Q(below_min_score_pct__gte=0) & Q(below_min_score_pct__lte=100)),
                name="performance_rule_below_min_range",
            ),
        ]

    def __str__(self):
        return f"{self.code} v{self.version}"

    def save(self, *args, **kwargs):
        if self.pk:
            _assert_lifecycle_only(ScoringRule, self.pk, kwargs.get("update_fields"))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _assert_draft(ScoringRule, self.pk)
        return super().delete(*args, **kwargs)


class ScoringRuleStep(models.Model):
    """Achievement of at least `min_achievement_pct` scores `score_pct` (highest step wins)."""

    rule = models.ForeignKey(ScoringRule, on_delete=models.PROTECT, related_name="steps")
    min_achievement_pct = models.DecimalField(**CONFIG_PERCENT)
    score_pct = models.DecimalField(**CONFIG_PERCENT)

    class Meta:
        db_table = "performance_scoring_rule_step"
        ordering = ["rule_id", "-min_achievement_pct"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["rule", "min_achievement_pct"], name="performance_rule_step_uniq"
            ),
            models.CheckConstraint(
                condition=Q(min_achievement_pct__gte=0) & Q(min_achievement_pct__lte=100)
                & Q(score_pct__gte=0) & Q(score_pct__lte=100),
                name="performance_rule_step_range",
            ),
        ]

    def __str__(self):
        return f"{self.rule_id}: >= {self.min_achievement_pct}% -> {self.score_pct}%"

    def save(self, *args, **kwargs):
        _assert_draft(ScoringRule, self.rule_id)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _assert_draft(ScoringRule, self.rule_id)
        return super().delete(*args, **kwargs)


class BandScheme(models.Model):
    """A versioned set of performance bands on the fixed 10-point scale."""

    code = models.CharField(max_length=40)
    version = models.PositiveIntegerField()
    name = models.CharField(max_length=120)
    status = models.CharField(
        max_length=8, choices=WeightVersionStatus.choices, default=WeightVersionStatus.DRAFT
    )
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    retired_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    retired_at = models.DateTimeField(null=True, blank=True)
    retire_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "performance_band_scheme"
        ordering = ["code", "version"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["code", "version"], name="performance_scheme_uniq"),
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True) | Q(effective_to__gte=F("effective_from")),
                name="performance_scheme_period_valid",
            ),
        ]

    def __str__(self):
        return f"{self.code} v{self.version}"

    def save(self, *args, **kwargs):
        if self.pk:
            _assert_lifecycle_only(BandScheme, self.pk, kwargs.get("update_fields"))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _assert_draft(BandScheme, self.pk)
        return super().delete(*args, **kwargs)


class Band(models.Model):
    """A band applies when the monthly points are AT LEAST `min_points` (lower bound)."""

    scheme = models.ForeignKey(BandScheme, on_delete=models.PROTECT, related_name="bands")
    name = models.CharField(max_length=60)
    min_points = models.DecimalField(max_digits=5, decimal_places=2)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "performance_band"
        ordering = ["scheme_id", "-min_points"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["scheme", "name"], name="performance_band_name_uniq"),
            models.UniqueConstraint(
                fields=["scheme", "min_points"], name="performance_band_min_uniq"
            ),
            models.CheckConstraint(
                condition=Q(min_points__gte=0) & Q(min_points__lte=10),
                name="performance_band_min_range",
            ),
        ]

    def __str__(self):
        return f"{self.name} (>= {self.min_points})"

    def save(self, *args, **kwargs):
        _assert_draft(BandScheme, self.scheme_id)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _assert_draft(BandScheme, self.scheme_id)
        return super().delete(*args, **kwargs)


class KPIPlanDefault(models.Model):
    """Department + system role (an auth Group, the existing canonical role) -> a KRA plan
    family (KPIWeightVersion.configuration) over a period. HR overrides
    (EmployeeKPIAssignment) win over defaults."""

    department = models.ForeignKey(
        "org.Department", on_delete=models.PROTECT, related_name="+"
    )
    role = models.ForeignKey("auth.Group", on_delete=models.PROTECT, related_name="+")
    configuration = models.CharField(max_length=40)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "performance_plan_default"
        ordering = ["department_id", "role_id", "effective_from", "id"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["department", "role"],
                condition=Q(effective_to__isnull=True),
                name="performance_one_open_default",
            ),
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True) | Q(effective_to__gte=F("effective_from")),
                name="performance_default_period_valid",
            ),
        ]

    def __str__(self):
        return f"{self.department_id}/{self.role_id} -> {self.configuration}"


class ComponentSource(models.TextChoices):
    RESPONSIBILITY_TASKS = "RESPONSIBILITY_TASKS", "Tasks of a responsibility"
    MANUAL_ENTRY = "MANUAL_ENTRY", "Manual entry (scored by HR)"


class TaskScope(models.TextChoices):
    SCHEDULED = "SCHEDULED", "Scheduled tasks"
    MANUAL = "MANUAL", "Manual tasks"
    BOTH = "BOTH", "Scheduled and manual tasks"


class ManualMatch(models.TextChoices):
    """How manual tasks are matched to a responsibility automatically (besides a task that
    carries the responsibility itself and HR exceptions)."""

    NONE = "NONE", "No automatic rule"
    TASK_TYPE = "TASK_TYPE", "Same task type as the responsibility"
    CATEGORY = "CATEGORY", "Same category and department as the responsibility"


class VerificationPolicy(models.TextChoices):
    NOT_REQUIRED = "NOT_REQUIRED", "Completion counts"
    REQUIRED = "REQUIRED", "Counts only once verified"


class KPIComponent(models.Model):
    """A source contributing to a KRA plan line. Shares are relative (default 1 = equal) and
    are normalised over the applicable components when a month is calculated."""

    plan_line = models.ForeignKey(KPIWeight, on_delete=models.PROTECT, related_name="components")
    position = models.PositiveSmallIntegerField(default=0)
    source_type = models.CharField(max_length=24, choices=ComponentSource.choices)
    responsibility = models.ForeignKey(
        "recurring.Responsibility", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
    )
    label = models.CharField(max_length=120, blank=True, default="")
    contribution_share = models.DecimalField(default=1, **SHARE)
    task_scope = models.CharField(
        max_length=10, choices=TaskScope.choices, blank=True, default=""
    )
    manual_match = models.CharField(
        max_length=10, choices=ManualMatch.choices, blank=True, default=""
    )
    verification_policy = models.CharField(
        max_length=12, choices=VerificationPolicy.choices, blank=True, default=""
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "performance_kpi_component"
        ordering = ["plan_line_id", "position", "id"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["plan_line", "responsibility"],
                condition=Q(responsibility__isnull=False),
                name="performance_component_resp_uniq",
            ),
            models.CheckConstraint(
                condition=Q(contribution_share__gt=0), name="performance_component_share_positive"
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
        ]

    def __str__(self):
        return f"{self.plan_line_id}:{self.source_type}:{self.responsibility_id or self.label}"

    def save(self, *args, **kwargs):
        _assert_kra_plan_draft(self._version_id())
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _assert_kra_plan_draft(self._version_id())
        return super().delete(*args, **kwargs)

    def _version_id(self):
        return KPIWeight.objects.filter(pk=self.plan_line_id).values_list(
            "weight_version_id", flat=True
        ).first()


class DeductionKind(models.TextChoices):
    PERCENT_RANGE = "PERCENT_RANGE", "Percentage reduction within a range"
    BAND_CEILING = "BAND_CEILING", "Band ceiling (special penalty)"


class DeductionScope(models.TextChoices):
    COMPONENT = "COMPONENT", "Component"
    KPI = "KPI", "KPI"
    OVERALL = "OVERALL", "Overall"


class DeductionStacking(models.TextChoices):
    STACK = "STACK", "Stacks with other deductions"
    NON_STACKING = "NON_STACKING", "Does not stack"


class DeductionRule(models.Model):
    """A configured deduction category of a KRA plan version. HR applies it to a month with
    evidence and a reason (Phase 7.3); the range only bounds the percentage HR may choose.
    Empty scope / stacking / priority / cap / ceiling band are allowed in a draft and refused
    at activation."""

    plan_version = models.ForeignKey(
        KPIWeightVersion, on_delete=models.PROTECT, related_name="deduction_rules"
    )
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=16, choices=DeductionKind.choices)
    scope = models.CharField(max_length=10, choices=DeductionScope.choices, blank=True, default="")
    min_pct = models.DecimalField(null=True, blank=True, **CONFIG_PERCENT)
    max_pct = models.DecimalField(null=True, blank=True, **CONFIG_PERCENT)
    ceiling_band = models.ForeignKey(
        Band, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    stacking = models.CharField(
        max_length=12, choices=DeductionStacking.choices, blank=True, default=""
    )
    cap_pct = models.DecimalField(null=True, blank=True, **CONFIG_PERCENT)
    uncapped = models.BooleanField(default=False)
    priority = models.PositiveSmallIntegerField(null=True, blank=True)
    description = models.TextField(blank=True, default="")

    class Meta:
        db_table = "performance_deduction_rule"
        ordering = ["plan_version_id", "priority", "code"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["plan_version", "code"], name="performance_deduction_code_uniq"
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
        ]

    def __str__(self):
        return f"{self.plan_version_id}:{self.code}"

    def save(self, *args, **kwargs):
        _assert_kra_plan_draft(self.plan_version_id)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _assert_kra_plan_draft(self.plan_version_id)
        return super().delete(*args, **kwargs)


# =============================================================================================
# Phase 7.1: monthly KRA result structures (written by the KRA engine / workflow later)
# =============================================================================================


class AppendOnly(Exception):
    """An append-only audit record was about to be changed or deleted."""


class MonthlyComponentResult(models.Model):
    """One component of one KPI of one month (snapshot of label and share)."""

    kpi_score = models.ForeignKey(
        MonthlyKPIScore, on_delete=models.PROTECT, related_name="component_results"
    )
    component = models.ForeignKey(KPIComponent, on_delete=models.PROTECT, related_name="+")
    label_snapshot = models.CharField(max_length=120, blank=True, default="")
    share_snapshot = models.DecimalField(null=True, blank=True, **SHARE)
    normalized_share = models.DecimalField(null=True, blank=True, **POINTS)
    applicable = models.BooleanField(default=True)
    na_reason = models.CharField(max_length=200, blank=True, default="")
    on_time_count = models.PositiveIntegerField(default=0)
    late_count = models.PositiveIntegerField(default=0)
    overdue_count = models.PositiveIntegerField(default=0)
    excluded_count = models.PositiveIntegerField(default=0)
    credit_sum = models.DecimalField(null=True, blank=True, **POINTS)
    achievement_pct = models.DecimalField(null=True, blank=True, **PERCENT)
    manual_achievement_pct = models.DecimalField(null=True, blank=True, **PERCENT)
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    entered_at = models.DateTimeField(null=True, blank=True)
    # --- Phase 7.3: points taken by component-scope deductions (base: the component's share of
    # the KPI's points, approved E3); written only by apps.performance.settlement. ---
    deduction_points = models.DecimalField(default=0, **POINTS)

    class Meta:
        db_table = "performance_monthly_component"
        ordering = ["kpi_score_id", "id"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["kpi_score", "component"], name="performance_component_result_uniq"
            ),
        ]

    def __str__(self):
        return f"{self.kpi_score_id}:{self.component_id}"


class TaskOutcome(models.TextChoices):
    ON_TIME = "ON_TIME", "On time"
    LATE = "LATE", "Late"
    OVERDUE = "OVERDUE", "Overdue or incomplete at cutoff"
    NA = "NA", "Not applicable"


class MatchReason(models.TextChoices):
    """Why a task (or an expected-but-not-generated occurrence) belongs to a component."""

    SCHEDULED = "SCHEDULED", "Generated from the responsibility's schedule"
    TASK_RESPONSIBILITY = "TASK_RESPONSIBILITY", "The task carries the responsibility"
    TASK_TYPE = "TASK_TYPE", "Same task type"
    CATEGORY = "CATEGORY", "Same category and department"
    OVERRIDE = "OVERRIDE", "HR exception (include)"
    GAP = "GAP", "Expected occurrence that was not generated"


class MonthlyTaskCredit(models.Model):
    """The credit one task earned for a component. task_id is a plain number (not a foreign
    key) so that deleting a task is never blocked by performance history.

    Phase 7.2: an expected-but-not-generated occurrence (generation gap) has no task; it is
    recorded with occurrence_id instead. Exactly one of the two is set."""

    component_result = models.ForeignKey(
        MonthlyComponentResult, on_delete=models.PROTECT, related_name="task_credits"
    )
    task_id = models.BigIntegerField(null=True, blank=True)
    occurrence_id = models.BigIntegerField(null=True, blank=True)
    match_reason = models.CharField(
        max_length=20, choices=MatchReason.choices, blank=True, default=""
    )
    task_reference = models.CharField(max_length=200, blank=True, default="")
    outcome = models.CharField(max_length=8, choices=TaskOutcome.choices)
    na_reason = models.CharField(max_length=200, blank=True, default="")
    credit = models.DecimalField(null=True, blank=True, **CREDIT)
    deadline_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "performance_monthly_task_credit"
        ordering = ["component_result_id", "task_id"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["component_result", "task_id"], name="performance_task_credit_uniq"
            ),
            models.UniqueConstraint(
                fields=["component_result", "occurrence_id"],
                condition=Q(occurrence_id__isnull=False),
                name="performance_gap_credit_uniq",
            ),
            models.CheckConstraint(
                condition=(Q(task_id__isnull=False) & Q(occurrence_id__isnull=True))
                | (Q(task_id__isnull=True) & Q(occurrence_id__isnull=False)),
                name="performance_task_credit_source_chk",
            ),
        ]

    def __str__(self):
        return f"{self.component_result_id}:{self.task_id or self.occurrence_id}={self.outcome}"


class ScoreAdjustment(models.Model):
    """Append-only HR adjustment of one KPI's points (before / adjustment / after)."""

    kpi_score = models.ForeignKey(
        MonthlyKPIScore, on_delete=models.PROTECT, related_name="adjustments"
    )
    before_points = models.DecimalField(**POINTS)
    adjustment_points = models.DecimalField(**POINTS)
    after_points = models.DecimalField(**POINTS)
    reason = models.TextField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "performance_score_adjustment"
        ordering = ["kpi_score_id", "created_at", "id"]
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=~Q(reason=""), name="performance_adjustment_reason_chk"
            ),
        ]

    def __str__(self):
        return f"{self.kpi_score_id}: {self.before_points} -> {self.after_points}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise AppendOnly("Score adjustments are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnly("Score adjustments are append-only.")


class DeductionApplication(models.Model):
    """Append-only: a deduction rule applied to a month by HR (a reversal is a new row that
    points at the one it reverses)."""

    monthly_performance = models.ForeignKey(
        MonthlyPerformance, on_delete=models.PROTECT, related_name="deduction_applications"
    )
    rule = models.ForeignKey(DeductionRule, on_delete=models.PROTECT, related_name="+")
    kpi_score = models.ForeignKey(
        MonthlyKPIScore, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    component_result = models.ForeignKey(
        MonthlyComponentResult, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    percent = models.DecimalField(null=True, blank=True, **CONFIG_PERCENT)
    ceiling_band = models.ForeignKey(
        Band, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    evidence = models.TextField(blank=True, default="")
    reason = models.TextField()
    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    applied_at = models.DateTimeField(auto_now_add=True)
    reverses = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        db_table = "performance_deduction_application"
        ordering = ["monthly_performance_id", "applied_at", "id"]
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=~Q(reason=""), name="performance_deduction_reason_chk"
            ),
        ]

    def __str__(self):
        return f"{self.monthly_performance_id}:{self.rule_id}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise AppendOnly("Deduction applications are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnly("Deduction applications are append-only.")


class GapDecision(models.TextChoices):
    SYSTEM_ISSUE_EXCLUDE = "SYSTEM_ISSUE_EXCLUDE", "Confirmed system issue (excluded)"
    EMPLOYEE_RESPONSIBLE = "EMPLOYEE_RESPONSIBLE", "Employee responsible (counts)"


class GenerationGapDecision(models.Model):
    """HR's audited decision on an expected-but-not-generated scheduled task."""

    occurrence = models.OneToOneField(
        "recurring.ScheduleOccurrence", on_delete=models.PROTECT, related_name="+"
    )
    decision = models.CharField(max_length=20, choices=GapDecision.choices)
    reason = models.TextField()
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "performance_generation_gap_decision"
        ordering = ["occurrence_id"]
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=~Q(reason=""), name="performance_gap_reason_chk"),
        ]

    def __str__(self):
        return f"{self.occurrence_id}: {self.decision}"


class ApprovedLeave(models.Model):
    """Performance-side record of approved leave (structure only; no leave management)."""

    employee = models.ForeignKey("org.Employee", on_delete=models.PROTECT, related_name="+")
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True, default="")
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "performance_approved_leave"
        ordering = ["employee_id", "start_date", "id"]
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")), name="performance_leave_period_valid"
            ),
        ]

    def __str__(self):
        return f"{self.employee_id}: {self.start_date} - {self.end_date}"


class OverrideAction(models.TextChoices):
    INCLUDE = "INCLUDE", "Count for the responsibility"
    EXCLUDE = "EXCLUDE", "Do not count for the responsibility"


class ManualTaskOverride(models.Model):
    """EXCEPTION ONLY: HR includes or excludes one manual task for one responsibility when the
    automatic identification (task's responsibility, task type, category) is wrong. Removed
    together with the task."""

    task = models.ForeignKey("tasks.Task", on_delete=models.CASCADE, related_name="+")
    responsibility = models.ForeignKey(
        "recurring.Responsibility", on_delete=models.PROTECT, related_name="+"
    )
    action = models.CharField(max_length=8, choices=OverrideAction.choices)
    reason = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "performance_manual_task_override"
        ordering = ["task_id", "responsibility_id"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["task", "responsibility"], name="performance_override_uniq"
            ),
            models.CheckConstraint(
                condition=~Q(reason=""), name="performance_override_reason_chk"
            ),
        ]

    def __str__(self):
        return f"{self.task_id} {self.action} {self.responsibility_id}"
