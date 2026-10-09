from django.conf import settings
from django.db import models
from django.utils import timezone


class AuditLogImmutable(Exception):
    """Raised on any attempt to change or remove an audit row."""


class AuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AuditLogImmutable("audit_log is append-only")

    def delete(self):
        raise AuditLogImmutable("audit_log is append-only")


class AuditLog(models.Model):
    """Append-only record of every important write (v4 table 21).

    Also protected in PostgreSQL by a trigger (migration 0002) that refuses UPDATE, DELETE and
    TRUNCATE. Rows never change, so there is `occurred_at` but no `updated_at`.
    """

    id = models.BigAutoField(primary_key=True)
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Null means the system acted.",
    )
    action = models.CharField(max_length=64)
    entity_type = models.CharField(max_length=64)
    entity_id = models.CharField(max_length=64)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    request_id = models.UUIDField(null=True, blank=True)
    context = models.JSONField(default=dict, blank=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        db_table = "audit_log"
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"], name="audit_entity_idx"),
            models.Index(fields=["actor_user", "occurred_at"], name="audit_actor_time_idx"),
            models.Index(fields=["action"], name="audit_action_idx"),
        ]
        default_permissions = ()
        permissions = [("view_audit_log", "Read the audit log")]

    def __str__(self):
        when = f"{self.occurred_at:%Y-%m-%d %H:%M}"
        return f"{when} {self.action} {self.entity_type}:{self.entity_id}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AuditLogImmutable("audit_log is append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditLogImmutable("audit_log is append-only")
