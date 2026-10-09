from rest_framework import serializers

from ..reports import employee_id


class PerformanceDepartmentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()


class PerformanceEmployeeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    employee_id = serializers.SerializerMethodField(
        help_text="Business employee code; the internal id when no code is set."
    )
    full_name = serializers.CharField()
    department = PerformanceDepartmentSerializer()

    def get_employee_id(self, employee) -> str:
        return employee_id(employee)


class ReportKPIScoreSerializer(serializers.Serializer):
    """A stored KPI row of the monthly snapshot (never recalculated)."""

    code = serializers.CharField(source="kpi.code")
    name = serializers.CharField(source="kpi.name")
    weight = serializers.DecimalField(max_digits=5, decimal_places=2)
    score = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    source = serializers.CharField()
    remarks = serializers.CharField(source="manager_remark")


class PerformanceReportRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    employee = PerformanceEmployeeSerializer()
    year = serializers.IntegerField()
    month = serializers.IntegerField()
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    status = serializers.CharField()
    overall_score = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    performance_band = serializers.CharField()
    manager_remark = serializers.CharField()
    assigned_tasks = serializers.IntegerField()
    completed_tasks = serializers.IntegerField()
    pending_tasks = serializers.IntegerField()
    overdue_tasks = serializers.IntegerField()
    sla_breached_tasks = serializers.IntegerField()
    completion_rate = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    sla_compliance_rate = serializers.DecimalField(
        max_digits=5, decimal_places=2, allow_null=True
    )
    kpi_scores = ReportKPIScoreSerializer(many=True, source="report_scores")
