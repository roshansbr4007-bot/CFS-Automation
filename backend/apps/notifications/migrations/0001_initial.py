import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("sla", "0001_initial"),
        ("tasks", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Notification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("SLA_WARNING", "SLA warning (50%)"),
                            ("SLA_CRITICAL", "SLA critical (75%)"),
                            ("SLA_OVERDUE", "SLA overdue (100%)"),
                        ],
                        max_length=16,
                    ),
                ),
                ("title", models.CharField(max_length=200)),
                ("body", models.TextField()),
                ("dedup_key", models.CharField(max_length=120, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("read_at", models.DateTimeField(blank=True, null=True)),
                (
                    "email_status",
                    models.CharField(
                        choices=[
                            ("NOT_REQUIRED", "No email"),
                            ("PENDING", "Waiting to send"),
                            ("SENT", "Sent"),
                            ("FAILED", "Failed, will retry"),
                        ],
                        default="NOT_REQUIRED",
                        max_length=14,
                    ),
                ),
                ("email_attempts", models.PositiveSmallIntegerField(default=0)),
                ("email_sent_at", models.DateTimeField(blank=True, null=True)),
                ("email_error", models.TextField(blank=True)),
                (
                    "clock",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="sla.tasksla",
                    ),
                ),
                (
                    "recipient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="notifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="tasks.task",
                    ),
                ),
            ],
            options={
                "db_table": "notification",
                "ordering": ["-created_at", "-id"],
                "default_permissions": (),
                "indexes": [
                    models.Index(fields=["recipient", "read_at"], name="notification_inbox_idx"),
                    models.Index(fields=["email_status"], name="notification_email_idx"),
                ],
            },
        ),
    ]
