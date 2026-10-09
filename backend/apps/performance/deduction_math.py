"""Phase 7.3 settlement maths: HR adjustments and deductions on top of the automatic KRA points.

PURE: no database and no Django import, so every formula is testable on its own. The database
side (which rows count, writing the results) is apps.performance.settlement.

Approved decisions (Phase 7.3, E1-E11):
- E1/E2  HR adjustment first. HR enters the KPI's new points T within [0, KPI maximum]; the
         latest adjustment wins. Points before deductions: P = T, or the automatic points A.
- E3     Component deduction base = the component's share of P (C1):
         CP_c = P x s_c, where s_c = share / sum of the shares of the applicable components.
- E4     Overall deduction is spread over the KPIs in proportion to their points, so the final
         monthly score is still exactly the sum of the final KPI points.
- E5     Several STACKING rules on one target: ADDITIVE  rate = min(1, sum of rule rates);
                                               SEQUENTIAL rate = 1 - product(1 - rule rate),
         in priority order.
- E6     NON_STACKING: if a target has any non-stacking rule, only the best-priority
         non-stacking rule applies there; every other rule on that target is ignored.
- E7     Priority 1 = highest.
- E8     cap_pct = the most ONE rule can take from ONE target in the month, however often it is
         applied: rule rate = min(sum of its percentages, cap); uncapped = no rule cap.
- E9/E10 Band ceilings never change points (handled in settlement.py: the lowest ceiling caps the
         band; revenue leakage is a band ceiling only).
- E11    Every reduction is limited to 100% of its base, and final points never go below 0.

Order on one KPI:  P  ->  component deductions  ->  KPI deduction  ->  overall deduction.
    RC_c = CP_c x rate_c
    RK   = (P - sum RC_c) x rate_k
    F    = max(0, P - sum RC_c - RK)
    RO_k = F x rate_overall       (S = sum F; RO = S x rate_overall, spread pro rata to F)
    final = F - RO_k

Attribution (which rule took how many points; shown per rule and target): SEQUENTIAL - each rule
takes its rate of what is left, in priority order. ADDITIVE - each rule takes its own rate; if
the rates add up to more than 100% the rules are taken in priority order until 100% is reached.
Attribution never changes a total.
"""

from dataclasses import dataclass, field
from fractions import Fraction

COMPONENT, KPI, OVERALL = "COMPONENT", "KPI", "OVERALL"
STACK, NON_STACKING = "STACK", "NON_STACKING"
ADDITIVE, SEQUENTIAL = "ADDITIVE", "SEQUENTIAL"
ONE = Fraction(1)
ZERO = Fraction(0)
HUNDRED = Fraction(100)


@dataclass(frozen=True)
class Rule:
    id: int
    code: str
    name: str
    priority: int
    stacking: str  # STACK | NON_STACKING
    cap_pct: Fraction | None  # None = uncapped


@dataclass(frozen=True)
class Deduction:
    """One active (not reversed) percentage deduction applied by HR."""

    id: int
    rule: Rule
    percent: Fraction  # 0-100, within the rule's range (checked when applied)
    scope: str  # COMPONENT | KPI | OVERALL (the rule's scope)
    target_id: int | None  # component result id, KPI score id, or None (overall)


@dataclass(frozen=True)
class ComponentInput:
    id: int
    share: Fraction  # the configured (relative) contribution share
    applicable: bool


@dataclass(frozen=True)
class KpiInput:
    id: int
    weight: Fraction  # the KPI's maximum points
    auto: Fraction  # automatic points (0 when the KPI is N/A)
    applicable: bool
    adjusted: Fraction | None = None  # T of the latest valid HR adjustment, if any
    components: tuple = ()


@dataclass(frozen=True)
class RuleShare:
    """What one rule did on one target."""

    rule: Rule
    rate: Fraction  # the rule's own rate after its cap (0-1)
    effective: bool  # False: set aside by a non-stacking rule (E6)
    taken: Fraction  # fraction of the base this rule took (attribution)
    deduction_ids: tuple


@dataclass(frozen=True)
class Combined:
    rate: Fraction  # combined fraction of the base taken (0-1)
    shares: tuple  # RuleShare, in priority order


@dataclass(frozen=True)
class Line:
    """One rule on one target, with the points it took (summed over KPIs for OVERALL)."""

    scope: str
    target_id: int | None
    kpi_id: int | None
    rule: Rule
    rate: Fraction
    effective: bool
    points: Fraction
    deduction_ids: tuple


@dataclass
class KpiResult:
    id: int
    applicable: bool
    auto: Fraction
    adjusted: bool
    points: Fraction  # P: before deductions
    component_deductions: dict = field(default_factory=dict)  # component id -> points
    kpi_deduction: Fraction = ZERO
    overall_deduction: Fraction = ZERO
    final: Fraction = ZERO

    @property
    def adjustment(self) -> Fraction:
        return self.points - self.auto

    @property
    def deduction(self) -> Fraction:
        return self.points - self.final


@dataclass
class Result:
    kpis: dict  # kpi id -> KpiResult
    lines: list
    overall_rate: Fraction

    @property
    def final_total(self) -> Fraction:
        return sum((k.final for k in self.kpis.values()), ZERO)


def rule_rate(percents, cap_pct: Fraction | None) -> Fraction:
    """E8 + E11: one rule on one target - the sum of its percentages, at most its cap, at most
    100% - as a fraction (0-1)."""
    total = sum((Fraction(p) for p in percents), ZERO)
    if cap_pct is not None:
        total = min(total, Fraction(cap_pct))
    return min(total, HUNDRED) / HUNDRED


def combine(deductions, method: str) -> Combined:
    """E5-E8, E11: the combined rate of the deductions on ONE target and each rule's part."""
    by_rule = {}
    for item in deductions:
        by_rule.setdefault(item.rule.id, []).append(item)
    rules = sorted((items[0].rule for items in by_rule.values()), key=lambda r: (r.priority, r.id))
    rates = {r.id: rule_rate([d.percent for d in by_rule[r.id]], r.cap_pct) for r in rules}
    exclusive = [r for r in rules if r.stacking == NON_STACKING]
    effective = [exclusive[0]] if exclusive else rules  # E6
    taken, used = {}, ZERO
    if len(effective) > 1 and method not in (ADDITIVE, SEQUENTIAL):
        raise ValueError(f"Unknown stacking method: {method!r}")
    for r in effective:  # priority order (E7)
        if method == SEQUENTIAL:
            part = (ONE - used) * rates[r.id]
        else:  # ADDITIVE, or a single rule (both methods agree)
            part = min(rates[r.id], ONE - used)
        taken[r.id] = part
        used += part
    shares = tuple(
        RuleShare(rule=r, rate=rates[r.id], effective=r.id in taken, taken=taken.get(r.id, ZERO),
                  deduction_ids=tuple(d.id for d in by_rule[r.id]))
        for r in rules
    )
    return Combined(rate=used, shares=shares)


def _lines(scope, target_id, kpi_id, combined: Combined, base: Fraction):
    return [
        Line(scope=scope, target_id=target_id, kpi_id=kpi_id, rule=s.rule, rate=s.rate,
             effective=s.effective, points=s.taken * base, deduction_ids=s.deduction_ids)
        for s in combined.shares
    ]


def settle(kpis, deductions, method: str) -> Result:
    """The settled month: every KPI's points before deductions, its deductions by level and its
    final points, plus one line per rule and target."""
    by_target = {COMPONENT: {}, KPI: {}, OVERALL: []}
    for item in deductions:
        if item.scope == OVERALL:
            by_target[OVERALL].append(item)
        else:
            by_target[item.scope].setdefault(item.target_id, []).append(item)
    results, lines = {}, []
    for kpi in kpis:
        if not kpi.applicable:  # N/A earns nothing; nothing to adjust or deduct (P8)
            results[kpi.id] = KpiResult(id=kpi.id, applicable=False, auto=ZERO, adjusted=False,
                                        points=ZERO)
            for component in kpi.components:
                found = by_target[COMPONENT].get(component.id)
                if found:
                    lines += _lines(COMPONENT, component.id, kpi.id, combine(found, method), ZERO)
            if by_target[KPI].get(kpi.id):
                lines += _lines(KPI, kpi.id, kpi.id, combine(by_target[KPI][kpi.id], method),
                                ZERO)
            continue
        points = Fraction(kpi.adjusted) if kpi.adjusted is not None else Fraction(kpi.auto)
        result = KpiResult(id=kpi.id, applicable=True, auto=Fraction(kpi.auto),
                           adjusted=kpi.adjusted is not None, points=points)
        applicable = [c for c in kpi.components if c.applicable]
        total_share = sum((Fraction(c.share) for c in applicable), ZERO)
        for component in kpi.components:
            found = by_target[COMPONENT].get(component.id)
            if not found:
                continue
            base = ZERO
            if component.applicable and total_share > 0:
                base = points * Fraction(component.share) / total_share  # CP_c (C1)
            combined = combine(found, method)
            result.component_deductions[component.id] = base * combined.rate
            lines += _lines(COMPONENT, component.id, kpi.id, combined, base)
        remaining = points - sum(result.component_deductions.values(), ZERO)
        found = by_target[KPI].get(kpi.id)
        if found:
            combined = combine(found, method)
            result.kpi_deduction = remaining * combined.rate
            lines += _lines(KPI, kpi.id, kpi.id, combined, remaining)
        result.final = max(ZERO, remaining - result.kpi_deduction)  # E11
        results[kpi.id] = result
    overall_rate = ZERO
    if by_target[OVERALL]:
        combined = combine(by_target[OVERALL], method)
        overall_rate = combined.rate
        subtotal = sum((r.final for r in results.values()), ZERO)  # S
        lines += _lines(OVERALL, None, None, combined, subtotal)
        for result in results.values():
            result.overall_deduction = result.final * overall_rate  # RO_k = F_k x rate (E4)
            result.final = max(ZERO, result.final - result.overall_deduction)
    return Result(kpis=results, lines=lines, overall_rate=overall_rate)
