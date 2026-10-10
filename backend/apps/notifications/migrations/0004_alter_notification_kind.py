from django.db import migrations, models


class Migration(migrations.Migration):
    """Responsibility owner change: one in-app notification kind for the previous owner when
    today's task moves to the new owner (choices only; no schema change, the SQL is a no-op)."""

    dependencies = [("notifications", "0003_alter_notification_kind")]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="kind",
            field=models.CharField(
                choices=[
                    ("SLA_WARNING", "SLA warning (50%)"),
                    ("SLA_CRITICAL", "SLA critical (75%)"),
                    ("SLA_OVERDUE", "SLA overdue (100%)"),
                    ("SCHEDULE_WARNING", "Scheduled responsibility not generated"),
                    ("OVERDUE_REASON", "Overdue reason needed"),
                    ("OVERDUE_REVIEW", "Overdue reason submitted — review needed"),
                    ("TASK_REASSIGNED", "Task moved to a new responsibility owner"),
                ],
                max_length=16,
            ),
        ),
    ]
