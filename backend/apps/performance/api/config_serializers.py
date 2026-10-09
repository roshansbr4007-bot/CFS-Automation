"""Phase 7.1 KRA configuration API shapes. Choice values travel as plain strings (validated by
apps.performance.config_services), and every class is prefixed KpiCfg so the OpenAPI component
names never collide with existing ones."""

from decimal import Decimal

from rest_framework import serializers

from apps.org.models import Department, Employee
from apps.recurring.models import Responsibility

from ..models import KPI, Band, BandScheme, KPIWeightVersion, ScoringRule

# --- references ------------------------------------------------------------------------------


class KpiCfgKpiRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()


class KpiCfgVersionedRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    version = serializers.IntegerField()
    name = serializers.CharField()
    status = serializers.CharField()


class KpiCfgPlanRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    configuration = serializers.CharField()
    version = serializers.IntegerField()
    name = serializers.CharField()
    status = serializers.CharField()
    calculation_model = serializers.CharField()


class KpiCfgBandRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    min_points = serializers.DecimalField(max_digits=5, decimal_places=2)


class KpiCfgResponsibilityRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()


class KpiCfgDepartmentRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()


class KpiCfgEmployeeRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()


# --- read shapes -------------------------------------------------------------------------------


class KpiCfgKpiSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    is_active = serializers.BooleanField()


class KpiCfgComponentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    plan_line = serializers.IntegerField(source="plan_line_id")
    position = serializers.IntegerField()
    source_type = serializers.CharField()
    responsibility = KpiCfgResponsibilityRefSerializer(allow_null=True)
    label = serializers.CharField()
    contribution_share = serializers.DecimalField(max_digits=9, decimal_places=6)
    task_scope = serializers.CharField()
    manual_match = serializers.CharField()
    verification_policy = serializers.CharField()


class KpiCfgLineSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    plan_version = serializers.IntegerField(source="weight_version_id")
    kpi = KpiCfgKpiRefSerializer()
    name = serializers.CharField(source="label", help_text="display_name, or the KPI's name")
    display_name = serializers.CharField()
    weight = serializers.DecimalField(
        max_digits=5, decimal_places=2, help_text="Maximum points of this KPI (fixed)."
    )
    scoring_rule = KpiCfgVersionedRefSerializer(allow_null=True)
    position = serializers.IntegerField()
    components = KpiCfgComponentSerializer(many=True)


class KpiCfgDeductionRuleSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    plan_version = serializers.IntegerField(source="plan_version_id")
    code = serializers.CharField()
    name = serializers.CharField()
    kind = serializers.CharField()
    scope = serializers.CharField()
    min_pct = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    max_pct = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    ceiling_band = KpiCfgBandRefSerializer(allow_null=True)
    stacking = serializers.CharField()
    cap_pct = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    uncapped = serializers.BooleanField()
    priority = serializers.IntegerField(allow_null=True)
    description = serializers.CharField()


class KpiCfgPlanSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    configuration = serializers.CharField()
    version = serializers.IntegerField()
    name = serializers.CharField()
    calculation_model = serializers.CharField()
    status = serializers.CharField()
    effective_from = serializers.DateField()
    effective_to = serializers.DateField(allow_null=True)
    band_scheme = KpiCfgVersionedRefSerializer(allow_null=True)
    credit_on_time = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    credit_late = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    credit_overdue = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    deduction_stacking_method = serializers.CharField()
    created_at = serializers.DateTimeField()
    activated_at = serializers.DateTimeField(allow_null=True)
    retired_at = serializers.DateTimeField(allow_null=True)
    retire_reason = serializers.CharField()


class KpiCfgPlanDetailSerializer(KpiCfgPlanSerializer):
    lines = KpiCfgLineSerializer(many=True, source="weights")
    deduction_rules = KpiCfgDeductionRuleSerializer(many=True)


class KpiCfgStepSerializer(serializers.Serializer):
    min_achievement_pct = serializers.DecimalField(max_digits=5, decimal_places=2)
    score_pct = serializers.DecimalField(max_digits=5, decimal_places=2)


class KpiCfgScoringRuleSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    version = serializers.IntegerField()
    name = serializers.CharField()
    status = serializers.CharField()
    effective_from = serializers.DateField()
    effective_to = serializers.DateField(allow_null=True)
    below_min_score_pct = serializers.DecimalField(
        max_digits=5, decimal_places=2, allow_null=True
    )
    steps = KpiCfgStepSerializer(many=True)
    activated_at = serializers.DateTimeField(allow_null=True)
    retired_at = serializers.DateTimeField(allow_null=True)
    retire_reason = serializers.CharField()


class KpiCfgBandSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    min_points = serializers.DecimalField(
        max_digits=5, decimal_places=2, help_text="The band applies at or above this total."
    )
    position = serializers.IntegerField()


class KpiCfgBandSchemeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    version = serializers.IntegerField()
    name = serializers.CharField()
    status = serializers.CharField()
    effective_from = serializers.DateField()
    effective_to = serializers.DateField(allow_null=True)
    bands = KpiCfgBandSerializer(many=True)
    activated_at = serializers.DateTimeField(allow_null=True)
    retired_at = serializers.DateTimeField(allow_null=True)
    retire_reason = serializers.CharField()


class KpiCfgPlanDefaultSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    department = KpiCfgDepartmentRefSerializer()
    role = serializers.CharField(source="role.name")
    configuration = serializers.CharField()
    effective_from = serializers.DateField()
    effective_to = serializers.DateField(allow_null=True)
    created_at = serializers.DateTimeField()
    ended_at = serializers.DateTimeField(allow_null=True)


class KpiCfgOverrideSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    employee = KpiCfgEmployeeRefSerializer()
    plan_version = KpiCfgPlanRefSerializer(source="weight_version")
    effective_from = serializers.DateField()
    effective_to = serializers.DateField(allow_null=True)
    reason = serializers.CharField()
    created_at = serializers.DateTimeField()


class KpiCfgResolutionSerializer(serializers.Serializer):
    employee = KpiCfgEmployeeRefSerializer()
    date = serializers.DateField()
    state = serializers.CharField(help_text="RESOLVED, NO_PLAN or AMBIGUOUS_ROLE")
    source = serializers.CharField(allow_null=True, help_text="OVERRIDE, DEFAULT or null")
    reason = serializers.CharField()
    roles = serializers.ListField(child=serializers.CharField())
    plan_version = KpiCfgPlanRefSerializer(allow_null=True)
    override_id = serializers.IntegerField(allow_null=True)
    default_id = serializers.IntegerField(allow_null=True)
    matching_default_ids = serializers.ListField(child=serializers.IntegerField())


# --- inputs -------------------------------------------------------------------------------------


def _text(**kwargs):
    return serializers.CharField(allow_blank=True, **kwargs)


class KpiCfgKpiUpdateInputSerializer(serializers.Serializer):
    description = _text()


class KpiCfgPlanCreateInputSerializer(serializers.Serializer):
    configuration = serializers.CharField(max_length=40)
    name = serializers.CharField(max_length=120)
    effective_from = serializers.DateField()
    effective_to = serializers.DateField(required=False, allow_null=True, default=None)
    band_scheme = serializers.PrimaryKeyRelatedField(
        queryset=BandScheme.objects.all(), required=False, allow_null=True, default=None
    )
    credit_on_time = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, default=Decimal("1.00")
    )
    credit_late = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, default=Decimal("0.25")
    )
    credit_overdue = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, default=Decimal("0.00")
    )
    deduction_stacking_method = _text(required=False, default="")


class KpiCfgPlanUpdateInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, required=False)
    effective_from = serializers.DateField(required=False)
    effective_to = serializers.DateField(required=False, allow_null=True)
    band_scheme = serializers.PrimaryKeyRelatedField(
        queryset=BandScheme.objects.all(), required=False, allow_null=True
    )
    credit_on_time = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    credit_late = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    credit_overdue = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    deduction_stacking_method = _text(required=False)


class KpiCfgRetireInputSerializer(serializers.Serializer):
    last_day = serializers.DateField()
    reason = _text()


class KpiCfgLineCreateInputSerializer(serializers.Serializer):
    kpi = serializers.PrimaryKeyRelatedField(queryset=KPI.objects.all())
    weight = serializers.DecimalField(max_digits=5, decimal_places=2)
    display_name = _text(max_length=120, required=False, default="")
    scoring_rule = serializers.PrimaryKeyRelatedField(
        queryset=ScoringRule.objects.all(), required=False, allow_null=True, default=None
    )
    position = serializers.IntegerField(
        min_value=0, max_value=32767, required=False, allow_null=True, default=None
    )


class KpiCfgLineUpdateInputSerializer(serializers.Serializer):
    weight = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    display_name = _text(max_length=120, required=False)
    scoring_rule = serializers.PrimaryKeyRelatedField(
        queryset=ScoringRule.objects.all(), required=False, allow_null=True
    )
    position = serializers.IntegerField(min_value=0, max_value=32767, required=False)


class KpiCfgComponentCreateInputSerializer(serializers.Serializer):
    source_type = serializers.CharField(help_text="RESPONSIBILITY_TASKS or MANUAL_ENTRY")
    responsibility = serializers.PrimaryKeyRelatedField(
        queryset=Responsibility.objects.all(), required=False, allow_null=True, default=None
    )
    label = _text(max_length=120, required=False, default="")
    contribution_share = serializers.DecimalField(
        max_digits=9, decimal_places=6, required=False, default=Decimal("1")
    )
    task_scope = _text(required=False, default="", help_text="SCHEDULED, MANUAL or BOTH")
    manual_match = _text(required=False, default="", help_text="NONE, TASK_TYPE or CATEGORY")
    verification_policy = _text(
        required=False, default="", help_text="NOT_REQUIRED or REQUIRED"
    )
    position = serializers.IntegerField(
        min_value=0, max_value=32767, required=False, allow_null=True, default=None
    )


class KpiCfgComponentUpdateInputSerializer(serializers.Serializer):
    source_type = serializers.CharField(required=False)
    responsibility = serializers.PrimaryKeyRelatedField(
        queryset=Responsibility.objects.all(), required=False, allow_null=True
    )
    label = _text(max_length=120, required=False)
    contribution_share = serializers.DecimalField(max_digits=9, decimal_places=6, required=False)
    task_scope = _text(required=False)
    manual_match = _text(required=False)
    verification_policy = _text(required=False)
    position = serializers.IntegerField(min_value=0, max_value=32767, required=False)


class KpiCfgDeductionCreateInputSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=40)
    name = serializers.CharField(max_length=120)
    kind = serializers.CharField(help_text="PERCENT_RANGE or BAND_CEILING")
    scope = _text(required=False, default="", help_text="COMPONENT, KPI or OVERALL")
    min_pct = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True, default=None
    )
    max_pct = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True, default=None
    )
    ceiling_band = serializers.PrimaryKeyRelatedField(
        queryset=Band.objects.all(), required=False, allow_null=True, default=None
    )
    stacking = _text(required=False, default="", help_text="STACK or NON_STACKING")
    cap_pct = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True, default=None
    )
    uncapped = serializers.BooleanField(required=False, default=False)
    priority = serializers.IntegerField(
        min_value=1, max_value=32767, required=False, allow_null=True, default=None
    )
    description = _text(required=False, default="")


class KpiCfgDeductionUpdateInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, required=False)
    kind = serializers.CharField(required=False)
    scope = _text(required=False)
    min_pct = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )
    max_pct = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )
    ceiling_band = serializers.PrimaryKeyRelatedField(
        queryset=Band.objects.all(), required=False, allow_null=True
    )
    stacking = _text(required=False)
    cap_pct = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )
    uncapped = serializers.BooleanField(required=False)
    priority = serializers.IntegerField(
        min_value=1, max_value=32767, required=False, allow_null=True
    )
    description = _text(required=False)


class KpiCfgScoringRuleCreateInputSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=40)
    name = serializers.CharField(max_length=120)
    effective_from = serializers.DateField()
    effective_to = serializers.DateField(required=False, allow_null=True, default=None)
    below_min_score_pct = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True, default=None
    )
    steps = KpiCfgStepSerializer(many=True, required=False, default=list)


class KpiCfgScoringRuleUpdateInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, required=False)
    effective_from = serializers.DateField(required=False)
    effective_to = serializers.DateField(required=False, allow_null=True)
    below_min_score_pct = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )
    steps = KpiCfgStepSerializer(many=True, required=False, help_text="Replaces all steps.")


class KpiCfgBandInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=60)
    min_points = serializers.DecimalField(max_digits=5, decimal_places=2)
    position = serializers.IntegerField(
        min_value=0, max_value=32767, required=False, allow_null=True, default=None
    )


class KpiCfgBandSchemeCreateInputSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=40)
    name = serializers.CharField(max_length=120)
    effective_from = serializers.DateField()
    effective_to = serializers.DateField(required=False, allow_null=True, default=None)
    bands = KpiCfgBandInputSerializer(many=True, required=False, default=list)


class KpiCfgBandSchemeUpdateInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, required=False)
    effective_from = serializers.DateField(required=False)
    effective_to = serializers.DateField(required=False, allow_null=True)
    bands = KpiCfgBandInputSerializer(many=True, required=False, help_text="Replaces all bands.")


class KpiCfgPlanDefaultCreateInputSerializer(serializers.Serializer):
    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.all())
    role = serializers.CharField(help_text="System role: Employee, Operations Manager, HR, Admin")
    configuration = serializers.CharField(max_length=40, help_text="KRA plan family code")
    effective_from = serializers.DateField()
    effective_to = serializers.DateField(required=False, allow_null=True, default=None)


class KpiCfgEndInputSerializer(serializers.Serializer):
    last_day = serializers.DateField()


class KpiCfgOverrideCreateInputSerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all())
    plan_version = serializers.PrimaryKeyRelatedField(queryset=KPIWeightVersion.objects.all())
    effective_from = serializers.DateField()
    effective_to = serializers.DateField(required=False, allow_null=True, default=None)
    reason = _text()


class KpiCfgOverrideEndInputSerializer(serializers.Serializer):
    last_day = serializers.DateField()
    reason = _text(required=False, default="")


class KpiCfgResolutionQuerySerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all())
    date = serializers.DateField()
