from rest_framework import serializers

from ..models import BusinessCalendar, CalendarDay, DayKind


class CalendarDaySerializer(serializers.ModelSerializer):
    class Meta:
        model = CalendarDay
        fields = ["id", "date", "kind", "name", "created_at"]
        read_only_fields = fields


class CalendarDayCreateSerializer(serializers.Serializer):
    date = serializers.DateField()
    kind = serializers.ChoiceField(choices=DayKind.choices)
    name = serializers.CharField(max_length=120, allow_blank=True)


class CompanyCalendarSerializer(serializers.ModelSerializer):
    days = CalendarDaySerializer(many=True, read_only=True)

    class Meta:
        model = BusinessCalendar
        fields = ["code", "name", "weekly_off_weekdays", "saturday_working_occurrences", "days"]
        read_only_fields = fields


class WorkingDaysSerializer(serializers.Serializer):
    working_days = serializers.ListField(child=serializers.DateField())
