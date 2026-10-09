"""Seeds the Company Calendar's approved weekly pattern: Sunday off; Saturdays working only on
the 1st and 3rd occurrence. No holidays are seeded: Admin enters them."""

from django.db import migrations

SUNDAY = 6


def seed(apps, schema_editor):
    BusinessCalendar = apps.get_model("calendars", "BusinessCalendar")
    BusinessCalendar.objects.get_or_create(
        code="COMPANY",
        defaults={
            "name": "Company Calendar",
            "calendar_type": "COMPANY",
            "weekly_off_weekdays": [SUNDAY],
            "saturday_working_occurrences": [1, 3],
        },
    )


def unseed(apps, schema_editor):
    BusinessCalendar = apps.get_model("calendars", "BusinessCalendar")
    BusinessCalendar.objects.filter(code="COMPANY", days__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("calendars", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]
