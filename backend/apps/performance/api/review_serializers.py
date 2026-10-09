"""Phase 7.3 KRA review API shapes. Every class is prefixed KraRev so OpenAPI component names
never collide. Values are validated by apps.performance.review_services."""

from rest_framework import serializers

from apps.org.models import Employee
from apps.recurring.models import Responsibility
from apps.tasks.models import Task

from ..models import ApprovedLeave, ManualTaskOverride

POINTS = {"max_digits": 12, "decimal_places": 6}
PERCENT = {"max_digits": 9, "decimal_places": 6}


def _text(**kwargs):
    return serializers.CharField(allow_blank=True, **kwargs)


# --- inputs ------------------------------------------------------------------------------------


class KraRevCalculateInputSerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all())
    year = serializers.IntegerField(min_value=2000, max_value=2100)
    month = serializers.IntegerField(min_value=1, max_value=12)


class KraRevVersionInputSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)


class KraRevReasonInputSerializer(KraRevVersionInputSerializer):
    reason = _text(required=False, default="")


class KraRevReturnInputSerializer(KraRevVersionInputSerializer):
    """Return for recalculation: the reason is required (7.3 decision 6)."""

    reason = _text()


class KraRevAdjustInputSerializer(KraRevVersionInputSerializer):
    points = serializers.DecimalField(**POINTS, help_text="New KPI points, 0 to the maximum")
    reason = _text()


class KraRevDeductionInputSerializer(KraRevVersionInputSerializer):
    rule = serializers.IntegerField(min_value=1, help_text="Deduction rule of the month's plan")
    percent = serializers.DecimalField(max_digits=5, decimal_places=2, required=False,
                                       allow_null=True, default=None,
                                       help_text="PERCENT_RANGE rules only")
    kpi = serializers.IntegerField(min_value=1, required=False, allow_null=True, default=None,
                                   help_text="KPI id (KPI-scope rules)")
    component = serializers.IntegerField(min_value=1, required=False, allow_null=True,
                                         default=None,
                                         help_text="Plan component id (component-scope rules)")
    evidence = _text(required=False, default="")
    reason = _text()


class KraRevGapDecisionInputSerializer(KraRevVersionInputSerializer):
    occurrence = serializers.IntegerField(min_value=1)
    decision = serializers.CharField(help_text="SYSTEM_ISSUE_EXCLUDE or EMPLOYEE_RESPONSIBLE")
    reason = _text()


class KraRevManualEntryInputSerializer(KraRevVersionInputSerializer):
    achievement_pct = serializers.DecimalField(**PERCENT, help_text="0 to 100")
    reason = _text()  # required (7.3 decision 6)


class KraRevLeaveInputSerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all())
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    reason = _text(required=False, default="")


class KraRevTextReasonInputSerializer(serializers.Serializer):
    reason = _text()


class KraRevTaskOverrideInputSerializer(serializers.Serializer):
    task = serializers.PrimaryKeyRelatedField(queryset=Task.objects.all())
    responsibility = serializers.PrimaryKeyRelatedField(queryset=Responsibility.objects.all())
    action = serializers.CharField(help_text="INCLUDE or EXCLUDE")
    reason = _text()


class KraRevAnnualQuerySerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all())
    year = serializers.IntegerField(min_value=2000, max_value=2100)


# --- outputs -----------------------------------------------------------------------------------


class KraRevBlockerSerializer(serializers.Serializer):
    kind = serializers.CharField()
    component = serializers.CharField()
    occurrence_id = serializers.IntegerField(required=False)
    component_id = serializers.IntegerField(required=False)
    message = serializers.CharField()


class KraRevComponentSerializer(serializers.Serializer):
    component_id = serializers.IntegerField()
    label = serializers.CharField()
    source_type = serializers.CharField()
    applicable = serializers.BooleanField()
    na_reason = serializers.CharField()
    normalized_share = serializers.DecimalField(**POINTS, allow_null=True)
    achievement_pct = serializers.DecimalField(**PERCENT, allow_null=True)
    manual_achievement_pct = serializers.DecimalField(**PERCENT, allow_null=True)
    entered_by = serializers.IntegerField(allow_null=True)
    entered_at = serializers.DateTimeField(allow_null=True)
    on_time_count = serializers.IntegerField()
    late_count = serializers.IntegerField()
    overdue_count = serializers.IntegerField()
    excluded_count = serializers.IntegerField()
    deduction_points = serializers.DecimalField(**POINTS)


class KraRevAdjustmentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    before_points = serializers.DecimalField(**POINTS)
    adjustment_points = serializers.DecimalField(**POINTS)
    after_points = serializers.DecimalField(**POINTS)
    reason = serializers.CharField()
    actor = serializers.IntegerField()
    created_at = serializers.DateTimeField()


class KraRevKpiSerializer(serializers.Serializer):
    kpi_id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    weight = serializers.DecimalField(max_digits=5, decimal_places=2)
    not_applicable = serializers.BooleanField()
    na_reason = serializers.CharField()
    achievement_pct = serializers.DecimalField(**PERCENT, allow_null=True)
    benchmark_pct = serializers.DecimalField(**PERCENT, allow_null=True)
    auto_points = serializers.DecimalField(**POINTS, allow_null=True)
    adjustment_points = serializers.DecimalField(**POINTS)
    deduction_points = serializers.DecimalField(**POINTS)
    final_points = serializers.DecimalField(**POINTS, allow_null=True)
    points_before_deductions = serializers.DecimalField(**POINTS)
    adjustment_applied = serializers.BooleanField()
    latest_adjustment_id = serializers.IntegerField(allow_null=True)
    components = KraRevComponentSerializer(many=True)
    adjustments = KraRevAdjustmentSerializer(many=True)


class KraRevApplicationSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    rule_id = serializers.IntegerField()
    rule_code = serializers.CharField()
    rule_name = serializers.CharField()
    kind = serializers.CharField()
    scope = serializers.CharField()
    kpi_id = serializers.IntegerField(allow_null=True)
    component_id = serializers.IntegerField(allow_null=True)
    percent = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    ceiling_band = serializers.CharField()
    evidence = serializers.CharField()
    reason = serializers.CharField()
    applied_by = serializers.IntegerField()
    applied_at = serializers.DateTimeField()
    reverses = serializers.IntegerField(allow_null=True)
    reversed_by = serializers.IntegerField(allow_null=True)
    active = serializers.BooleanField()


class KraRevLineSerializer(serializers.Serializer):
    scope = serializers.CharField()
    rule_id = serializers.IntegerField()
    rule_code = serializers.CharField()
    rule_name = serializers.CharField()
    kpi_id = serializers.IntegerField(allow_null=True)
    kpi = serializers.CharField()
    component_id = serializers.IntegerField(allow_null=True)
    component = serializers.CharField()
    rate_pct = serializers.DecimalField(**POINTS, allow_null=True)
    effective = serializers.BooleanField()
    points = serializers.DecimalField(**POINTS)
    application_ids = serializers.ListField(child=serializers.IntegerField())
    ceiling_band = serializers.CharField(required=False)


class KraRevMonthSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    employee = serializers.IntegerField()
    year = serializers.IntegerField()
    month = serializers.IntegerField()
    status = serializers.CharField()
    version = serializers.IntegerField()
    plan_version = serializers.IntegerField(allow_null=True)
    cutoff_at = serializers.DateTimeField(allow_null=True)
    provisional = serializers.BooleanField()
    max_points_applicable = serializers.DecimalField(**POINTS, allow_null=True)
    auto_total = serializers.DecimalField(**POINTS, allow_null=True)
    adjustment_total = serializers.DecimalField(**POINTS, allow_null=True)
    deduction_total = serializers.DecimalField(**POINTS, allow_null=True)
    final_total = serializers.DecimalField(**POINTS, allow_null=True)
    band = serializers.CharField()
    band_ceiling = serializers.CharField()
    reopen_count = serializers.IntegerField()
    reviewed_by = serializers.IntegerField(allow_null=True)
    reviewed_at = serializers.DateTimeField(allow_null=True)
    finalized_by = serializers.IntegerField(allow_null=True)
    finalized_at = serializers.DateTimeField(allow_null=True)
    kpis = KraRevKpiSerializer(many=True)
    deduction_applications = KraRevApplicationSerializer(many=True)
    deduction_lines = KraRevLineSerializer(many=True)
    blockers = KraRevBlockerSerializer(many=True)


class KraRevAnnualMonthSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    month = serializers.IntegerField()
    final_total = serializers.DecimalField(**POINTS)
    max_points_applicable = serializers.DecimalField(**POINTS)
    band = serializers.CharField()


class KraRevAnnualExclusionSerializer(serializers.Serializer):
    month = serializers.IntegerField()
    reason = serializers.CharField()
    status = serializers.CharField()


class KraRevAnnualSerializer(serializers.Serializer):
    employee = serializers.IntegerField()
    year = serializers.IntegerField()
    applicable_months = serializers.IntegerField()
    annual_total = serializers.DecimalField(**POINTS, allow_null=True)
    annual_average = serializers.DecimalField(**POINTS, allow_null=True)
    maximum_total = serializers.DecimalField(max_digits=5, decimal_places=2)
    months = KraRevAnnualMonthSerializer(many=True)
    excluded = KraRevAnnualExclusionSerializer(many=True)


class KraRevLeaveSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApprovedLeave
        fields = ["id", "employee", "start_date", "end_date", "reason", "recorded_by",
                  "recorded_at", "cancelled_by", "cancelled_at"]
        read_only_fields = fields


class KraRevTaskOverrideSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManualTaskOverride
        fields = ["id", "task", "responsibility", "action", "reason", "created_by",
                  "created_at"]
        read_only_fields = fields
