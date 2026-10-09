"""Business calendars (approved v4 design). Phase 5 builds the Company Calendar; the
Transaction Calendar (calendar_type TRANSACTION) belongs to the later transaction-SLA phase.

Weekly pattern and Saturday rule are data on the calendar row; holidays and special working
days are CalendarDay rows. Nothing here is hard-coded per date.
"""

from django.db import models


class CalendarType(models.TextChoices):
    COMPANY = "COMPANY", "Company"


class BusinessCalendar(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    calendar_type = models.CharField(max_length=12, choices=CalendarType.choices)
    # Python weekday numbers (Monday=0 ... Sunday=6) that are always off.
    weekly_off_weekdays = models.JSONField(default=list)
    # Which Saturdays of a month are working (1st..5th), e.g. [1, 3].
    saturday_working_occurrences = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "calendar_business_calendar"
        ordering = ["code"]
        default_permissions = ()
        permissions = [
            ("manage_company_calendar", "Add and remove company holidays and special working days")
        ]

    def __str__(self):
        return self.name


class DayKind(models.TextChoices):
    HOLIDAY = "HOLIDAY", "Holiday"
    SPECIAL_WORKING_DAY = "SPECIAL_WORKING_DAY", "Special working day"


class CalendarDay(models.Model):
    """One configured exception to the weekly pattern: a holiday or a special working day."""

    calendar = models.ForeignKey(BusinessCalendar, on_delete=models.PROTECT, related_name="days")
    date = models.DateField()
    kind = models.CharField(max_length=20, choices=DayKind.choices)
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "calendar_day"
        ordering = ["date"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["calendar", "date"], name="calendar_day_uniq")
        ]

    def __str__(self):
        return f"{self.date} {self.kind}"
