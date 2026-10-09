"""Phase 6A: observability data for the Admin Command Center. Nothing here drives business rules."""

from django.db import models


class SchedulerHeartbeat(models.Model):
    """The latest run of one scheduled job (one row per job, updated in place).

    Written by the existing Celery task wrappers around the unchanged business functions; a failure
    to write it is logged and never affects the business task.
    """

    STATUS_RUNNING = "RUNNING"
    STATUS_SUCCESS = "SUCCESS"
    STATUS_FAILED = "FAILED"

    job = models.CharField(max_length=80, unique=True)
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_finished_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=10, blank=True)
    last_summary = models.JSONField(default=dict, blank=True)
    last_error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "command_center_scheduler_heartbeat"
        ordering = ["job"]
        default_permissions = ()

    def __str__(self):
        return f"{self.job}: {self.last_status or 'never run'}"
