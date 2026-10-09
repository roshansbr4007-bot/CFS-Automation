"""IST/UTC helpers. Stored values are UTC; business time is Asia/Kolkata.

These helpers only convert. Deadline calculation belongs to the SLA engine (Phase 6).
"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def now_ist() -> datetime:
    return datetime.now(IST)


def to_ist(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("to_ist() needs a timezone-aware datetime")
    return value.astimezone(IST)


def ist_datetime(day: date, at: time) -> datetime:
    """An aware datetime for a wall-clock time in IST on a given date."""
    if at.tzinfo is not None:
        raise ValueError("ist_datetime() takes a naive time, interpreted in IST")
    return datetime.combine(day, at, tzinfo=IST)
