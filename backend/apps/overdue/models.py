"""Overdue case (Phase 9).

One case per overdue RESOLUTION SLA clock (database-unique). Facts about the task and the SLA
are copied when the case opens, so later edits, reassignments or rule changes never rewrite
history. The employee's REASON and the reviewer's authoritative CAUSE are separate fields: the
employee's answer is never the final cause. A REVIEWED case is immutable.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.errors import ConflictError


class OverdueCause(models.TextChoices):
    """One controlled vocabulary for the employee reason and the reviewer cause (P9-1)."""

    DEPENDENCY = "DEPENDENCY", "Dependency"
    EMPLOYEE = "EMPLOYEE", "Employee"
    SYSTEM = "SYSTEM", "System"
    CLIENT = "CLIENT", "Client"
    OTHER = "OTHER", "Other"


class OverdueStatus(models.TextChoices):
    OPEN = "OPEN", "Reason needed"
    REASON_SUBMITTED = "REASON_SUBMITTED", "Reason submitted"
    REVIEWED = "REVIEWED", "Reviewed"


class OpenedVia(models.TextChoices):
    TICK = "TICK", "SLA checker"
    COMPLETION = "COMPLETION", "Completed after the deadline"
    REPAIR = "REPAIR", "Repair command"


class ReviewedCaseLocked(ConflictError):
    code = "overdue_case_reviewed"
    message = "A reviewed overdue case cannot be changed."


class OverdueCase(models.Model):
    clock = models.OneToOneField(
        "sla.TaskSla", on_delete=models.PROTECT, related_name="overdue_case"
    )
    task = models.ForeignKey("tasks.Task", on_delete=models.PROTECT, related_name="overdue_cases")
    # The assignee when the clock became overdue (approved Q5): this employee answers.
    employee = models.ForeignKey(
        "org.Employee", on_delete=models.PROTECT, related_name="overdue_cases"
    )
    # The TASK's department at that moment (approved Q3): an Operations Manager's review scope.
    department = models.ForeignKey("org.Department", on_delete=models.PROTECT, related_name="+")
    # --- facts copied when the case opens (read-only afterwards) ---
    task_title = models.CharField(max_length=200)
    task_creator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    task_assigned_at = models.DateTimeField()
    priority = models.CharField(max_length=8)
    category_name = models.CharField(max_length=100, blank=True)
    sla_start_at = models.DateTimeField()
    sla_due_at = models.DateTimeField()
    sla_rule_code = models.CharField(max_length=40)
    sla_rule_name = models.CharField(max_length=120)
    overdue_at = models.DateTimeField()  # the moment the clock counted as overdue
    opened_at = models.DateTimeField(auto_now_add=True)
    opened_via = models.CharField(max_length=10, choices=OpenedVia.choices)
    status = models.CharField(
        max_length=16, choices=OverdueStatus.choices, default=OverdueStatus.OPEN
    )
    # --- the employee's reason (never the final cause) ---
    reason_category = models.CharField(max_length=10, choices=OverdueCause.choices, blank=True)
    explanation = models.TextField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # --- the reviewer's authoritative cause ---
    cause = models.CharField(max_length=10, choices=OverdueCause.choices, blank=True)
    review_remark = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "overdue_case"
        ordering = ["-opened_at", "-id"]
        default_permissions = ()
        permissions = [
            ("view_all_cases", "Can view every overdue case"),
            ("review_team_cases", "Can review overdue cases of tasks in own department"),
            ("review_all_cases", "Can review every overdue case"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(status="OPEN")
                | (~Q(reason_category="") & Q(submitted_at__isnull=False)),
                name="overdue_reason_before_review",
            ),
            models.CheckConstraint(
                condition=~Q(status="REVIEWED")
                | (~Q(cause="") & ~Q(review_remark="") & Q(reviewed_at__isnull=False)),
                name="overdue_review_complete",
            ),
        ]
        indexes = [models.Index(fields=["status", "department"], name="overdue_queue_idx")]

    def save(self, *args, **kwargs):
        if self.pk:
            stored = OverdueCase.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if stored == OverdueStatus.REVIEWED:
                raise ReviewedCaseLocked()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Overdue case {self.pk} ({self.status}) for task {self.task_id}"
