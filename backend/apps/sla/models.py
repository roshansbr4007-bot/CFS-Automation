"""SLA rules (versioned, never edited in place), task SLA clocks and Admin SLA settings.

Workflow status lives on Task; SLA state lives here and is never a workflow status.
"""

from django.conf import settings
from django.db import models
from django.db.models import F, Q


class RuleType(models.TextChoices):
    DURATION = "DURATION", "Fixed duration"
    END_OF_DAY = "END_OF_DAY", "End of the company working day"
    TRANSACTION_CUTOFF = "TRANSACTION_CUTOFF", "Transaction cutoff (Phase 7)"


class ClockType(models.TextChoices):
    CALENDAR = "CALENDAR", "Calendar hours (24/7)"
    BUSINESS = "BUSINESS", "Business hours (calendar engine, later phase)"


class Trigger(models.TextChoices):
    ASSIGNMENT = "ASSIGNMENT", "Assignment"
    LOGIN = "LOGIN", "Employee login"
    FIXED_TIME = "FIXED_TIME", "Fixed time"
    EVENT = "EVENT", "Event"
    DEPENDENCY = "DEPENDENCY", "Dependency completion"


class ClockKind(models.TextChoices):
    ACK = "ACK", "Acknowledgment"
    RESOLUTION = "RESOLUTION", "Resolution"


class SlaState(models.TextChoices):
    NOT_STARTED = "NOT_STARTED", "Not started"
    ON_TRACK = "ON_TRACK", "On track"
    WARNING = "WARNING", "Warning"
    CRITICAL = "CRITICAL", "Critical"
    OVERDUE = "OVERDUE", "Overdue"


class Outcome(models.TextChoices):
    MET = "MET", "Met"
    MISSED = "MISSED", "Missed"


class StopReason(models.TextChoices):
    ACKNOWLEDGED = "ACKNOWLEDGED", "Acknowledged"
    COMPLETED = "COMPLETED", "Task completed"
    CANCELLED = "CANCELLED", "Task cancelled"
    REASSIGNED = "REASSIGNED", "Task reassigned"
    NOT_REQUIRED = "NOT_REQUIRED", "Acknowledgment no longer required"


class SlaRule(models.Model):
    code = models.CharField(max_length=40)
    version = models.PositiveIntegerField(default=1)
    name = models.CharField(max_length=120)
    rule_type = models.CharField(max_length=20, choices=RuleType.choices)
    clock = models.CharField(max_length=10, choices=ClockType.choices, default=ClockType.CALENDAR)
    duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    warning_pct = models.PositiveSmallIntegerField(default=50)
    critical_pct = models.PositiveSmallIntegerField(default=75)
    overdue_pct = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)
    supersedes = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sla_rule"
        ordering = ["code", "-version"]
        default_permissions = ()
        permissions = [("manage_sla_rules", "Supersede SLA rules and edit SLA settings")]
        constraints = [
            models.UniqueConstraint(fields=["code", "version"], name="sla_rule_code_version_uniq"),
            models.UniqueConstraint(
                fields=["code"], condition=Q(is_active=True), name="sla_rule_one_active_version"
            ),
            models.CheckConstraint(
                condition=~Q(rule_type="DURATION") | Q(duration_minutes__gt=0),
                name="sla_rule_duration_needs_minutes",
            ),
            models.CheckConstraint(
                condition=Q(warning_pct__gt=0)
                & Q(warning_pct__lt=F("critical_pct"))
                & Q(critical_pct__lt=F("overdue_pct")),
                name="sla_rule_thresholds_ordered",
            ),
        ]

    def __str__(self):
        return f"{self.code} v{self.version}"


class TaskSla(models.Model):
    """One SLA clock. Values from the rule are snapshotted when the clock is created, so a
    later rule change never moves an existing deadline. Old clocks are never deleted."""

    task = models.ForeignKey("tasks.Task", on_delete=models.PROTECT, related_name="sla_clocks")
    kind = models.CharField(max_length=10, choices=ClockKind.choices)
    rule = models.ForeignKey(SlaRule, on_delete=models.PROTECT, related_name="clocks")
    rule_snapshot = models.JSONField()
    trigger = models.CharField(max_length=12, choices=Trigger.choices)
    assignment = models.ForeignKey(
        "tasks.TaskAssignment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    start_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    state = models.CharField(
        max_length=12, choices=SlaState.choices, default=SlaState.NOT_STARTED
    )
    warning_at = models.DateTimeField(null=True, blank=True)
    critical_at = models.DateTimeField(null=True, blank=True)
    overdue_at = models.DateTimeField(null=True, blank=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    stop_reason = models.CharField(max_length=14, choices=StopReason.choices, blank=True)
    outcome = models.CharField(max_length=8, choices=Outcome.choices, null=True, blank=True)
    is_current = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "task_sla"
        ordering = ["created_at", "id"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["task", "kind"], condition=Q(is_current=True), name="sla_one_current_clock"
            ),
            models.CheckConstraint(
                condition=Q(start_at__isnull=True, due_at__isnull=True)
                | Q(start_at__isnull=False, due_at__isnull=False),
                name="sla_start_and_due_together",
            ),
        ]
        indexes = [models.Index(fields=["stopped_at", "start_at"], name="sla_running_idx")]

    def __str__(self):
        return f"{self.task_id} {self.kind}"


class SlaSetting(models.Model):
    """Single row of Admin-set times. Both start empty (approved v4: no default)."""

    company_work_end = models.TimeField(null=True, blank=True)
    login_fallback_time = models.TimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sla_setting"
        default_permissions = ()

    def __str__(self):
        return "SLA settings"

    @classmethod
    def load(cls) -> "SlaSetting":
        return cls.objects.get_or_create(pk=1)[0]


class PrioritySla(models.Model):
    """Phase 5.2: which SLA rule a manually raised task of a given priority uses.

    Configuration data only; nothing is seeded, so no duration is invented. While a priority has
    no active mapping, manual tasks keep the existing task-type SLA behaviour. Scheduled daily
    activities never use this table: their SLA always comes from their schedule's task type.
    """

    priority = models.CharField(max_length=8, unique=True)  # a tasks.TaskPriority value
    rule_code = models.CharField(max_length=40)  # an SlaRule code (its active version is used)
    is_active = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sla_priority_rule"
        ordering = ["priority"]
        default_permissions = ()

    def __str__(self):
        return f"{self.priority} -> {self.rule_code}"
