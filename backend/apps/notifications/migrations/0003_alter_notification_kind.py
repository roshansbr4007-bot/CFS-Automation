from django.db import migrations, models


class Migration(migrations.Migration):
    """Phase 9: two in-app notification kinds for overdue cases (choices only; no schema)."""

    dependencies = [("notifications", "0002_alter_notification_kind")]

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
                ],
                max_length=16,
            ),
        ),
    ]
