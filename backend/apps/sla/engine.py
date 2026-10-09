"""Pure SLA arithmetic. No database access, so every rule is easy to test exhaustively.

Only the CALENDAR clock (24/7) is implemented now. The clock type is part of every snapshot
so business-hours clocks can be added with the calendar engine without changing callers.
"""

from datetime import datetime, time, timedelta

from apps.core.timeutils import ist_datetime, to_ist

from .models import ClockType, Outcome, RuleType, SlaState


class SlaNotSupported(Exception):
    """A rule or clock type whose engine arrives in a later phase."""


def snapshot(rule, work_end: time | None = None) -> dict:
    return {
        "code": rule.code,
        "version": rule.version,
        "name": rule.name,
        "rule_type": rule.rule_type,
        "clock": rule.clock,
        "duration_minutes": rule.duration_minutes,
        "warning_pct": rule.warning_pct,
        "critical_pct": rule.critical_pct,
        "overdue_pct": rule.overdue_pct,
        "work_end": work_end.strftime("%H:%M") if work_end else None,
    }


def compute_due(snap: dict, start_at: datetime) -> datetime:
    if snap["clock"] != ClockType.CALENDAR:
        raise SlaNotSupported("Business-hours clocks need the calendar engine.")
    if snap["rule_type"] == RuleType.DURATION:
        return start_at + timedelta(minutes=snap["duration_minutes"])
    if snap["rule_type"] == RuleType.END_OF_DAY:
        hours, minutes = (int(part) for part in snap["work_end"].split(":"))
        return ist_datetime(to_ist(start_at).date(), time(hours, minutes))
    raise SlaNotSupported(f"{snap['rule_type']} rules arrive in a later phase.")


def elapsed_pct(start_at: datetime, due_at: datetime, now: datetime) -> float:
    total = (due_at - start_at).total_seconds()
    if total <= 0:
        return 100.0 if now >= start_at else 0.0
    return max(0.0, (now - start_at).total_seconds() / total * 100)


def state_for(pct: float, snap: dict) -> str:
    if pct >= snap["overdue_pct"]:
        return SlaState.OVERDUE
    if pct >= snap["critical_pct"]:
        return SlaState.CRITICAL
    if pct >= snap["warning_pct"]:
        return SlaState.WARNING
    return SlaState.ON_TRACK


def threshold_instant(start_at: datetime, due_at: datetime, pct: int) -> datetime:
    return start_at + (due_at - start_at) * (pct / 100)


def outcome_for(stopped_at: datetime, due_at: datetime) -> str:
    return Outcome.MET if stopped_at <= due_at else Outcome.MISSED
