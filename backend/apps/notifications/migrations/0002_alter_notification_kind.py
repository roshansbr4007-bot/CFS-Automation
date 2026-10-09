from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("notifications", "0001_initial")]

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
                ],
                max_length=16,
            ),
        ),
    ]
