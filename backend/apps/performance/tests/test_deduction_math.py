"""Phase 7.3 settlement maths (apps.performance.deduction_math), exact Fractions, no database.
One block per approved decision E1-E11; every expected value is worked out by hand."""

from fractions import Fraction as F

import pytest

from apps.performance import deduction_math as dm

# No query is made here; the shared autouse fixture known_schedule_start (recurring
# conftest) touches the database, as in test_kra_engine_units.py.
pytestmark = pytest.mark.django_db


def rule(rid, priority, stacking="STACK", cap=None, code=None):
    return dm.Rule(id=rid, code=code or f"R{rid}", name=f"Rule {rid}", priority=priority,
                   stacking=stacking, cap_pct=None if cap is None else F(cap))


def ded(did, r, percent, scope="KPI", target=1):
    return dm.Deduction(id=did, rule=r, percent=F(percent), scope=scope,
                        target_id=None if scope == "OVERALL" else target)


def kpi(kid, weight, auto, components=((1, 1, True),), adjusted=None, applicable=True):
    return dm.KpiInput(
        id=kid, weight=F(weight), auto=F(auto), applicable=applicable,
        adjusted=None if adjusted is None else F(adjusted),
        components=tuple(dm.ComponentInput(id=c, share=F(s), applicable=a)
                         for c, s, a in components),
    )


# --- E8 / E11: one rule on one target -------------------------------------------------------


def test_rule_rate_sums_its_applications_up_to_its_cap_and_100_percent():
    assert dm.rule_rate([F(20), F(15)], F(30)) == F(3, 10)  # capped at 30%
    assert dm.rule_rate([F(20), F(5)], F(30)) == F(1, 4)  # below the cap
    assert dm.rule_rate([F(60), F(60)], None) == F(1)  # uncapped, but never above 100% (E11)
    assert dm.rule_rate([], None) == 0


def test_the_cap_is_per_rule_and_per_target():
    capped = rule(1, 1, cap=30)
    result = dm.settle(
        [kpi(1, 3, 3), kpi(2, 2, 2, components=((2, 1, True),))],
        [ded(1, capped, 20, target=1), ded(2, capped, 20, target=1), ded(3, capped, 20, target=2)],
        "ADDITIVE",
    )
    assert result.kpis[1].kpi_deduction == F(9, 10)  # 3 x 30% (40% capped)
    assert result.kpis[2].kpi_deduction == F(2, 5)  # 2 x 20% (its own target)


# --- E5 / E7: stacking rules ------------------------------------------------------------------


def test_additive_adds_the_rule_rates():
    combined = dm.combine([ded(1, rule(1, 1), 20), ded(2, rule(2, 2), 10)], "ADDITIVE")
    assert combined.rate == F(3, 10)
    assert [s.taken for s in combined.shares] == [F(1, 5), F(1, 10)]


def test_additive_above_100_percent_fills_in_priority_order():
    combined = dm.combine(
        [ded(1, rule(3, 3), 30), ded(2, rule(1, 1), 50), ded(3, rule(2, 2), 40)], "ADDITIVE"
    )
    assert combined.rate == 1
    assert [(s.rule.id, s.taken) for s in combined.shares] == [
        (1, F(1, 2)), (2, F(2, 5)), (3, F(1, 10)),
    ]


def test_sequential_applies_each_rule_to_the_remainder_in_priority_order():
    combined = dm.combine([ded(1, rule(2, 2), 10), ded(2, rule(1, 1), 20)], "SEQUENTIAL")
    assert combined.rate == 1 - F(8, 10) * F(9, 10)  # 28%
    assert [(s.rule.id, s.taken) for s in combined.shares] == [(1, F(1, 5)), (2, F(2, 25))]


def test_a_single_rule_is_the_same_under_both_methods():
    for method in ("ADDITIVE", "SEQUENTIAL", ""):
        assert dm.combine([ded(1, rule(1, 1), 25)], method).rate == F(1, 4)


def test_an_unknown_method_with_several_rules_is_refused():
    with pytest.raises(ValueError):
        dm.combine([ded(1, rule(1, 1), 20), ded(2, rule(2, 2), 10)], "")


# --- E6: non-stacking -------------------------------------------------------------------------


def test_only_the_best_priority_non_stacking_rule_applies_on_its_target():
    combined = dm.combine(
        [ded(1, rule(1, 1), 20), ded(2, rule(2, 3, "NON_STACKING"), 25),
         ded(3, rule(3, 2, "NON_STACKING"), 10)],
        "ADDITIVE",
    )
    assert combined.rate == F(1, 10)
    assert [(s.rule.id, s.effective, s.taken) for s in combined.shares] == [
        (1, False, 0), (3, True, F(1, 10)), (2, False, 0),
    ]


def test_non_stacking_on_one_target_does_not_affect_another_target():
    result = dm.settle(
        [kpi(1, 2, 2), kpi(2, 2, 2, components=((2, 1, True),))],
        [ded(1, rule(1, 1, "NON_STACKING"), 50, target=1), ded(2, rule(2, 2), 25, target=2)],
        "ADDITIVE",
    )
    assert (result.kpis[1].final, result.kpis[2].final) == (F(1), F(3, 2))


# --- E1 / E2 / E3 / E4: the full order on a month -------------------------------------------


def test_adjustment_then_component_then_kpi_then_overall():
    kpis = [
        kpi(1, 3, F(18, 10), components=((11, 1, True), (12, 1, True)), adjusted=F(24, 10)),
        kpi(2, 2, F(16, 10), components=((21, 1, True),)),
        kpi(3, 1, 0, components=((31, 1, False),), applicable=False),
    ]
    deductions = [
        ded(1, rule(1, 1), 25, scope="COMPONENT", target=11),
        ded(2, rule(2, 2), 10, scope="KPI", target=1),
        ded(3, rule(3, 3), 20, scope="OVERALL"),
    ]
    result = dm.settle(kpis, deductions, "ADDITIVE")
    first, second, third = (result.kpis[k] for k in (1, 2, 3))
    # KPI 1: P = 2.4 (HR); component 11 base = 2.4 x 1/2 = 1.2 -> 25% = 0.3;
    # KPI base 2.1 -> 10% = 0.21; F = 1.89; overall 20% of 1.89 = 0.378; final 1.512
    assert (first.points, first.adjustment) == (F(12, 5), F(3, 5))
    assert first.component_deductions == {11: F(3, 10)}
    assert (first.kpi_deduction, first.overall_deduction) == (F(21, 100), F(378, 1000))
    assert first.final == F(1512, 1000) and first.deduction == F(888, 1000)
    # KPI 2: only the overall deduction: 1.6 x 80% = 1.28
    assert (second.overall_deduction, second.final) == (F(32, 100), F(128, 100))
    # KPI 3 is N/A: earns nothing, takes nothing
    assert (third.applicable, third.points, third.final) == (False, 0, 0)
    # E4: the overall 20% of S = 3.49 is spread pro rata; the month is the sum of the KPIs
    assert result.overall_rate == F(1, 5)
    assert result.final_total == F(3490, 1000) * F(4, 5) == F(2792, 1000)
    by_scope = {line.scope: line.points for line in result.lines}
    assert by_scope == {"COMPONENT": F(3, 10), "KPI": F(21, 100), "OVERALL": F(698, 1000)}


def test_component_base_is_its_share_of_the_kpi_points_over_applicable_components():
    result = dm.settle(
        [kpi(1, 3, 3, components=((11, 2, True), (12, 1, False), (13, 1, True)))],
        [ded(1, rule(1, 1), 50, scope="COMPONENT", target=11),
         ded(2, rule(2, 2), 50, scope="COMPONENT", target=12)],
        "ADDITIVE",
    )
    # applicable shares 2 + 1: component 11 = 3 x 2/3 = 2 -> 50% = 1; component 12 is N/A -> 0
    assert result.kpis[1].component_deductions == {11: F(1), 12: F(0)}
    assert result.kpis[1].final == F(2)


def test_sequential_kpi_rules_attribute_points_in_priority_order():
    result = dm.settle([kpi(1, 2, 2)], [ded(1, rule(1, 2), 50), ded(2, rule(2, 1), 25)],
                       "SEQUENTIAL")
    # priority 1 (25%) first: 0.5; then 50% of the remaining 1.5: 0.75
    assert [(line.rule.id, line.points) for line in result.lines] == [(2, F(1, 2)), (1, F(3, 4))]
    assert result.kpis[1].final == F(3, 4)


# --- E11: limits ------------------------------------------------------------------------------


def test_reductions_never_exceed_their_base_and_final_points_never_go_below_zero():
    result = dm.settle(
        [kpi(1, 1, 1)],
        [ded(1, rule(1, 1), 100, scope="COMPONENT", target=1), ded(2, rule(2, 2), 50)],
        "ADDITIVE",
    )
    assert result.kpis[1].component_deductions == {1: F(1)}
    assert (result.kpis[1].kpi_deduction, result.kpis[1].final) == (0, 0)


def test_an_adjustment_on_a_not_applicable_kpi_is_ignored():
    result = dm.settle([kpi(1, 2, 0, adjusted=F(2), applicable=False)], [], "ADDITIVE")
    assert (result.kpis[1].points, result.kpis[1].final, result.final_total) == (0, 0, 0)


def test_no_deductions_and_no_adjustment_leave_the_automatic_points():
    result = dm.settle([kpi(1, 3, F(18, 10)), kpi(2, 2, F(6, 5), components=((2, 1, True),))],
                       [], "")
    assert (result.kpis[1].final, result.kpis[2].final) == (F(18, 10), F(6, 5))
    assert result.lines == [] and result.overall_rate == 0


def test_deductions_on_a_target_that_became_not_applicable_take_nothing():
    result = dm.settle(
        [kpi(1, 2, 0, components=((11, 1, False),), applicable=False), kpi(2, 2, 2,
                                                                          components=((21, 1,
                                                                                       True),))],
        [ded(1, rule(1, 1), 30, scope="COMPONENT", target=11), ded(2, rule(2, 2), 20, target=1)],
        "ADDITIVE",
    )
    assert [(line.scope, line.target_id, line.points) for line in result.lines] == [
        ("COMPONENT", 11, 0), ("KPI", 1, 0),
    ]
    assert (result.kpis[1].final, result.kpis[2].final) == (0, 2)


# --- 7.3 decision 4: priority changes only the per-rule allocation -------------------------------


def _overflowing_month(p1, p2, p3):
    """KPI 1 (3 points) carries three ADDITIVE KPI rules of 50% + 30% + 40% = 120%; an
    overall 20% rule follows. p1-p3 are the priorities of the three KPI rules."""
    rules = (rule(1, p1), rule(2, p2), rule(3, p3))
    return dm.settle(
        [kpi(1, 3, 3), kpi(2, 2, 2, components=((2, 1, True),))],
        [ded(1, rules[0], 50), ded(2, rules[1], 30), ded(3, rules[2], 40),
         ded(4, rule(4, 4), 20, scope="OVERALL")],
        "ADDITIVE",
    )


def test_reversing_priority_changes_only_the_per_rule_allocation():
    forward, backward = _overflowing_month(1, 2, 3), _overflowing_month(3, 2, 1)
    for result in (forward, backward):
        assert result.kpis[1].kpi_deduction == 3  # capped at 100% of the base (E11)
        assert (result.kpis[1].final, result.kpis[2].final) == (0, F(8, 5))
        assert result.final_total == F(8, 5)  # S = 0 + 2 -> 20% overall
    assert [(k, r.points, r.deduction, r.final) for k, r in forward.kpis.items()] == [
        (k, r.points, r.deduction, r.final) for k, r in backward.kpis.items()
    ]

    def kpi_lines(result):
        return {line.rule.id: line.points for line in result.lines if line.scope == "KPI"}

    assert kpi_lines(forward) == {1: F(3, 2), 2: F(9, 10), 3: F(3, 5)}  # 50 / 30 / 20 %
    assert kpi_lines(backward) == {3: F(6, 5), 2: F(9, 10), 1: F(9, 10)}  # 40 / 30 / 30 %


def test_per_rule_points_reconcile_with_the_deductions():
    for result in (_overflowing_month(1, 2, 3), _overflowing_month(3, 2, 1)):
        total = sum((r.deduction for r in result.kpis.values()), F(0))
        assert sum((line.points for line in result.lines), F(0)) == total  # exact
        assert sum((line.points for line in result.lines if line.scope == "KPI"),
                   F(0)) == result.kpis[1].kpi_deduction


def test_stored_rounding_keeps_per_rule_points_within_half_a_unit_each():
    from decimal import ROUND_HALF_UP, Decimal

    def stored(value):
        value = F(value)
        return (Decimal(value.numerator) / Decimal(value.denominator)).quantize(
            Decimal("0.000001"), ROUND_HALF_UP)

    result = dm.settle(
        [kpi(1, 3, F("2.333333"))],
        [ded(1, rule(1, 1), F("33.33")), ded(2, rule(2, 2), F("33.33")),
         ded(3, rule(3, 3), F("33.34"))],
        "ADDITIVE",
    )
    lines = [stored(line.points) for line in result.lines]
    deducted = stored(result.kpis[1].points) - stored(result.kpis[1].final)
    assert deducted == Decimal("2.333333")
    assert abs(sum(lines) - deducted) <= Decimal("0.0000005") * len(lines)
