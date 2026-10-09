"""Phase 7.2 KRA engine: the pure functions (no database). One block per locked decision."""

from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace

import pytest

from apps.core.timeutils import IST
from apps.performance import kra_engine as engine
from apps.performance.kra_engine import NA, Applicability, ComponentSpec, Credits, TaskFacts

# The shared autouse fixture known_schedule_start (recurring conftest) touches the database.
pytestmark = pytest.mark.django_db
CREDITS = Credits(Decimal("1.00"), Decimal("0.25"), Decimal("0.00"))
CUTOFF = datetime(2026, 12, 1, 0, 0, tzinfo=IST)  # November closed
DUE = datetime(2026, 11, 10, 10, 0, tzinfo=IST)
REQUIRED, NOT_REQUIRED = "REQUIRED", "NOT_REQUIRED"


def at(day, hour=10, month=11):
    return datetime(2026, month, day, hour, 0, tzinfo=IST)


def facts(**overrides) -> TaskFacts:
    values = {
        "task_id": 1, "reference": "Work", "source": "MANUAL", "responsibility_id": None,
        "template_id": None, "category_id": 7, "department_id": 3, "occurrence_date": None,
        "has_clock": True, "deadline": DUE, "accepted_completion": None,
        "verified_completion": None, "pending_verification_at_cutoff": False,
        "cancelled_before_cutoff": False, "verification_required": False,
    }
    values.update(overrides)
    return TaskFacts(**values)


def classify(f, verification=NOT_REQUIRED, cutoff=CUTOFF, applicability=None):
    return engine.classify_task(f, verification=verification, credits=CREDITS, cutoff=cutoff,
                                applicability=applicability)


# --- D1 / D2 / D4: completion history at the cutoff ------------------------------------------


def test_completion_counts_only_if_made_by_the_cutoff():
    assert engine.completion_history({at(9)}, [], CUTOFF) == (at(9), None, True)
    late = at(1, month=12) + timedelta(hours=1)
    assert engine.completion_history({late}, [], CUTOFF) == (None, None, False)  # D4


def test_a_rejection_reopens_the_task_and_rework_is_the_accepted_completion():  # D1
    first, rework = at(9, 12), at(9, 16)
    decisions = [(first, "REJECTED", at(9, 13))]
    assert engine.completion_history({first}, decisions, CUTOFF)[0] is None  # open again
    assert engine.completion_history({first, rework}, decisions, CUTOFF)[0] == rework


def test_a_rejection_after_the_cutoff_does_not_undo_the_month():  # D1 + D4
    first = at(9, 12)
    decisions = [(first, "REJECTED", at(2, 9, month=12))]
    assert engine.completion_history({first}, decisions, CUTOFF)[0] == first


def test_the_verified_submission_is_the_accepted_one_for_verification():  # D2
    first, rework = at(4, 12), at(6, 10)
    decisions = [(first, "REJECTED", at(4, 13)), (rework, "VERIFIED", at(6, 11))]
    accepted, verified, pending = engine.completion_history({first, rework}, decisions, CUTOFF)
    assert (accepted, verified, pending) == (rework, rework, False)
    not_yet = [(first, "VERIFIED", at(2, 9, month=12))]  # verified only after the cutoff
    assert engine.completion_history({first}, not_yet, CUTOFF) == (first, None, True)


# --- D3 / D4 / P6 / P7: classification ---------------------------------------------------------


@pytest.mark.parametrize(("f", "verification", "expected"), [
    (facts(cancelled_before_cutoff=True), NOT_REQUIRED, ("NA", None, NA.CANCELLED)),
    (facts(has_clock=False, deadline=None), NOT_REQUIRED, ("NA", None, NA.NO_DEADLINE)),
    (facts(deadline=None), NOT_REQUIRED, ("NA", None, NA.NOT_STARTED)),
    (facts(accepted_completion=DUE), NOT_REQUIRED, ("ON_TIME", Decimal("1.00"), "")),
    (facts(accepted_completion=DUE + timedelta(seconds=1)), NOT_REQUIRED,
     ("LATE", Decimal("0.25"), "")),
    (facts(), NOT_REQUIRED, ("OVERDUE", Decimal("0.00"), "")),  # not completed by cutoff
    # verification required: only a verified submission counts, timed on that submission (D2)
    (facts(verification_required=True, accepted_completion=at(9),
           verified_completion=at(9)), REQUIRED, ("ON_TIME", Decimal("1.00"), "")),
    (facts(verification_required=True, accepted_completion=at(11),
           verified_completion=at(11)), REQUIRED, ("LATE", Decimal("0.25"), "")),
    (facts(verification_required=True, accepted_completion=at(9),
           pending_verification_at_cutoff=True), REQUIRED, ("OVERDUE", Decimal("0.00"), "")),
    # a NOT_REQUIRED component counts the completion even while verification is pending
    (facts(verification_required=True, accepted_completion=at(9),
           pending_verification_at_cutoff=True), NOT_REQUIRED, ("ON_TIME", Decimal("1.00"), "")),
])
def test_classification_at_month_close(f, verification, expected):
    assert classify(f, verification) == expected


def test_before_the_deadline_a_provisional_month_is_not_applicable():  # P6 / P7
    early = at(9, 12)
    assert classify(facts(), cutoff=early) == ("NA", None, NA.NOT_DUE)
    pending = facts(verification_required=True, accepted_completion=at(9),
                    pending_verification_at_cutoff=True)
    assert classify(pending, REQUIRED, cutoff=early) == ("NA", None, NA.PENDING_VERIFICATION)


def test_an_on_hold_task_is_judged_on_the_sla_engines_deadline_not_on_its_hold():
    """The engine never marks a task N/A for being on hold: the deadline it receives is the
    SLA engine's (hold-adjusted) one. Passed and not completed = OVERDUE; later = NOT_DUE."""
    passed = facts(deadline=CUTOFF - timedelta(minutes=1))
    assert classify(passed) == ("OVERDUE", Decimal("0.00"), "")
    assert classify(facts(deadline=CUTOFF + timedelta(days=3)), cutoff=at(20)) == (
        "NA", None, NA.NOT_DUE,
    )


@pytest.mark.parametrize(("applicability", "reason"), [
    (Applicability(before_joining=True), NA.BEFORE_JOINING),
    (Applicability(on_leave=True), NA.APPROVED_LEAVE),
    (Applicability(responsibility_inactive=True), NA.RESPONSIBILITY_DEACTIVATED),
])
def test_applicability_makes_work_not_applicable(applicability, reason):  # D7 / D8
    assert classify(facts(), applicability=applicability) == ("NA", None, reason)


# --- D6: generation gaps -----------------------------------------------------------------------


def test_generation_gaps():
    assert engine.classify_gap("SYSTEM_ISSUE_EXCLUDE") == ("NA", None, NA.GAP_SYSTEM_ISSUE)
    assert engine.classify_gap("EMPLOYEE_RESPONSIBLE") == ("OVERDUE", Decimal("0"), "")
    assert engine.classify_gap(None) == ("NA", None, NA.GAP_UNDECIDED)  # never 0
    assert engine.classify_gap(None, on_leave=True) == ("NA", None, NA.APPROVED_LEAVE)
    assert engine.classify_gap("EMPLOYEE_RESPONSIBLE", before_joining=True) == (
        "NA", None, NA.BEFORE_JOINING,
    )


# --- matching (approved 7.1 mapping) ----------------------------------------------------------


def spec(cid, resp, scope="BOTH", match="", template=None, category=None, department=3,
         position=0, source="RESPONSIBILITY_TASKS"):
    return ComponentSpec(cid, position, source, resp, template, category, department, scope,
                         match)


def test_scheduled_tasks_match_their_responsibility_only():
    comps = [spec(1, 10, scope="SCHEDULED"), spec(2, 11, scope="MANUAL", match="CATEGORY",
                                                   category=7)]
    task = facts(source="SCHEDULED", responsibility_id=10)
    assert engine.match_line(comps, task) == [(1, "SCHEDULED")]


def test_manual_matching_most_specific_level_wins():
    comps = [spec(1, 10, match="CATEGORY", category=7), spec(2, 11, match="TASK_TYPE", template=5)]
    assert engine.match_line(comps, facts(template_id=5)) == [(2, "TASK_TYPE")]
    assert engine.match_line(comps, facts()) == [(1, "CATEGORY")]
    assert engine.match_line(comps, facts(responsibility_id=10, template_id=5)) == [
        (1, "TASK_RESPONSIBILITY"),
    ]
    assert engine.match_line(comps, facts(department_id=4)) == []  # other department


def test_overrides_are_exceptions():
    comps = [spec(1, 10, match="CATEGORY", category=7), spec(2, 11, match="NONE")]
    assert engine.match_line(comps, facts(), includes={11}) == [(2, "OVERRIDE")]
    assert engine.match_line(comps, facts(), includes={99}) == []  # belongs elsewhere
    assert engine.match_line(comps, facts(), excludes={10}) == []


# --- normalisation, benchmark, points, band ---------------------------------------------------


def test_component_and_kpi_achievement_normalise_over_applicable_parts():
    rows = [engine.CreditRow("ON_TIME", Decimal("1.00")), engine.CreditRow("LATE", Decimal("0.25")),
            engine.CreditRow("NA", None, "NO_DEADLINE")]
    assert engine.component_achievement(rows) == Fraction(125, 2)  # 1.25 / 2 x 100
    assert engine.component_achievement([engine.CreditRow("NA", None, "X")]) is None
    achievement, shares = engine.kpi_achievement(
        [(1, Decimal("1"), Fraction(100)), (2, Decimal("3"), Fraction(50)), (3, Decimal("2"), None)]
    )
    assert shares == {1: Fraction(1, 4), 2: Fraction(3, 4)}  # the N/A component is left out
    assert achievement == Fraction(125, 2)
    assert engine.kpi_achievement([(1, Decimal("1"), None)]) == (None, {})  # whole KPI N/A


STEPS = [(Decimal("100"), Decimal("100")), (Decimal("85"), Decimal("80")),
         (Decimal("70"), Decimal("60")), (Decimal("50"), Decimal("40"))]


@pytest.mark.parametrize(("achievement", "score"), [
    (Fraction(100), "100"), (Fraction(9999, 100), "80"), (Fraction(85), "80"),
    (Fraction(849999, 10000), "60"), (Fraction(70), "60"), (Fraction(50), "40"),
    (Fraction(4999, 100), "0"), (Fraction(0), "0"),
])
def test_benchmark_boundaries(achievement, score):
    assert engine.benchmark(achievement, STEPS, Decimal("0")) == Decimal(score)


def test_below_fifty_is_configurable_and_points_are_weight_times_score():
    assert engine.benchmark(Fraction(10), STEPS, Decimal("15")) == Decimal("15")
    assert engine.kpi_points(Decimal("3.00"), Decimal("80")) == Decimal("2.400000")
    assert engine.kpi_points(Decimal("1.50"), Decimal("40")) == Decimal("0.600000")


def test_bands_use_lower_bounds_and_a_ceiling_caps_them():
    bands = [SimpleNamespace(name=n, min_points=Decimal(m)) for n, m in (
        ("High Performer", "9.0"), ("Consistent Performer", "7.5"),
        ("Needs Improvement", "6.0"), ("Performance Concern", "0"))]
    names = {t: engine.band_for(Decimal(t), bands).name
             for t in ("10", "9.0", "8.99", "7.5", "6.0", "5.99", "0")}
    assert names == {"10": "High Performer", "9.0": "High Performer",
                     "8.99": "Consistent Performer", "7.5": "Consistent Performer",
                     "6.0": "Needs Improvement", "5.99": "Performance Concern",
                     "0": "Performance Concern"}
    ceiling = bands[2]
    assert engine.band_for(Decimal("9.5"), bands, ceiling).name == "Needs Improvement"
    assert engine.band_for(Decimal("5"), bands, ceiling).name == "Performance Concern"


def test_responsibility_activity_from_audit_events():  # D8 (logic only)
    activity = engine.ResponsibilityActivity.__new__(engine.ResponsibilityActivity)
    activity.currently_active = True
    activity.events = [(at(15, 12), "responsibility.deactivated"),
                       (at(20, 12), "responsibility.activated")]
    assert activity.active_at(at(10))
    assert not activity.active_at(at(16))
    assert activity.active_at(at(23))
    activity.events = []
    assert activity.active_at(at(16))  # no events: its current status
