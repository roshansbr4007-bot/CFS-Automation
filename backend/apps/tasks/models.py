"""Phase 3 task engine.

Workflow status has exactly five values. Acknowledgment and verification are separate fields,
never statuses. No SLA field lives here: SLA clocks arrive in Phase 6 as their own table.
All writes go through apps.tasks.services so every change is audited in the same transaction.
"""

from django.conf import settings
from django.db import models
from django.db.models import F, Q


class TaskStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    BLOCKED = "BLOCKED", "Blocked"  # the UI may label this "On Hold"
    COMPLETED = "COMPLETED", "Completed"
    CANCELLED = "CANCELLED", "Cancelled"


class TaskPriority(models.TextChoices):
    LOW = "LOW", "Low"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"
    URGENT = "URGENT", "Urgent"


class TaskType(models.TextChoices):
    REGULAR = "REGULAR", "Regular"
    RECURRING = "RECURRING", "Recurring"  # created only by the Phase 5 generator
    ADHOC = "ADHOC", "Ad-hoc"
    INCENTIVE = "INCENTIVE", "Incentive"


class ReceivedSource(models.TextChoices):
    EMAIL = "EMAIL", "Email"
    API = "API", "API"
    IMPORT = "IMPORT", "Import"
    MANUAL = "MANUAL", "Manual"
    SYSTEM = "SYSTEM", "System"


class TaskSource(models.TextChoices):
    MANUAL = "MANUAL", "Manually created / assigned"
    SCHEDULED = "SCHEDULED", "Generated from a responsibility schedule"


class VerificationStatus(models.TextChoices):
    NOT_REQUIRED = "NOT_REQUIRED", "Not required"
    PENDING = "PENDING", "Pending"
    VERIFIED = "VERIFIED", "Verified"
    REJECTED = "REJECTED", "Rejected"


class CompletionSource(models.TextChoices):
    DIRECT = "DIRECT", "Marked complete by the assignee"
    # Future hook (approved, not built in Phase 3): an approved Completion Review Request.
    COMPLETION_REVIEW = "COMPLETION_REVIEW", "Approved completion review"


class TemplateTrigger(models.TextChoices):
    """What starts a template's RESOLUTION clock (approved v4 trigger model)."""

    ASSIGNMENT = "ASSIGNMENT", "Assignment"
    LOGIN = "LOGIN", "Employee login"
    FIXED_TIME = "FIXED_TIME", "Fixed time"
    EVENT = "EVENT", "Event"
    DEPENDENCY = "DEPENDENCY", "Dependency completion"


class DependencyState(models.TextChoices):
    """The state a prerequisite task must reach (Task Dependency Engine). Never interchangeable:
    COMPLETED is satisfied by completion, VERIFIED only by verification."""

    COMPLETED = "COMPLETED", "Completed"
    VERIFIED = "VERIFIED", "Verified"


class TaskTemplate(models.Model):
    """A kind of task (Feed Upload, Broker Mapping, ...). Admin-configurable data, never code.

    The SLA rule is referenced by code; its latest active version is snapshotted when a clock
    starts. Tasks without a template are Ad-hoc and have no RESOLUTION SLA.
    """

    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    department = models.ForeignKey(
        "org.Department", on_delete=models.PROTECT, related_name="task_templates"
    )
    resolution_rule_code = models.CharField(max_length=40, blank=True)
    trigger = models.CharField(
        max_length=12, choices=TemplateTrigger.choices, default=TemplateTrigger.ASSIGNMENT
    )
    fixed_time = models.TimeField(null=True, blank=True)  # IST wall-clock, FIXED_TIME only
    acknowledgment_required = models.BooleanField(default=False)
    verification_required = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    # Task Dependency Engine (apps.tasks.dependencies): a task of this type whose RESOLUTION
    # clock waits on the DEPENDENCY trigger starts when the matching task(s) of the prerequisite
    # type (same department, same business date) reach prerequisite_state. Configuration only.
    prerequisite_template = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="dependent_templates",
    )
    prerequisite_state = models.CharField(
        max_length=10, choices=DependencyState.choices, blank=True, default=""
    )

    class Meta:
        db_table = "tasks_template"
        ordering = ["name", "id"]
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=~Q(trigger="FIXED_TIME") | Q(fixed_time__isnull=False),
                name="tasks_template_fixed_time_chk",
            ),
            models.CheckConstraint(
                condition=Q(prerequisite_template__isnull=True, prerequisite_state="")
                | Q(
                    prerequisite_template__isnull=False,
                    prerequisite_state__in=["COMPLETED", "VERIFIED"],
                ),
                name="tasks_template_prerequisite_chk",
            ),
            models.CheckConstraint(
                condition=~Q(prerequisite_template=F("id")),
                name="tasks_template_not_own_prerequisite_chk",
            ),
        ]

    def __str__(self):
        return self.name


class TaskCategory(models.Model):
    """Business category of a task (Sales, Operations, ...). Admin-managed list; chosen by the
    task's creator and independent of the assignee and of the task type (TaskTemplate)."""

    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tasks_category"
        ordering = ["name", "id"]
        default_permissions = ()
        permissions = [("manage_task_categories", "Create and edit task categories")]

    def __str__(self):
        return self.name


class Task(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    task_type = models.CharField(max_length=16, choices=TaskType.choices, default=TaskType.ADHOC)
    priority = models.CharField(
        max_length=8, choices=TaskPriority.choices, default=TaskPriority.MEDIUM
    )
    status = models.CharField(max_length=16, choices=TaskStatus.choices, default=TaskStatus.PENDING)
    # Chosen by the creator; never derived from the assignee and never changed by reassignment.
    department = models.ForeignKey(
        "org.Department", on_delete=models.PROTECT, related_name="tasks"
    )
    # Required for new tasks (API); nullable only for tasks created before Phase 4.
    category = models.ForeignKey(
        TaskCategory, null=True, blank=True, on_delete=models.PROTECT, related_name="tasks"
    )
    # Where the task came from (Phase 5). Never changed after creation, not even by reassignment.
    source = models.CharField(max_length=10, choices=TaskSource.choices, default=TaskSource.MANUAL)
    responsibility = models.ForeignKey(
        "recurring.Responsibility",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="tasks",
    )
    schedule = models.ForeignKey(
        "recurring.RecurringSchedule",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="tasks",
    )
    occurrence_date = models.DateField(null=True, blank=True)  # business date (IST)
    generated_at = models.DateTimeField(null=True, blank=True)
    # Set at creation and never changed (keeps the SLA snapshot meaningful).
    template = models.ForeignKey(
        TaskTemplate, null=True, blank=True, on_delete=models.PROTECT, related_name="tasks"
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_tasks"
    )
    assigned_to = models.ForeignKey(
        "org.Employee", on_delete=models.PROTECT, related_name="assigned_tasks"
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="assigned_tasks"
    )
    assigned_at = models.DateTimeField()

    # When the work actually arrived (never a copy of created_at). Both set or both empty.
    received_at = models.DateTimeField(null=True, blank=True)
    received_at_source = models.CharField(
        max_length=8, choices=ReceivedSource.choices, null=True, blank=True
    )

    # EVENT templates only: when the triggering event was recorded (starts the SLA clock).
    trigger_at = models.DateTimeField(null=True, blank=True)

    acknowledgment_required = models.BooleanField(default=False)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)

    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="completed_tasks",
    )
    completion_recorded_at = models.DateTimeField(null=True, blank=True)
    completion_source = models.CharField(
        max_length=20, choices=CompletionSource.choices, null=True, blank=True
    )

    verification_required = models.BooleanField(default=False)
    verification_status = models.CharField(
        max_length=16,
        choices=VerificationStatus.choices,
        default=VerificationStatus.NOT_REQUIRED,
    )
    rework_count = models.PositiveIntegerField(default=0)

    blocked_reason = models.TextField(blank=True)
    blocked_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tasks_task"
        ordering = ["-created_at", "-id"]
        default_permissions = ()
        permissions = [
            ("create_task", "Create tasks (for oneself unless also allowed to assign)"),
            ("assign", "Assign tasks to other employees"),
            ("view_all_tasks", "See every task"),
            ("view_team_tasks", "See tasks of one's own department"),
            ("manage_team_tasks", "Assign, edit, cancel and verify tasks of own department"),
            ("manage_all_tasks", "Assign, edit, cancel and verify any task"),
            ("edit_all_tasks", "Edit and reassign any task"),
            ("delete_task", "Physically delete any task"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(received_at__isnull=True, received_at_source__isnull=True)
                | Q(received_at__isnull=False, received_at_source__isnull=False),
                name="tasks_received_pair_chk",
            ),
            models.CheckConstraint(
                condition=~Q(status="COMPLETED") | Q(completed_at__isnull=False),
                name="tasks_completed_has_time_chk",
            ),
            models.CheckConstraint(
                condition=(
                    Q(source="MANUAL") & Q(schedule__isnull=True) & Q(occurrence_date__isnull=True)
                )
                | (
                    Q(source="SCHEDULED")
                    & Q(responsibility__isnull=False)
                    & Q(schedule__isnull=False)
                    & Q(occurrence_date__isnull=False)
                    & Q(generated_at__isnull=False)
                ),
                name="tasks_source_fields_chk",
            ),
            # Second duplicate barrier after the occurrence ledger.
            models.UniqueConstraint(
                fields=["schedule", "occurrence_date"],
                condition=Q(schedule__isnull=False),
                name="tasks_one_task_per_occurrence",
            ),
        ]
        indexes = [
            models.Index(fields=["assigned_to", "status"], name="tasks_assignee_status_idx"),
            models.Index(fields=["created_by", "status"], name="tasks_creator_status_idx"),
            models.Index(fields=["department", "status"], name="tasks_dept_status_idx"),
        ]

    def __str__(self):
        return f"{self.reference} {self.title}"

    @property
    def reference(self) -> str:
        return f"T-{self.pk:06d}" if self.pk else "T-new"


class TaskAssignment(models.Model):
    """Every assignment and hand-off of a task, oldest first. Append-only."""

    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="assignments")
    from_employee = models.ForeignKey(
        "org.Employee", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    to_employee = models.ForeignKey("org.Employee", on_delete=models.PROTECT, related_name="+")
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    assigned_at = models.DateTimeField()
    note = models.TextField(blank=True)

    class Meta:
        db_table = "tasks_assignment"
        ordering = ["assigned_at", "id"]
        default_permissions = ()

    def __str__(self):
        return f"{self.task_id}: {self.from_employee_id} -> {self.to_employee_id}"


class VerificationDecision(models.TextChoices):
    VERIFIED = "VERIFIED", "Verified"
    REJECTED = "REJECTED", "Rejected"


class TaskVerification(models.Model):
    """One row per verification decision; rework cycles stay historically available."""

    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="verifications")
    cycle_no = models.PositiveIntegerField()
    submitted_at = models.DateTimeField()  # the completion being reviewed
    decision = models.CharField(max_length=8, choices=VerificationDecision.choices)
    rejection_reason = models.TextField(blank=True)
    remarks = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField()
    # Seconds from this rejection to the next completion (recorded for reporting).
    rework_seconds = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "tasks_verification"
        ordering = ["cycle_no"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["task", "cycle_no"], name="tasks_verification_cycle_uniq"
            ),
            models.CheckConstraint(
                condition=~Q(decision="REJECTED") | (~Q(rejection_reason="") & ~Q(remarks="")),
                name="tasks_rejection_needs_reason_chk",
            ),
        ]

    def __str__(self):
        return f"{self.task_id} cycle {self.cycle_no}: {self.decision}"


class CommentKind(models.TextChoices):
    COMMENT = "COMMENT", "Comment"
    # Written ONLY by complete_task, in the same transaction as the completion: what the assignee
    # reports having done. The comments API can never create one.
    WORK_RESPONSE = "WORK_RESPONSE", "Work response (submitted on completion)"


class TaskComment(models.Model):
    """Append-only: there is no edit or delete path. A WORK_RESPONSE comment is the assignee's
    report of the work performed, submitted with each completion (author = the assignee,
    created_at = when it was submitted); a reopened task gets a new one on its next completion."""

    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    body = models.TextField()
    kind = models.CharField(max_length=14, choices=CommentKind.choices, default=CommentKind.COMMENT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tasks_comment"
        ordering = ["created_at", "id"]
        default_permissions = ()

    def __str__(self):
        return f"{self.task_id} comment {self.pk}"


class TaskAttachment(models.Model):
    """Minimal, private attachment store. Files are only served through a permission check."""

    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="attachments")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    file = models.FileField(upload_to="task-attachments/%Y/%m/")
    original_filename = models.CharField(max_length=255)
    size_bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tasks_attachment"
        ordering = ["created_at", "id"]
        default_permissions = ()

    def __str__(self):
        return self.original_filename


class TaskDependency(models.Model):
    """prerequisite task -> dependent task (Task Dependency Engine, apps.tasks.dependencies).

    Links are made by the engine from task-type configuration, never by hand. satisfied_at is
    written once, when the prerequisite first reaches required_state, and never changed again.
    Both sides are PROTECT; deleting a task removes its links first (tasks.services.delete_task).
    """

    prerequisite = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="dependent_links")
    dependent = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="prerequisite_links")
    required_state = models.CharField(max_length=10, choices=DependencyState.choices)
    satisfied_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tasks_dependency"
        ordering = ["created_at", "id"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["prerequisite", "dependent"], name="tasks_dependency_pair_uniq"
            ),
            models.CheckConstraint(
                condition=~Q(prerequisite=F("dependent")), name="tasks_dependency_not_self_chk"
            ),
            models.CheckConstraint(
                condition=Q(required_state__in=["COMPLETED", "VERIFIED"]),
                name="tasks_dependency_state_chk",
            ),
        ]
        indexes = [
            models.Index(fields=["prerequisite", "satisfied_at"], name="tasks_dep_prereq_sat_idx"),
            models.Index(fields=["dependent", "satisfied_at"], name="tasks_dep_dependent_sat_idx"),
        ]

    def __str__(self):
        return f"{self.prerequisite_id} -> {self.dependent_id} ({self.required_state})"
