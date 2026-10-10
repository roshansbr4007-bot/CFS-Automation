from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.org.api.serializers import (
    DepartmentRefSerializer,
    EmployeeRefSerializer,
    VersionSerializer,
)
from apps.org.models import Department, Employee
from apps.tasks.api.serializers import (
    CategoryRefSerializer,
    ResponsibilityRefSerializer,
    TemplateRefSerializer,
    UserRefSerializer,
)
from apps.tasks.models import TaskCategory, TaskPriority, TaskTemplate

from ..models import (
    Frequency,
    NonWorkingDayPolicy,
    RecurringSchedule,
    Responsibility,
    ResponsibilityOwner,
    ScheduleOccurrence,
)
from ..services import (
    can_manage,
    can_manage_deadline,
    can_manage_owner,
    can_manage_schedule,
    current_owner_row,
    deadline_minutes,
    today_ist,
)


def _can(context, kind: str, responsibility) -> bool:
    """Server-decided permission flag for the signed-in user, cached per request and department
    (a list must not run one scope query per row)."""
    request = context.get("request")
    if request is None or not request.user.is_authenticated:
        return False
    cache = context.setdefault("_can_cache", {})
    key = (kind, responsibility.department_id)
    if key not in cache:
        check = can_manage if kind == "responsibility" else can_manage_schedule
        cache[key] = check(request.user, responsibility)
    return cache[key]


class ResponsibilityOwnerSerializer(serializers.ModelSerializer):
    employee = EmployeeRefSerializer(read_only=True)
    assigned_by = UserRefSerializer(read_only=True)
    superseded_by = UserRefSerializer(read_only=True, allow_null=True)

    class Meta:
        model = ResponsibilityOwner
        fields = [
            "id",
            "employee",
            "effective_from",
            "effective_to",
            "note",
            "assigned_by",
            "created_at",
            "superseded_at",
            "superseded_by",
        ]
        read_only_fields = fields


class TransferredTaskSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    reference = serializers.CharField()
    title = serializers.CharField()


class TodayGenerationSerializer(serializers.Serializer):
    schedule_id = serializers.IntegerField(allow_null=True)
    occurrence_date = serializers.DateField()
    result = serializers.ChoiceField(
        choices=["generated", "recovered", "existing", "skipped", "missed", "failed", "not_due"],
        help_text="existing: today's task exists. skipped / missed / failed: today's occurrence "
        "was not generated (see detail). not_due: today's run time has not come yet (it is "
        "generated at its run time) or the schedule has no occurrence today.",
    )
    detail = serializers.CharField(allow_null=True)


class OwnerAssignmentResultSerializer(ResponsibilityOwnerSerializer):
    """The new ownership row, plus what happened to TODAY's work when the owner starts today:
    the open tasks moved from the previous owner, and today's immediate generation outcome.
    Both are empty when the owner starts on a later date."""

    transferred_tasks = TransferredTaskSerializer(many=True, read_only=True)
    today_generation = TodayGenerationSerializer(many=True, read_only=True)

    class Meta(ResponsibilityOwnerSerializer.Meta):
        fields = [*ResponsibilityOwnerSerializer.Meta.fields, "transferred_tasks",
                  "today_generation"]
        read_only_fields = fields


class ResponsibilitySerializer(serializers.ModelSerializer):
    department = DepartmentRefSerializer(read_only=True)
    category = CategoryRefSerializer(read_only=True)
    template = TemplateRefSerializer(read_only=True, allow_null=True)
    current_owner = serializers.SerializerMethodField()
    active_schedule_count = serializers.SerializerMethodField()
    can_manage = serializers.SerializerMethodField()
    deadline_minutes = serializers.SerializerMethodField()
    can_manage_deadline = serializers.SerializerMethodField()
    can_manage_owner = serializers.SerializerMethodField()

    class Meta:
        model = Responsibility
        fields = [
            "id",
            "code",
            "name",
            "description",
            "department",
            "category",
            "template",
            "priority",
            "is_active",
            "current_owner",
            "active_schedule_count",
            "can_manage",
            "deadline_minutes",
            "can_manage_deadline",
            "can_manage_owner",
            "version",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(ResponsibilityOwnerSerializer(allow_null=True))
    def get_current_owner(self, responsibility) -> dict | None:
        row = current_owner_row(responsibility)
        return ResponsibilityOwnerSerializer(row).data if row else None

    def get_active_schedule_count(self, responsibility) -> int:
        """Schedules that are active and not ended (a future start still counts): 0 means
        nothing will ever be generated for this responsibility."""
        today = today_ist()
        return sum(
            1
            for s in responsibility.schedules.all()
            if s.is_active and (s.effective_to is None or s.effective_to >= today)
        )

    def get_can_manage(self, responsibility) -> bool:
        return _can(self.context, "responsibility", responsibility)

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_deadline_minutes(self, responsibility) -> int | None:
        """Responsibility deadline (SLA) in minutes; null = none (the existing SLA applies)."""
        return deadline_minutes(responsibility)

    def get_can_manage_deadline(self, responsibility) -> bool:
        """HR / Admin may define the deadline (server-decided; never an Operations Manager)."""
        request = self.context.get("request")
        return bool(
            request
            and request.user.is_authenticated
            and responsibility.is_active
            and can_manage_deadline(request.user)
        )

    def get_can_manage_owner(self, responsibility) -> bool:
        """HR / Admin may assign, change or end the owner (server-decided; locked rule A)."""
        request = self.context.get("request")
        return bool(
            request
            and request.user.is_authenticated
            and responsibility.is_active
            and can_manage_owner(request.user)
        )



class ResponsibilitySetupResultSerializer(ResponsibilitySerializer):
    """The created responsibility, plus today's immediate generation outcome when its owner
    starts today (empty otherwise)."""

    today_generation = serializers.SerializerMethodField()

    class Meta(ResponsibilitySerializer.Meta):
        fields = [*ResponsibilitySerializer.Meta.fields, "today_generation"]
        read_only_fields = fields

    @extend_schema_field(TodayGenerationSerializer(many=True))
    def get_today_generation(self, responsibility) -> list:
        return TodayGenerationSerializer(
            self.context.get("today_generation", []), many=True
        ).data

class ResponsibilityCreateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=40)
    name = serializers.CharField(max_length=120)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.all())
    category = serializers.PrimaryKeyRelatedField(queryset=TaskCategory.objects.all())
    template = serializers.PrimaryKeyRelatedField(
        queryset=TaskTemplate.objects.all(), required=False, allow_null=True, default=None
    )
    priority = serializers.ChoiceField(choices=TaskPriority.choices, default=TaskPriority.MEDIUM)


class ResponsibilityUpdateSerializer(VersionSerializer):
    name = serializers.CharField(max_length=120, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(), required=False
    )
    category = serializers.PrimaryKeyRelatedField(
        queryset=TaskCategory.objects.all(), required=False
    )
    template = serializers.PrimaryKeyRelatedField(
        queryset=TaskTemplate.objects.all(), required=False, allow_null=True
    )
    priority = serializers.ChoiceField(choices=TaskPriority.choices, required=False)
    is_active = serializers.BooleanField(required=False)
    # Responsibility deadline (SLA) in minutes; null clears it. HR / Admin only (403 otherwise).
    deadline_minutes = serializers.IntegerField(min_value=1, required=False, allow_null=True)


class AssignOwnerSerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all())
    effective_from = serializers.DateField()
    note = serializers.CharField(required=False, allow_blank=True, default="")


class EndOwnershipSerializer(serializers.Serializer):
    last_day = serializers.DateField()
    note = serializers.CharField(required=False, allow_blank=True, default="")


class RecurringScheduleSerializer(serializers.ModelSerializer):
    responsibility = ResponsibilityRefSerializer(read_only=True)
    weekdays = serializers.ListField(child=serializers.IntegerField(), read_only=True)  # Phase B
    can_manage = serializers.SerializerMethodField()

    class Meta:
        model = RecurringSchedule
        fields = [
            "id",
            "responsibility",
            "title",
            "description",
            "frequency",
            "run_time",
            "day_of_month",
            "weekdays",
            "run_date",
            "non_working_day_policy",
            "is_active",
            "effective_from",
            "effective_to",
            "can_manage",
            "version",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_can_manage(self, schedule) -> bool:
        return _can(self.context, "schedule", schedule.responsibility)


class _ScheduleStartMixin:
    """Phase B: a ONCE schedule's window is derived from run_date, so only it may omit the start.
    Every other frequency keeps the existing required-field error under the same key."""

    def validate(self, attrs):
        if attrs.get("frequency") != Frequency.ONCE and attrs.get("effective_from") is None:
            raise serializers.ValidationError({"effective_from": ["This field is required."]})
        return attrs


class ScheduleCreateSerializer(_ScheduleStartMixin, serializers.Serializer):
    responsibility = serializers.PrimaryKeyRelatedField(queryset=Responsibility.objects.all())
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    frequency = serializers.ChoiceField(choices=Frequency.choices)
    run_time = serializers.TimeField()
    day_of_month = serializers.IntegerField(required=False, allow_null=True, default=None)
    weekdays = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    run_date = serializers.DateField(required=False, allow_null=True, default=None)
    non_working_day_policy = serializers.ChoiceField(
        choices=NonWorkingDayPolicy.choices, default=NonWorkingDayPolicy.SKIP
    )
    effective_from = serializers.DateField(required=False, allow_null=True, default=None)
    effective_to = serializers.DateField(required=False, allow_null=True, default=None)


class ScheduleUpdateSerializer(VersionSerializer):
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    frequency = serializers.ChoiceField(choices=Frequency.choices, required=False)
    run_time = serializers.TimeField(required=False)
    day_of_month = serializers.IntegerField(required=False, allow_null=True)
    weekdays = serializers.ListField(child=serializers.IntegerField(), required=False)
    run_date = serializers.DateField(required=False, allow_null=True)
    non_working_day_policy = serializers.ChoiceField(
        choices=NonWorkingDayPolicy.choices, required=False
    )
    effective_from = serializers.DateField(required=False)
    effective_to = serializers.DateField(required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False)


class OccurrenceTaskRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    reference = serializers.CharField()
    status = serializers.CharField()


class ScheduleOccurrenceSerializer(serializers.ModelSerializer):
    responsibility = serializers.SerializerMethodField()
    schedule_title = serializers.CharField(source="schedule.title", read_only=True)
    task = OccurrenceTaskRefSerializer(read_only=True, allow_null=True)
    assignee = EmployeeRefSerializer(read_only=True, allow_null=True)

    class Meta:
        model = ScheduleOccurrence
        fields = [
            "id",
            "schedule",
            "schedule_title",
            "responsibility",
            "occurrence_date",
            "status",
            "task",
            "assignee",
            "generated_at",
            "detail",
            "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(ResponsibilityRefSerializer)
    def get_responsibility(self, occurrence) -> dict:
        return ResponsibilityRefSerializer(occurrence.schedule.responsibility).data


# --- Phase A: one-step setup -------------------------------------------------------------------


class SetupOwnerSerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all())
    effective_from = serializers.DateField()
    note = serializers.CharField(required=False, allow_blank=True, default="")


class SetupScheduleSerializer(_ScheduleStartMixin, serializers.Serializer):
    title = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    description = serializers.CharField(required=False, allow_blank=True, default="")
    frequency = serializers.ChoiceField(choices=Frequency.choices)
    run_time = serializers.TimeField()
    day_of_month = serializers.IntegerField(required=False, allow_null=True, default=None)
    weekdays = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    run_date = serializers.DateField(required=False, allow_null=True, default=None)
    non_working_day_policy = serializers.ChoiceField(
        choices=NonWorkingDayPolicy.choices, default=NonWorkingDayPolicy.SKIP
    )
    effective_from = serializers.DateField(required=False, allow_null=True, default=None)
    effective_to = serializers.DateField(required=False, allow_null=True, default=None)


class ResponsibilitySetupSerializer(ResponsibilityCreateSerializer):
    """Responsibility + optional owner + first schedule, created together (Phase A). The
    schedule title defaults to the responsibility name."""

    owner = SetupOwnerSerializer(required=False, allow_null=True, default=None)
    schedule = SetupScheduleSerializer()
    # Optional responsibility deadline (SLA) in minutes; HR / Admin only (403 otherwise).
    deadline_minutes = serializers.IntegerField(min_value=1, required=False, allow_null=True)
