from rest_framework import serializers

from ..models import ClockType, PrioritySla, SlaRule, SlaSetting


class SlaRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = SlaRule
        fields = [
            "id",
            "code",
            "version",
            "name",
            "rule_type",
            "clock",
            "duration_minutes",
            "warning_pct",
            "critical_pct",
            "overdue_pct",
            "is_active",
            "supersedes",
            "created_at",
        ]
        read_only_fields = fields


class SlaRuleSupersedeSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, required=False)
    clock = serializers.ChoiceField(choices=ClockType.choices, required=False)
    duration_minutes = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    warning_pct = serializers.IntegerField(min_value=1, max_value=1000, required=False)
    critical_pct = serializers.IntegerField(min_value=1, max_value=1000, required=False)
    overdue_pct = serializers.IntegerField(min_value=1, max_value=1000, required=False)


class SlaSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = SlaSetting
        fields = ["company_work_end", "login_fallback_time", "updated_at"]
        read_only_fields = ["updated_at"]
        extra_kwargs = {
            "company_work_end": {"required": False, "allow_null": True},
            "login_fallback_time": {"required": False, "allow_null": True},
        }


class SlaRuleCreateSerializer(serializers.Serializer):
    """Phase 5.2: a new calendar-time DURATION rule (e.g. for a task priority)."""

    code = serializers.CharField(max_length=40)
    name = serializers.CharField(max_length=120)
    duration_minutes = serializers.IntegerField(min_value=1)
    warning_pct = serializers.IntegerField(required=False, default=50)
    critical_pct = serializers.IntegerField(required=False, default=75)
    overdue_pct = serializers.IntegerField(required=False, default=100)


class PrioritySlaSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrioritySla
        fields = ["priority", "rule_code", "is_active", "updated_at"]
        read_only_fields = fields


class PrioritySlaUpdateSerializer(serializers.Serializer):
    rule_code = serializers.CharField(max_length=40, required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False, default=True)
