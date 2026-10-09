"""Valid login = first login on a Company Calendar working day (approved R20, decided with the
calendar in Phase 5). Existing facts are classified with the calendar's weekly pattern."""

from django.db import migrations, models


def classify_existing(apps, schema_editor):
    Calendar = apps.get_model("calendars", "BusinessCalendar")
    Day = apps.get_model("calendars", "CalendarDay")
    Login = apps.get_model("org", "EmployeeDailyLogin")
    calendar = Calendar.objects.filter(code="COMPANY").first()
    if calendar is None:
        return
    entries = {d.date: d.kind for d in Day.objects.filter(calendar=calendar)}
    for login in Login.objects.all().only("id", "work_date"):
        day = login.work_date
        if day in entries:
            valid = entries[day] == "SPECIAL_WORKING_DAY"
        elif day.weekday() in calendar.weekly_off_weekdays:
            valid = False
        elif day.weekday() == 5:
            valid = ((day.day - 1) // 7 + 1) in calendar.saturday_working_occurrences
        else:
            valid = True
        if not valid:
            Login.objects.filter(pk=login.pk).update(is_valid=False)


class Migration(migrations.Migration):
    dependencies = [
        ("org", "0002_seed_departments"),
        ("calendars", "0002_seed_company_calendar"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeedailylogin",
            name="is_valid",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(classify_existing, migrations.RunPython.noop),
    ]
