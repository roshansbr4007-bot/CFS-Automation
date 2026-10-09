"""In-app notifications with optional email delivery.

dedup_key is unique: a given event can notify a given person only once, however many times
the SLA checker runs or restarts.
"""

from django.conf import settings
from django.db import models


class NotificationKind(models.TextChoices):
    SLA_WARNING = "SLA_WARNING", "SLA warning (50%)"
    SLA_CRITICAL = "SLA_CRITICAL", "SLA critical (75%)"
    SLA_OVERDUE = "SLA_OVERDUE", "SLA overdue (100%)"
    SCHEDULE_WARNING = "SCHEDULE_WARNING", "Scheduled responsibility not generated"
    # Phase 9 (in-app only; no email): the overdue case reason / review steps.
    OVERDUE_REASON = "OVERDUE_REASON", "Overdue reason needed"
    OVERDUE_REVIEW = "OVERDUE_REVIEW", "Overdue reason submitted — review needed"


class EmailStatus(models.TextChoices):
    NOT_REQUIRED = "NOT_REQUIRED", "No email"
    PENDING = "PENDING", "Waiting to send"
    SENT = "SENT", "Sent"
    FAILED = "FAILED", "Failed, will retry"


class Notification(models.Model):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="notifications"
    )
    task = models.ForeignKey(
        "tasks.Task", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    clock = models.ForeignKey(
        "sla.TaskSla", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    kind = models.CharField(max_length=16, choices=NotificationKind.choices)
    title = models.CharField(max_length=200)
    body = models.TextField()
    dedup_key = models.CharField(max_length=120, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)
    email_status = models.CharField(
        max_length=14, choices=EmailStatus.choices, default=EmailStatus.NOT_REQUIRED
    )
    email_attempts = models.PositiveSmallIntegerField(default=0)
    email_sent_at = models.DateTimeField(null=True, blank=True)
    email_error = models.TextField(blank=True)

    class Meta:
        db_table = "notification"
        ordering = ["-created_at", "-id"]
        default_permissions = ()
        indexes = [
            models.Index(fields=["recipient", "read_at"], name="notification_inbox_idx"),
            models.Index(fields=["email_status"], name="notification_email_idx"),
        ]

    def __str__(self):
        return f"{self.recipient_id}: {self.title}"
