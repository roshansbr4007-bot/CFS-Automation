"""Responsibility deadline (SLA): one optional field. Empty for every existing responsibility;
no data migration, no backfill."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("recurring", "0004_weekly_and_specific_date")]

    operations = [
        migrations.AddField(
            model_name="responsibility",
            name="deadline_rule_code",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
    ]
