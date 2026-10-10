"""Phase 7.4 API shapes: the caller's own KRA performance (KraMy*) and the HR / Admin KRA month
listing (KraList*). Prefixed so OpenAPI component names never collide."""

from rest_framework import serializers

POINTS = {"max_digits": 12, "decimal_places": 6}
PERCENT = {"max_digits": 9, "decimal_places": 6}
STATE_HELP = "PROVISIONAL, PENDING_REVIEW or FINALIZED"


# --- the caller's own performance (Employee Home) ---------------------------------------------


class KraMyEmployeeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()
    date_of_joining = serializers.DateField(allow_null=True)


class KraMyCurrentSerializer(serializers.Serializer):
    year = serializers.IntegerField()
    month = serializers.IntegerField()


class KraMyMonthRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    year = serializers.IntegerField()
    month = serializers.IntegerField()
    state = serializers.CharField(help_text=STATE_HELP)
    final_total = serializers.DecimalField(**POINTS, allow_null=True,
                                           help_text="Only for PROVISIONAL and FINALIZED")
    max_points_applicable = serializers.DecimalField(**POINTS, allow_null=True)
    band = serializers.CharField(allow_null=True)


class KraMyHistorySerializer(serializers.Serializer):
    employee = KraMyEmployeeSerializer()
    current = KraMyCurrentSerializer()
    months = KraMyMonthRowSerializer(many=True)


class KraMyComponentSerializer(serializers.Serializer):
    label = serializers.CharField()
    applicable = serializers.BooleanField()
    na_label = serializers.CharField(allow_null=True)
    achievement_pct = serializers.DecimalField(**PERCENT, allow_null=True)
    on_time_count = serializers.IntegerField()
    late_count = serializers.IntegerField()
    overdue_count = serializers.IntegerField()


class KraMyKpiSerializer(serializers.Serializer):
    name = serializers.CharField()
    weight = serializers.DecimalField(max_digits=5, decimal_places=2)
    not_applicable = serializers.BooleanField()
    na_label = serializers.CharField(allow_null=True)
    achievement_pct = serializers.DecimalField(**PERCENT, allow_null=True)
    auto_points = serializers.DecimalField(**POINTS, allow_null=True)
    final_points = serializers.DecimalField(**POINTS, allow_null=True)
    components = KraMyComponentSerializer(many=True)


class KraMyDeductionSerializer(serializers.Serializer):
    rule = serializers.CharField()
    kpi = serializers.CharField()
    component = serializers.CharField()
    points = serializers.DecimalField(**POINTS)


class KraMyMonthSerializer(serializers.Serializer):
    """PENDING_REVIEW months carry only id / year / month / state."""

    id = serializers.IntegerField()
    year = serializers.IntegerField()
    month = serializers.IntegerField()
    state = serializers.CharField(help_text=STATE_HELP)
    # Absent (not null) for PENDING_REVIEW months.
    auto_total = serializers.DecimalField(**POINTS, required=False)
    final_total = serializers.DecimalField(**POINTS, required=False)
    max_points_applicable = serializers.DecimalField(**POINTS, required=False)
    band = serializers.CharField(required=False, allow_blank=True)
    kpis = KraMyKpiSerializer(many=True, required=False)
    deductions = KraMyDeductionSerializer(many=True, required=False)


class KraMyAnnualQuerySerializer(serializers.Serializer):
    year = serializers.IntegerField(min_value=2000, max_value=2100)


class KraMyAnnualMonthSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    month = serializers.IntegerField()
    final_total = serializers.DecimalField(**POINTS)
    max_points_applicable = serializers.DecimalField(**POINTS)
    band = serializers.CharField()


class KraMyAnnualExclusionSerializer(serializers.Serializer):
    month = serializers.IntegerField()
    reason = serializers.CharField(
        help_text="NO_RECORD, NOT_FINALIZED, LEGACY_SCALE or NOTHING_APPLICABLE"
    )


class KraMyAnnualSerializer(serializers.Serializer):
    year = serializers.IntegerField()
    applicable_months = serializers.IntegerField()
    annual_total = serializers.DecimalField(**POINTS, allow_null=True)
    annual_average = serializers.DecimalField(**POINTS, allow_null=True)
    maximum_total = serializers.DecimalField(max_digits=5, decimal_places=2)
    months = KraMyAnnualMonthSerializer(many=True)
    excluded = KraMyAnnualExclusionSerializer(many=True)


# --- organisation-wide KRA month listing (HR / Admin) -----------------------------------------


class KraListEmployeeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    employee_code = serializers.CharField(allow_null=True)
    full_name = serializers.CharField()


class KraListDepartmentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()


class KraListRowSerializer(serializers.Serializer):
    """A stored KRA month (never recalculated). `department` is the department recorded when
    the month was calculated."""

    id = serializers.IntegerField()
    employee = KraListEmployeeSerializer()
    department = KraListDepartmentSerializer(allow_null=True)
    year = serializers.IntegerField()
    month = serializers.IntegerField()
    status = serializers.CharField()
    provisional = serializers.BooleanField()
    reopen_count = serializers.IntegerField()
    max_points_applicable = serializers.DecimalField(**POINTS, allow_null=True)
    auto_total = serializers.DecimalField(**POINTS, allow_null=True)
    adjustment_total = serializers.DecimalField(**POINTS, allow_null=True)
    deduction_total = serializers.DecimalField(**POINTS, allow_null=True)
    final_total = serializers.DecimalField(**POINTS, allow_null=True)
    band = serializers.CharField(source="band_name")
    band_ceiling = serializers.SerializerMethodField()
    finalized_at = serializers.DateTimeField(allow_null=True)

    def get_band_ceiling(self, record) -> str:
        return record.band_ceiling.name if record.band_ceiling_id else ""
