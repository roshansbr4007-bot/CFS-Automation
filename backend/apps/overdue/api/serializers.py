from rest_framework import serializers

from ..models import OverdueCause
from ..reports import overdue_minutes
from ..selectors import can_review, can_submit


class OverdueUserRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    email = serializers.EmailField()


class OverdueEmployeeRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()
    employee_code = serializers.CharField(allow_null=True)


class OverdueDepartmentRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()


class OverdueCaseSerializer(serializers.Serializer):
    """Facts are read-only copies taken when the case opened. The employee's REASON and the
    reviewer's authoritative CAUSE are separate. Dependencies are not part of the platform
    yet, so there is no dependency information (shown as not applicable)."""

    id = serializers.IntegerField()
    status = serializers.CharField()
    opened_at = serializers.DateTimeField()
    opened_via = serializers.CharField()
    # facts
    task_id = serializers.IntegerField()
    task_reference = serializers.CharField(source="task.reference")
    task_title = serializers.CharField()
    employee = OverdueEmployeeRefSerializer()
    department = OverdueDepartmentRefSerializer()
    task_creator = OverdueUserRefSerializer()
    task_assigned_at = serializers.DateTimeField()
    priority = serializers.CharField()
    category_name = serializers.CharField()
    sla_start_at = serializers.DateTimeField()
    sla_due_at = serializers.DateTimeField()
    sla_rule_code = serializers.CharField()
    sla_rule_name = serializers.CharField()
    overdue_at = serializers.DateTimeField()
    completed_at = serializers.DateTimeField(source="task.completed_at", allow_null=True)
    overdue_minutes = serializers.SerializerMethodField()
    # employee reason
    reason_category = serializers.CharField()
    explanation = serializers.CharField()
    submitted_at = serializers.DateTimeField(allow_null=True)
    submitted_by = OverdueUserRefSerializer(allow_null=True)
    # reviewer authoritative cause
    cause = serializers.CharField()
    review_remark = serializers.CharField()
    reviewed_at = serializers.DateTimeField(allow_null=True)
    reviewed_by = OverdueUserRefSerializer(allow_null=True)
    version = serializers.IntegerField()
    can_submit = serializers.SerializerMethodField()
    can_review = serializers.SerializerMethodField()

    def get_overdue_minutes(self, case) -> int:
        return overdue_minutes(case)

    def get_can_submit(self, case) -> bool:
        return can_submit(self.context["request"].user, case)

    def get_can_review(self, case) -> bool:
        return can_review(self.context["request"].user, case)


class SubmitReasonSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)
    reason_category = serializers.ChoiceField(choices=OverdueCause.choices)
    explanation = serializers.CharField(max_length=4000)


class ReviewSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)
    cause = serializers.ChoiceField(choices=OverdueCause.choices)
    remark = serializers.CharField(max_length=4000)
