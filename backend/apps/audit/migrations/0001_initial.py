import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("action", models.CharField(max_length=64)),
                ("entity_type", models.CharField(max_length=64)),
                ("entity_id", models.CharField(max_length=64)),
                ("old_value", models.JSONField(blank=True, null=True)),
                ("new_value", models.JSONField(blank=True, null=True)),
                ("occurred_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("ip", models.GenericIPAddressField(blank=True, null=True)),
                ("request_id", models.UUIDField(blank=True, null=True)),
                ("context", models.JSONField(blank=True, default=dict)),
                (
                    "actor_user",
                    models.ForeignKey(
                        blank=True,
                        help_text="Null means the system acted.",
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "audit_log",
                "ordering": ["-occurred_at", "-id"],
                "default_permissions": (),
                "permissions": [("view_audit_log", "Read the audit log")],
                "indexes": [
                    models.Index(fields=["entity_type", "entity_id"], name="audit_entity_idx"),
                    models.Index(fields=["actor_user", "occurred_at"], name="audit_actor_time_idx"),
                    models.Index(fields=["action"], name="audit_action_idx"),
                ],
            },
        ),
    ]
