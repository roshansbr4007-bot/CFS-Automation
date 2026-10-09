"""Responsibilities (recurring business duties), who owns them over time, their schedules, and
the occurrence ledger that makes generation idempotent.

Responsibility -> owner for a date (ResponsibilityOwner) -> generated Task (one occurrence).
Nothing names a permanent employee: ownership is a dated history, resolved per occurrence.
"""

from django.conf import settings
from django.db import models
from django.db.models import F, Q


class Responsibility(models.Model):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    department = models.ForeignKey(
        "org.Department", on_delete=models.PROTECT, related_name="responsibilities"
    )
    category = models.ForeignKey(
        "tasks.TaskCategory", on_delete=models.PROTECT, related_name="responsibilities"
    )
    # The task type that carries trigger, SLA rule, acknowledgment and verification defaults.
    template = models.ForeignKey(
        "tasks.TaskTemplate",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="responsibilities",
    )
    priority = models.CharField(
        max_length=8,
        choices=[("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High"), ("URGENT", "Urgent")],
        default="MEDIUM",
    )
    is_active = models.BooleanField(default=True)
    # Responsibility deadline (SLA), defined by HR / Admin: the code of a versioned DURATION
    # SlaRule ("RESP_<id>"). Empty = no responsibility deadline (the existing SLA applies).
    deadline_rule_code = models.CharField(max_length=40, blank=True, default="")
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "recurring_responsibility"
        ordering = ["name", "id"]
        default_permissions = ()
        permissions = [
            ("view_all_responsibilities", "See every responsibility and its schedules"),
            ("manage_team_responsibilities", "Manage responsibilities of one's own department"),
            ("manage_all_responsibilities", "Manage every responsibility"),
            ("manage_schedules", "Create and edit recurring schedules"),
        ]

    def __str__(self):
        return self.name


class ResponsibilityOwner(models.Model):
    """Who owns a responsibility from effective_from to effective_to (inclusive; open = current).
    Rows are closed, never rewritten, so ownership history stays queryable.

    Phase 5.2: a same-day correction marks the mistaken current row as superseded (kept for
    history and audit, never resolved as owner) instead of deleting it."""

    responsibility = models.ForeignKey(
        Responsibility, on_delete=models.PROTECT, related_name="owners"
    )
    employee = models.ForeignKey(
        "org.Employee", on_delete=models.PROTECT, related_name="owned_responsibilities"
    )
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    superseded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        db_table = "recurring_responsibility_owner"
        ordering = ["effective_from", "id"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["responsibility"],
                condition=Q(effective_to__isnull=True) & Q(superseded_at__isnull=True),
                name="recurring_one_open_owner",
            ),
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True) | Q(effective_to__gte=F("effective_from")),
                name="recurring_owner_period_valid",
            ),
        ]

    def __str__(self):
        return f"{self.responsibility_id} -> {self.employee_id} from {self.effective_from}"


class Frequency(models.TextChoices):
    DAILY = "DAILY", "Daily (working days)"
    MONTHLY = "MONTHLY", "Monthly"
    WEEKLY = "WEEKLY", "Weekly"  # Phase B
    ONCE = "ONCE", "Specific date (once)"  # Phase B


class NonWorkingDayPolicy(models.TextChoices):
    SKIP = "SKIP", "Skip the occurrence"
    NEXT_WORKING_DAY = "NEXT_WORKING_DAY", "Move to the next working day"
    PREVIOUS_WORKING_DAY = "PREVIOUS_WORKING_DAY", "Move to the previous working day"


class RecurringSchedule(models.Model):
    responsibility = models.ForeignKey(
        Responsibility, on_delete=models.PROTECT, related_name="schedules"
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    frequency = models.CharField(max_length=8, choices=Frequency.choices)
    run_time = models.TimeField()  # IST wall-clock
    day_of_month = models.PositiveSmallIntegerField(null=True, blank=True)
    # Phase B. WEEKLY: weekday numbers 0=Monday .. 6=Sunday (distinct, sorted; the company
    # calendar's convention). ONCE: the single date (its effective window is derived from it).
    weekdays = models.JSONField(default=list, blank=True)
    run_date = models.DateField(null=True, blank=True)
    non_working_day_policy = models.CharField(
        max_length=20, choices=NonWorkingDayPolicy.choices, default=NonWorkingDayPolicy.SKIP
    )
    is_active = models.BooleanField(default=True)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "recurring_schedule"
        ordering = ["responsibility_id", "id"]
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(frequency="DAILY")
                    & Q(day_of_month__isnull=True)
                    & Q(weekdays=[])
                    & Q(run_date__isnull=True)
                )
                | (
                    Q(frequency="MONTHLY")
                    & Q(day_of_month__gte=1)
                    & Q(day_of_month__lte=28)
                    & Q(weekdays=[])
                    & Q(run_date__isnull=True)
                )
                | (
                    Q(frequency="WEEKLY")
                    & Q(day_of_month__isnull=True)
                    & ~Q(weekdays=[])
                    & Q(weekdays__contained_by=[0, 1, 2, 3, 4, 5, 6])
                    & Q(run_date__isnull=True)
                )
                | (
                    Q(frequency="ONCE")
                    & Q(day_of_month__isnull=True)
                    & Q(weekdays=[])
                    & Q(run_date__isnull=False)
                ),
                name="recurring_schedule_frequency_fields_chk",
            ),
            models.CheckConstraint(
                condition=~Q(frequency="ONCE")
                | (
                    Q(effective_to__isnull=False)
                    & Q(effective_from__lte=F("run_date"))
                    & Q(effective_to__gte=F("run_date"))
                ),
                name="recurring_schedule_once_window_chk",
            ),
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True) | Q(effective_to__gte=F("effective_from")),
                name="recurring_schedule_period_valid",
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.frequency})"


class OccurrenceStatus(models.TextChoices):
    GENERATED = "GENERATED", "Generated"
    SKIPPED = "SKIPPED", "Skipped (no responsible employee)"
    MISSED = "MISSED", "Missed (scheduler unavailable)"
    FAILED = "FAILED", "Failed"


class ScheduleOccurrence(models.Model):
    """The ledger: one row per schedule and business date, written in the same transaction as
    the task. Its unique key is what makes any number of scheduler runs safe."""

    schedule = models.ForeignKey(
        RecurringSchedule, on_delete=models.PROTECT, related_name="occurrences"
    )
    occurrence_date = models.DateField()
    status = models.CharField(max_length=10, choices=OccurrenceStatus.choices)
    # Cleared (not cascaded) when an authorized user physically deletes the task.
    task = models.ForeignKey(
        "tasks.Task", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    assignee = models.ForeignKey(
        "org.Employee", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    generated_at = models.DateTimeField(null=True, blank=True)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "recurring_occurrence"
        ordering = ["-occurrence_date", "-id"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["schedule", "occurrence_date"], name="recurring_occurrence_uniq"
            )
        ]
        indexes = [
            models.Index(fields=["status", "occurrence_date"], name="recurring_occ_status_idx")
        ]

    def __str__(self):
        return f"{self.schedule_id} {self.occurrence_date} {self.status}"
