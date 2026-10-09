from rest_framework import serializers

from apps.tasks.api.serializers import (
    AssignedTaskRowSerializer,
    DailyActivitySerializer,
    MonitoredEmployeeSerializer,
    UserRefSerializer,
    WorkCountsSerializer,
)

STATUS_HELP = "HEALTHY, FAILED, NEVER_RUN or UNKNOWN (never assumed healthy)."


class SchedulerJobSerializer(serializers.Serializer):
    job = serializers.CharField()
    name = serializers.CharField()
    status = serializers.CharField(help_text=STATUS_HELP)
    detail = serializers.CharField()
    last_started_at = serializers.DateTimeField(allow_null=True)
    last_finished_at = serializers.DateTimeField(allow_null=True)
    last_success_at = serializers.DateTimeField(allow_null=True)
    last_summary = serializers.DictField(child=serializers.IntegerField())


class OccurrenceCountsSerializer(serializers.Serializer):
    generated = serializers.IntegerField()
    skipped = serializers.IntegerField()
    missed = serializers.IntegerField()
    failed = serializers.IntegerField()


class OperationsSnapshotSerializer(serializers.Serializer):
    daily_activity = WorkCountsSerializer()
    assigned_tasks = WorkCountsSerializer()


class SlaCountsSerializer(serializers.Serializer):
    not_started = serializers.IntegerField()
    on_track = serializers.IntegerField()
    warning = serializers.IntegerField()
    critical = serializers.IntegerField()
    overdue = serializers.IntegerField()
    completed_on_time = serializers.IntegerField()
    completed_late = serializers.IntegerField()


class SlaSnapshotSerializer(serializers.Serializer):
    daily_activity = SlaCountsSerializer()
    assigned_tasks = SlaCountsSerializer()


ATTENTION_HELP = (
    "Factual states of open work: OVERDUE, CRITICAL, WARNING, BLOCKED, ON_TRACK or "
    "NO_ACTIVE_WORK (not a score or ranking)."
)


class CommandCenterEmployeeSerializer(serializers.Serializer):
    employee = MonitoredEmployeeSerializer()
    daily_activity = WorkCountsSerializer()
    assigned_tasks = WorkCountsSerializer()
    attention = serializers.ListField(child=serializers.CharField(), help_text=ATTENTION_HELP)


class OverviewEmployeesSerializer(serializers.Serializer):
    total_active = serializers.IntegerField()
    with_work = serializers.IntegerField(help_text="Active employees with work on the date.")


class OverviewDailySerializer(serializers.Serializer):
    scheduled = serializers.IntegerField()
    completed = serializers.IntegerField()
    pending = serializers.IntegerField()
    in_progress = serializers.IntegerField()
    blocked = serializers.IntegerField()
    overdue = serializers.IntegerField()


class OverviewAssignedSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    active = serializers.IntegerField()
    completed = serializers.IntegerField()
    pending = serializers.IntegerField()
    in_progress = serializers.IntegerField()
    blocked = serializers.IntegerField()
    overdue = serializers.IntegerField()


class OverviewSlaSerializer(serializers.Serializer):
    on_track = serializers.IntegerField()
    warning = serializers.IntegerField()
    critical = serializers.IntegerField()
    overdue = serializers.IntegerField()


class CommandCenterOverviewSerializer(serializers.Serializer):
    employees = OverviewEmployeesSerializer()
    daily_activities = OverviewDailySerializer()
    assigned_tasks = OverviewAssignedSerializer()
    sla = OverviewSlaSerializer(help_text="SLA states of open work (from the SLA engine).")


class RecentEventSerializer(serializers.Serializer):
    """Audit metadata only (no old/new values, IP, request id or context)."""

    id = serializers.IntegerField()
    action = serializers.CharField()
    entity_type = serializers.CharField()
    entity_id = serializers.CharField()
    actor = UserRefSerializer(allow_null=True)
    occurred_at = serializers.DateTimeField()


class CommandCenterSummarySerializer(serializers.Serializer):
    date = serializers.DateField()
    server_time = serializers.DateTimeField()
    scheduler = SchedulerJobSerializer(many=True)
    todays_occurrences = OccurrenceCountsSerializer()
    operations = OperationsSnapshotSerializer()
    sla = SlaSnapshotSerializer()
    overview = CommandCenterOverviewSerializer()
    employees = CommandCenterEmployeeSerializer(many=True)
    recent_events = RecentEventSerializer(many=True)


class HealthCheckSerializer(serializers.Serializer):
    name = serializers.CharField()
    status = serializers.CharField(help_text=STATUS_HELP)
    detail = serializers.CharField()


class CommandCenterHealthSerializer(serializers.Serializer):
    checked_at = serializers.DateTimeField()
    overall = serializers.CharField(help_text=STATUS_HELP)
    checks = HealthCheckSerializer(many=True)


# --- Phase 6B: employee drill-down and SLA attention (read-only) ----------------------------


class CommandCenterDailyActivitySerializer(DailyActivitySerializer):
    source = serializers.CharField(help_text="Always SCHEDULED.")


class CommandCenterAssignedTaskSerializer(AssignedTaskRowSerializer):
    source = serializers.CharField(help_text="Always MANUAL.")
    remaining_seconds = serializers.IntegerField(allow_null=True)


class CommandCenterEmployeeDetailSerializer(serializers.Serializer):
    date = serializers.DateField()
    server_time = serializers.DateTimeField()
    employee = MonitoredEmployeeSerializer()
    attention = serializers.ListField(child=serializers.CharField(), help_text=ATTENTION_HELP)
    daily_activities = CommandCenterDailyActivitySerializer(many=True)
    assigned_tasks = CommandCenterAssignedTaskSerializer(many=True)


class SlaAttentionItemSerializer(serializers.Serializer):
    employee = MonitoredEmployeeSerializer()
    task_id = serializers.IntegerField()
    reference = serializers.CharField()
    title = serializers.CharField()
    source = serializers.CharField(help_text="SCHEDULED or MANUAL.")
    status = serializers.CharField()
    deadline = serializers.DateTimeField(allow_null=True)
    sla_state = serializers.CharField()
    remaining_seconds = serializers.IntegerField(allow_null=True)


class SlaAttentionSerializer(serializers.Serializer):
    date = serializers.DateField()
    server_time = serializers.DateTimeField()
    critical = SlaAttentionItemSerializer(many=True)
    warning = SlaAttentionItemSerializer(many=True)
    overdue = SlaAttentionItemSerializer(many=True)
    on_track = SlaAttentionItemSerializer(many=True, help_text="Only when requested.")
    not_started = SlaAttentionItemSerializer(many=True, help_text="Only when requested.")
