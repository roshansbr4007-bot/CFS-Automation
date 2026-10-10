from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import User
from apps.org.api.serializers import (  # shared OpenAPI components (identical shapes)
    DepartmentRefSerializer,
    EmployeeRefSerializer,
    VersionSerializer,
)
from apps.org.models import Department, Employee
from apps.sla import services as sla
from apps.sla.models import ClockKind, Outcome, SlaState, StopReason, Trigger

from .. import monitoring, policy
from ..models import (
    ReceivedSource,
    Task,
    TaskAssignment,
    TaskAttachment,
    TaskCategory,
    TaskComment,
    TaskPriority,
    TaskTemplate,
    TaskType,
    TaskVerification,
)

ACTION_NAMES = [
    "edit",
    "change_department",
    "reassign",
    "cancel",
    "acknowledge",
    "start",
    "complete",
    "block",
    "unblock",
    "verify",
    "reject_verification",
    "comment",
    "attach",
    "delete",
]


class UserRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "full_name"]
        read_only_fields = fields


class AssigneeOptionSerializer(serializers.ModelSerializer):
    department = DepartmentRefSerializer(read_only=True)

    class Meta:
        model = Employee
        fields = ["id", "full_name", "department"]
        read_only_fields = fields


class SlaClockSerializer(serializers.Serializer):
    """Backend-calculated SLA values. React displays these and never computes them."""

    kind = serializers.ChoiceField(choices=ClockKind.choices)
    rule_code = serializers.CharField()
    rule_name = serializers.CharField()
    rule_version = serializers.IntegerField()
    rule_type = serializers.CharField()
    clock = serializers.CharField()
    trigger = serializers.ChoiceField(choices=Trigger.choices)
    duration_minutes = serializers.IntegerField(allow_null=True)
    start_at = serializers.DateTimeField(allow_null=True)
    due_at = serializers.DateTimeField(allow_null=True)
    state = serializers.ChoiceField(choices=SlaState.choices)
    elapsed_pct = serializers.FloatField(allow_null=True)
    remaining_seconds = serializers.IntegerField(allow_null=True)
    warning_at = serializers.DateTimeField(allow_null=True)
    critical_at = serializers.DateTimeField(allow_null=True)
    overdue_at = serializers.DateTimeField(allow_null=True)
    stopped_at = serializers.DateTimeField(allow_null=True)
    stop_reason = serializers.ChoiceField(choices=StopReason.choices, allow_null=True)
    outcome = serializers.ChoiceField(choices=Outcome.choices, allow_null=True)
    waiting_for = serializers.CharField(allow_null=True)


class TaskSlaSerializer(serializers.Serializer):
    resolution = SlaClockSerializer(allow_null=True)
    resolution_note = serializers.CharField(allow_null=True)
    acknowledgment = SlaClockSerializer(allow_null=True)


class CategoryRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskCategory
        fields = ["id", "code", "name"]
        read_only_fields = fields


class TaskCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskCategory
        fields = ["id", "code", "name", "is_active", "created_at", "updated_at"]
        read_only_fields = fields


class TaskCategoryCreateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=40)
    name = serializers.CharField(max_length=120)
    is_active = serializers.BooleanField(required=False, default=True)


class TaskCategoryUpdateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=40, required=False)  # accepted only to refuse changes
    name = serializers.CharField(max_length=120, required=False)
    is_active = serializers.BooleanField(required=False)


class ResponsibilityRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()


class ScheduleRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    frequency = serializers.CharField()


class TemplateRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskTemplate
        fields = ["id", "code", "name"]
        read_only_fields = fields


class TaskTemplateSerializer(serializers.ModelSerializer):
    department = DepartmentRefSerializer(read_only=True)
    sla_rule_name = serializers.SerializerMethodField()
    sla_note = serializers.SerializerMethodField()

    class Meta:
        model = TaskTemplate
        fields = [
            "id",
            "code",
            "name",
            "department",
            "trigger",
            "fixed_time",
            "resolution_rule_code",
            "sla_rule_name",
            "sla_note",
            "acknowledgment_required",
            "verification_required",
        ]
        read_only_fields = fields

    def get_sla_rule_name(self, template) -> str | None:
        rule = sla.active_rule(template.resolution_rule_code)
        return rule.name if rule else None

    def get_sla_note(self, template) -> str | None:
        return sla.resolution_plan(template)[2]


class TaskSerializer(serializers.ModelSerializer):
    reference = serializers.CharField(read_only=True)
    department = DepartmentRefSerializer(read_only=True)
    created_by = UserRefSerializer(read_only=True)
    assigned_to = EmployeeRefSerializer(read_only=True)
    assigned_by = UserRefSerializer(read_only=True)
    completed_by = UserRefSerializer(read_only=True, allow_null=True)
    template = TemplateRefSerializer(read_only=True, allow_null=True)
    category = CategoryRefSerializer(read_only=True, allow_null=True)
    responsibility = ResponsibilityRefSerializer(read_only=True, allow_null=True)
    schedule = ScheduleRefSerializer(read_only=True, allow_null=True)
    sla = serializers.SerializerMethodField()
    allowed_actions = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id",
            "reference",
            "title",
            "description",
            "task_type",
            "template",
            "priority",
            "status",
            "department",
            "category",
            "source",
            "responsibility",
            "schedule",
            "occurrence_date",
            "generated_at",
            "created_by",
            "assigned_to",
            "assigned_by",
            "assigned_at",
            "received_at",
            "received_at_source",
            "trigger_at",
            "acknowledgment_required",
            "acknowledged_at",
            "started_at",
            "completed_at",
            "completed_by",
            "completion_source",
            "verification_required",
            "verification_status",
            "rework_count",
            "blocked_reason",
            "blocked_at",
            "cancelled_reason",
            "cancelled_at",
            "version",
            "created_at",
            "updated_at",
            "sla",
            "allowed_actions",
        ]
        read_only_fields = fields

    @extend_schema_field(TaskSlaSerializer)
    def get_sla(self, task) -> dict:
        # Through TaskSlaSerializer so timestamps get the same IST representation as every
        # other API datetime (a raw dict would be rendered by the JSON encoder as UTC "Z").
        return TaskSlaSerializer(sla.task_sla(task, timezone.now())).data

    @extend_schema_field(
        serializers.ListField(child=serializers.ChoiceField(choices=ACTION_NAMES))
    )
    def get_allowed_actions(self, task) -> list[str]:
        request = self.context.get("request")
        return policy.allowed_actions(request.user, task) if request else []


class TaskAssignmentSerializer(serializers.ModelSerializer):
    from_employee = EmployeeRefSerializer(read_only=True, allow_null=True)
    to_employee = EmployeeRefSerializer(read_only=True)
    assigned_by = UserRefSerializer(read_only=True)

    class Meta:
        model = TaskAssignment
        fields = ["id", "from_employee", "to_employee", "assigned_by", "assigned_at", "note"]
        read_only_fields = fields


class TaskVerificationSerializer(serializers.ModelSerializer):
    decided_by = UserRefSerializer(read_only=True)

    class Meta:
        model = TaskVerification
        fields = [
            "cycle_no",
            "submitted_at",
            "decision",
            "rejection_reason",
            "remarks",
            "decided_by",
            "decided_at",
            "rework_seconds",
        ]
        read_only_fields = fields


class TaskDetailSerializer(TaskSerializer):
    assignments = TaskAssignmentSerializer(many=True, read_only=True)
    verifications = TaskVerificationSerializer(many=True, read_only=True)
    # Approved S4-S6 (additive, read-only): a generated task's scheduled time and its arrival
    # facts. Null for manual tasks; null facts for tasks generated before they were recorded.
    scheduled_at = serializers.SerializerMethodField(
        help_text="When the occurrence was scheduled (generated tasks only)."
    )
    arrived_overdue = serializers.SerializerMethodField(
        help_text="The resolution SLA was already overdue when the task was generated "
        "(system-caused). Null: not a generated task, or not recorded."
    )
    ack_arrived_overdue = serializers.SerializerMethodField(
        help_text="The acknowledgment SLA was already overdue when the task was generated."
    )

    class Meta(TaskSerializer.Meta):
        fields = [
            *TaskSerializer.Meta.fields,
            "assignments",
            "verifications",
            "scheduled_at",
            "arrived_overdue",
            "ack_arrived_overdue",
        ]
        read_only_fields = fields

    def _arrival(self, task) -> dict:
        cache = self.__dict__.setdefault("_arrival_cache", {})
        if task.pk not in cache:
            facts = monitoring.arrival_facts([task])
            cache[task.pk] = facts.get(task.pk, dict(monitoring.UNKNOWN_ARRIVAL))
        return cache[task.pk]

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_scheduled_at(self, task):
        value = self._arrival(task)["scheduled_at"]
        return serializers.DateTimeField().to_representation(value) if value else None

    @extend_schema_field(serializers.BooleanField(allow_null=True))
    def get_arrived_overdue(self, task) -> bool | None:
        return self._arrival(task)["arrived_overdue"]

    @extend_schema_field(serializers.BooleanField(allow_null=True))
    def get_ack_arrived_overdue(self, task) -> bool | None:
        return self._arrival(task)["ack_arrived_overdue"]


class TaskCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    # Task-level classification, chosen by the creator (never taken from the assignee).
    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.all())
    category = serializers.PrimaryKeyRelatedField(queryset=TaskCategory.objects.all())
    template = serializers.PrimaryKeyRelatedField(
        queryset=TaskTemplate.objects.all(), required=False, allow_null=True, default=None
    )
    task_type = serializers.ChoiceField(
        choices=TaskType.choices, required=False, allow_null=True, default=None
    )
    priority = serializers.ChoiceField(choices=TaskPriority.choices, default=TaskPriority.MEDIUM)
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all())
    received_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    received_at_source = serializers.ChoiceField(
        choices=ReceivedSource.choices, required=False, allow_null=True, default=None
    )
    trigger_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    # Omitted = take the task type's default.
    acknowledgment_required = serializers.BooleanField(
        required=False, allow_null=True, default=None
    )
    verification_required = serializers.BooleanField(
        required=False, allow_null=True, default=None
    )


class SlaPreviewRequestSerializer(serializers.Serializer):
    template = serializers.PrimaryKeyRelatedField(
        queryset=TaskTemplate.objects.filter(is_active=True), required=False, allow_null=True
    )
    assigned_to = serializers.PrimaryKeyRelatedField(
        queryset=Employee.objects.all(), required=False, allow_null=True
    )
    acknowledgment_required = serializers.BooleanField(
        required=False, allow_null=True, default=None
    )
    trigger_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    # Phase 5.2: a configured priority SLA rule applies to manually raised tasks.
    priority = serializers.ChoiceField(
        choices=TaskPriority.choices, required=False, allow_null=True, default=None
    )


class TaskUpdateSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)
    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(), required=False
    )
    category = serializers.PrimaryKeyRelatedField(
        queryset=TaskCategory.objects.all(), required=False
    )
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    task_type = serializers.ChoiceField(choices=TaskType.choices, required=False)
    priority = serializers.ChoiceField(choices=TaskPriority.choices, required=False)
    acknowledgment_required = serializers.BooleanField(required=False)
    verification_required = serializers.BooleanField(required=False)
    received_at = serializers.DateTimeField(required=False, allow_null=True)
    received_at_source = serializers.ChoiceField(
        choices=ReceivedSource.choices, required=False, allow_null=True
    )
    received_at_reason = serializers.CharField(required=False, allow_blank=True)


class ReasonSerializer(VersionSerializer):
    reason = serializers.CharField(allow_blank=True)


class VerifySerializer(VersionSerializer):
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class RejectVerificationSerializer(VersionSerializer):
    reason = serializers.CharField(allow_blank=True)
    remarks = serializers.CharField(allow_blank=True)


class ReassignSerializer(VersionSerializer):
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all())
    note = serializers.CharField(required=False, allow_blank=True, default="")


class TaskCommentSerializer(serializers.ModelSerializer):
    author = UserRefSerializer(read_only=True)

    class Meta:
        model = TaskComment
        fields = ["id", "author", "body", "created_at"]
        read_only_fields = fields


class CommentCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=5000, allow_blank=True)


class TaskAttachmentSerializer(serializers.ModelSerializer):
    uploaded_by = UserRefSerializer(read_only=True)

    class Meta:
        model = TaskAttachment
        fields = ["id", "original_filename", "size_bytes", "sha256", "uploaded_by", "created_at"]
        read_only_fields = fields


class AttachmentUploadSerializer(serializers.Serializer):
    file = serializers.FileField()


class DeleteQuerySerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)


# --- Phase 5.1: Daily Activities and Admin (Boss) monitoring (read-only) ----------------------


class DailyActivitySerializer(serializers.Serializer):
    """One generated daily activity. Every time and state comes from the backend SLA engine;
    remaining_seconds is measured at response time (clients count down from it)."""

    task_id = serializers.IntegerField()
    reference = serializers.CharField()
    title = serializers.CharField()
    responsibility = ResponsibilityRefSerializer(allow_null=True)
    occurrence_date = serializers.DateField(allow_null=True)
    scheduled_start = serializers.DateTimeField(
        allow_null=True,
        help_text="Kept for existing clients: the SLA start, or the schedule time while the SLA "
        "has not started. Prefer scheduled_at and sla_start_at.",
    )
    scheduled_at = serializers.DateTimeField(
        allow_null=True, help_text="When the occurrence was scheduled (recorded at generation)."
    )
    sla_start_at = serializers.DateTimeField(
        allow_null=True,
        help_text="When the resolution SLA runs from (a hold moves it); null while not started.",
    )
    arrived_overdue = serializers.BooleanField(
        allow_null=True,
        help_text="The resolution SLA was already overdue when the task was generated "
        "(system-caused). Null: not recorded (generated before this was tracked).",
    )
    ack_arrived_overdue = serializers.BooleanField(
        allow_null=True,
        help_text="The acknowledgment SLA was already overdue when the task was generated.",
    )
    deadline = serializers.DateTimeField(allow_null=True)
    status = serializers.CharField(help_text="Task workflow status (PENDING, IN_PROGRESS, ...).")
    sla_state = serializers.CharField(allow_null=True, help_text="NOT_STARTED ... OVERDUE.")
    sla_note = serializers.CharField(allow_null=True)
    remaining_seconds = serializers.IntegerField(
        allow_null=True, help_text="Seconds to the deadline at response time; negative = overdue."
    )
    completed_at = serializers.DateTimeField(allow_null=True)
    completion_result = serializers.CharField(
        allow_null=True, help_text="ON_TIME, LATE, NO_DEADLINE, or null while not completed."
    )
    is_overdue = serializers.BooleanField()
    assignee = EmployeeRefSerializer()


class DailyActivityListSerializer(serializers.Serializer):
    date = serializers.DateField()
    server_time = serializers.DateTimeField()
    activities = DailyActivitySerializer(many=True)


class AssignedTaskRowSerializer(serializers.Serializer):
    task_id = serializers.IntegerField()
    reference = serializers.CharField()
    title = serializers.CharField()
    priority = serializers.CharField()
    raised_by = UserRefSerializer(help_text="Who raised (created) the task.")
    department = DepartmentRefSerializer()
    category = CategoryRefSerializer(allow_null=True)
    assigned_at = serializers.DateTimeField()
    assigned_by = UserRefSerializer(help_text="Who made the current assignment.")
    deadline = serializers.DateTimeField(allow_null=True)
    status = serializers.CharField()
    sla_state = serializers.CharField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)
    completion_result = serializers.CharField(allow_null=True)
    completed_on_day = serializers.BooleanField()
    is_overdue = serializers.BooleanField()


class WorkCountsSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    completed = serializers.IntegerField()
    pending = serializers.IntegerField()
    overdue = serializers.IntegerField()
    completed_late = serializers.IntegerField()


class MonitoredEmployeeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()
    department = DepartmentRefSerializer()


class EmployeeWorkSummarySerializer(serializers.Serializer):
    employee = MonitoredEmployeeSerializer()
    daily_activity = WorkCountsSerializer()
    assigned_tasks = WorkCountsSerializer()


class OperationsSummarySerializer(serializers.Serializer):
    date = serializers.DateField()
    server_time = serializers.DateTimeField()
    employees = EmployeeWorkSummarySerializer(many=True)


class TeamOperationsSummarySerializer(OperationsSummarySerializer):
    """The Operations Manager's team view: the same summary plus the department it covers."""

    department = DepartmentRefSerializer()


class EmployeeDailyActivitiesSerializer(serializers.Serializer):
    date = serializers.DateField()
    server_time = serializers.DateTimeField()
    employee = MonitoredEmployeeSerializer()
    counts = WorkCountsSerializer()
    activities = DailyActivitySerializer(many=True)


class EmployeeAssignedTasksSerializer(serializers.Serializer):
    date = serializers.DateField()
    server_time = serializers.DateTimeField()
    employee = MonitoredEmployeeSerializer()
    counts = WorkCountsSerializer()
    tasks = AssignedTaskRowSerializer(many=True)
