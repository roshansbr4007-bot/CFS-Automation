"""Phase B: WEEKLY and specific-date (ONCE) schedules.

Additive: two new fields with safe defaults (existing rows get weekdays=[] and run_date=NULL,
which satisfy the new rules), two new frequency choices, and the per-frequency check constraint
widened from DAILY/MONTHLY to all four types. No data migration, no backfill.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("recurring", "0003_owner_same_day_correction")]

    operations = [
        migrations.AddField(
            model_name="recurringschedule",
            name="weekdays",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="recurringschedule",
            name="run_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="recurringschedule",
            name="frequency",
            field=models.CharField(
                choices=[
                    ("DAILY", "Daily (working days)"),
                    ("MONTHLY", "Monthly"),
                    ("WEEKLY", "Weekly"),
                    ("ONCE", "Specific date (once)"),
                ],
                max_length=8,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="recurringschedule",
            name="recurring_schedule_day_of_month_chk",
        ),
        migrations.AddConstraint(
            model_name="recurringschedule",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("frequency", "DAILY"))
                    & models.Q(("day_of_month__isnull", True))
                    & models.Q(("weekdays", []))
                    & models.Q(("run_date__isnull", True))
                )
                | (
                    models.Q(("frequency", "MONTHLY"))
                    & models.Q(("day_of_month__gte", 1))
                    & models.Q(("day_of_month__lte", 28))
                    & models.Q(("weekdays", []))
                    & models.Q(("run_date__isnull", True))
                )
                | (
                    models.Q(("frequency", "WEEKLY"))
                    & models.Q(("day_of_month__isnull", True))
                    & ~models.Q(("weekdays", []))
                    & models.Q(("weekdays__contained_by", [0, 1, 2, 3, 4, 5, 6]))
                    & models.Q(("run_date__isnull", True))
                )
                | (
                    models.Q(("frequency", "ONCE"))
                    & models.Q(("day_of_month__isnull", True))
                    & models.Q(("weekdays", []))
                    & models.Q(("run_date__isnull", False))
                ),
                name="recurring_schedule_frequency_fields_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="recurringschedule",
            constraint=models.CheckConstraint(
                condition=~models.Q(("frequency", "ONCE"))
                | (
                    models.Q(("effective_to__isnull", False))
                    & models.Q(("effective_from__lte", models.F("run_date")))
                    & models.Q(("effective_to__gte", models.F("run_date")))
                ),
                name="recurring_schedule_once_window_chk",
            ),
        ),
    ]
