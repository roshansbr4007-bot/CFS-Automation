"""The single calendar service. Every working-day question in the system comes here.

Approved Company Calendar rules: Monday-Friday working; Sunday off; Saturdays working only
on the 1st and 3rd occurrence of the month (days 1-7 and 15-21); configured holidays off;
configured special working days on. A special working day wins over everything else.
"""

from datetime import date, timedelta

from django.db import transaction

from apps.audit.services import record
from apps.core.errors import ConflictError, FieldValidationError

from .models import BusinessCalendar, CalendarDay, DayKind

COMPANY = "COMPANY"
SATURDAY = 5
SEARCH_LIMIT_DAYS = 60  # misconfiguration guard (v4): never search forever


class CalendarError(Exception):
    """The calendar is misconfigured (e.g. no working day within the search limit)."""


class ResolvePolicy:
    SKIP = "SKIP"
    NEXT_WORKING_DAY = "NEXT_WORKING_DAY"
    PREVIOUS_WORKING_DAY = "PREVIOUS_WORKING_DAY"
    CHOICES = [
        (SKIP, "Skip the occurrence"),
        (NEXT_WORKING_DAY, "Move to the next working day"),
        (PREVIOUS_WORKING_DAY, "Move to the previous working day"),
    ]


def company_calendar() -> BusinessCalendar:
    return BusinessCalendar.objects.get(code=COMPANY)


def occurrence_in_month(day: date) -> int:
    """1st..5th occurrence of that weekday in its month (days 1-7 = 1st, 8-14 = 2nd, ...)."""
    return (day.day - 1) // 7 + 1


def is_working_day(day: date, calendar: BusinessCalendar | None = None) -> bool:
    calendar = calendar or company_calendar()
    entry = CalendarDay.objects.filter(calendar=calendar, date=day).first()
    if entry is not None:
        return entry.kind == DayKind.SPECIAL_WORKING_DAY
    if day.weekday() in calendar.weekly_off_weekdays:
        return False
    if day.weekday() == SATURDAY:
        return occurrence_in_month(day) in calendar.saturday_working_occurrences
    return True


def _step(day: date, direction: int, calendar) -> date:
    for offset in range(1, SEARCH_LIMIT_DAYS + 1):
        candidate = day + timedelta(days=direction * offset)
        if is_working_day(candidate, calendar):
            return candidate
    raise CalendarError(f"No working day within {SEARCH_LIMIT_DAYS} days of {day}.")


def next_working_day(day: date, calendar: BusinessCalendar | None = None) -> date:
    return _step(day, 1, calendar or company_calendar())


def previous_working_day(day: date, calendar: BusinessCalendar | None = None) -> date:
    return _step(day, -1, calendar or company_calendar())


def resolve_scheduled_date(target: date, policy: str, calendar=None) -> date | None:
    """The business date an occurrence planned for `target` really falls on (None = skipped)."""
    calendar = calendar or company_calendar()
    if is_working_day(target, calendar):
        return target
    if policy == ResolvePolicy.NEXT_WORKING_DAY:
        return next_working_day(target, calendar)
    if policy == ResolvePolicy.PREVIOUS_WORKING_DAY:
        return previous_working_day(target, calendar)
    return None


def working_days(start: date, end: date, calendar=None) -> list[date]:
    calendar = calendar or company_calendar()
    if end < start or (end - start).days > 366:
        raise FieldValidationError(fields={"to": ["Use a range of at most one year."]})
    return [
        start + timedelta(days=i)
        for i in range((end - start).days + 1)
        if is_working_day(start + timedelta(days=i), calendar)
    ]


# --- Admin configuration (audited) ------------------------------------------------------------


def _day_snapshot(entry: CalendarDay) -> dict:
    return {"date": entry.date.isoformat(), "kind": entry.kind, "name": entry.name}


def add_day(*, actor, day: date, kind: str, name: str) -> CalendarDay:
    calendar = company_calendar()
    name = (name or "").strip()
    if not name:
        raise FieldValidationError(fields={"name": ["This field is required."]})
    if CalendarDay.objects.filter(calendar=calendar, date=day).exists():
        raise ConflictError("This date is already configured.", code="calendar_day_exists")
    with transaction.atomic():
        entry = CalendarDay.objects.create(calendar=calendar, date=day, kind=kind, name=name)
        record(
            action="calendar.day_added",
            entity_type="calendar_day",
            entity_id=entry.pk,
            actor=actor,
            new=_day_snapshot(entry),
            extra={"calendar": calendar.code},
        )
    return entry


def remove_day(*, actor, entry: CalendarDay) -> None:
    """Configuration row, not business history: removed physically, the audit keeps it."""
    with transaction.atomic():
        record(
            action="calendar.day_removed",
            entity_type="calendar_day",
            entity_id=entry.pk,
            actor=actor,
            old=_day_snapshot(entry),
            extra={"calendar": entry.calendar.code},
        )
        entry.delete()
