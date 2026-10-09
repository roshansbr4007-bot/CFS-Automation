import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="BusinessCalendar",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=20, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("calendar_type", models.CharField(choices=[("COMPANY", "Company")], max_length=12)),
                ("weekly_off_weekdays", models.JSONField(default=list)),
                ("saturday_working_occurrences", models.JSONField(default=list)),
                ("is_active", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "calendar_business_calendar",
                "ordering": ["code"],
                "default_permissions": (),
                "permissions": [
                    ("manage_company_calendar", "Add and remove company holidays and special working days")
                ],
            },
        ),
        migrations.CreateModel(
            name="CalendarDay",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField()),
                (
                    "kind",
                    models.CharField(
                        choices=[("HOLIDAY", "Holiday"), ("SPECIAL_WORKING_DAY", "Special working day")],
                        max_length=20,
                    ),
                ),
                ("name", models.CharField(max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "calendar",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="days",
                        to="calendars.businesscalendar",
                    ),
                ),
            ],
            options={
                "db_table": "calendar_day",
                "ordering": ["date"],
                "default_permissions": (),
                "constraints": [models.UniqueConstraint(fields=("calendar", "date"), name="calendar_day_uniq")],
            },
        ),
    ]
