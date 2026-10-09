"""Pure SLA arithmetic (no database)."""

from datetime import datetime, timedelta

import pytest

from apps.core.timeutils import IST
from apps.sla import engine

START = datetime(2026, 10, 5, 10, 0, tzinfo=IST)


def snap(**overrides):
    base = {
        "code": "X",
        "version": 1,
        "name": "X",
        "rule_type": "DURATION",
        "clock": "CALENDAR",
        "duration_minutes": 120,
        "warning_pct": 50,
        "critical_pct": 75,
        "overdue_pct": 100,
        "work_end": None,
    }
    return {**base, **overrides}


def test_duration_due():
    assert engine.compute_due(snap(), START) == START + timedelta(hours=2)


def test_end_of_day_due_is_that_ist_days_work_end():
    due = engine.compute_due(snap(rule_type="END_OF_DAY", work_end="18:30"), START)
    assert due == datetime(2026, 10, 5, 18, 30, tzinfo=IST)


@pytest.mark.parametrize(
    "overrides", [{"clock": "BUSINESS"}, {"rule_type": "TRANSACTION_CUTOFF"}]
)
def test_later_phase_rules_are_refused(overrides):
    with pytest.raises(engine.SlaNotSupported):
        engine.compute_due(snap(**overrides), START)


@pytest.mark.parametrize(
    ("minutes", "state"),
    [(59, "ON_TRACK"), (60, "WARNING"), (89, "WARNING"), (90, "CRITICAL"), (119, "CRITICAL"),
     (120, "OVERDUE"), (500, "OVERDUE")],
)
def test_threshold_boundaries(minutes, state):
    due = START + timedelta(hours=2)
    pct = engine.elapsed_pct(START, due, START + timedelta(minutes=minutes))
    assert engine.state_for(pct, snap()) == state


def test_elapsed_pct_edges():
    due = START + timedelta(hours=2)
    assert engine.elapsed_pct(START, due, START - timedelta(minutes=5)) == 0.0
    assert engine.elapsed_pct(START, START, START) == 100.0
    assert engine.elapsed_pct(START, START - timedelta(hours=1), START - timedelta(hours=2)) == 0.0


def test_threshold_instant_and_outcome():
    due = START + timedelta(hours=2)
    assert engine.threshold_instant(START, due, 75) == START + timedelta(minutes=90)
    assert engine.outcome_for(due, due) == "MET"
    assert engine.outcome_for(due + timedelta(seconds=1), due) == "MISSED"
