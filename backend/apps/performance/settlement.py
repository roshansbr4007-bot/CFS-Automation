"""Phase 7.3 settlement: turns the append-only HR adjustments and deduction applications of a KRA
month into its stored points, totals and band. The maths is apps.performance.deduction_math;
this module only decides which rows count and writes the results.

Which rows count (nothing is ever deleted or edited):
- Adjustment (E1, E2): the LATEST ScoreAdjustment of a KPI, if it is still valid - the KPI is
  applicable and its "after" points lie within [0, KPI maximum]. Its "after" points are the KPI's
  points before deductions, also after a recalculation (E14). An adjustment that is no longer
  valid (the KPI became N/A, or a new plan version lowered the maximum) is kept but not applied.
- Deduction: a DeductionApplication that is not a reversal, has not been reversed, and whose rule
  belongs to the month's plan version. A target that became N/A gives it nothing to take.
- Band ceiling (E9, E10): every active BAND_CEILING application caps the band; the lowest
  ceiling wins; points and totals are never changed by a ceiling.

Stored values keep 6 decimal places (ROUND_HALF_UP), like the engine: per KPI
adjustment = P - auto, deduction = P - final, final = auto + adjustment - deduction exactly;
the monthly final total is the sum of the stored final KPI points (E4).
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from django.db.models import Prefetch

from . import config_services
from . import deduction_math as dm
from .kra_engine import band_for
from .models import (
    CalculationModel,
    DeductionApplication,
    DeductionKind,
    MonthlyComponentResult,
    MonthlyKPIScore,
    MonthlyPerformance,
    PerformanceStatus,
    ScoreAdjustment,
)
from .services import PerformanceError, month_bounds

SIX = Decimal("0.000001")
ZERO = Decimal("0")


def to_points(value) -> Decimal:
    """A Fraction / Decimal as stored points (6 decimal places, half up)."""
    value = Fraction(value)
    return (Decimal(value.numerator) / Decimal(value.denominator)).quantize(SIX, ROUND_HALF_UP)


class PlanChangedUnderDeductions(PerformanceError):
    code = "plan_changed_under_deductions"
    message = ("Deductions applied under another KPI plan version are still active for this "
               "month; reverse them (under review) before it is recalculated.")


@dataclass
class Settlement:
    performance: object
    scores: list
    components: dict  # kpi score id -> [MonthlyComponentResult]
    latest_adjustment: dict  # kpi score id -> ScoreAdjustment | None
    adjustment_applied: dict  # kpi score id -> bool
    applications: list  # every DeductionApplication of the month (history)
    active_ids: set  # ids of the applications that count
    ceiling: object  # the Band that caps the month, or None
    result: dm.Result
    has_inputs: bool  # any adjustment or deduction row exists for the month


def _rule(rule) -> dm.Rule:
    cap = None if rule.uncapped or rule.cap_pct is None else Fraction(rule.cap_pct)
    return dm.Rule(id=rule.pk, code=rule.code, name=rule.name, priority=rule.priority or 0,
                   stacking=rule.stacking, cap_pct=cap)


def _standing(applications) -> list:
    """Applications that are neither reversals nor reversed."""
    reversed_ids = {a.reverses_id for a in applications if a.reverses_id is not None}
    return [a for a in applications if a.reverses_id is None and a.pk not in reversed_ids]


def active_application_ids(performance, applications) -> set:
    return {a.pk for a in _standing(applications)
            if a.rule.plan_version_id == performance.weight_version_id}


def prepare_recalculation(employee, year: int, month: int) -> None:
    """Called in the same transaction just BEFORE the frozen 7.2 engine recalculates a KRA
    month (services.calculate_month). HR's settled adjustment / deduction points and band
    ceiling are set aside, so the engine computes, stores and audits the AUTOMATIC score only;
    settle() re-applies every valid adjustment and deduction right after (E14). Nothing of HR's
    append-only history is touched. A month whose standing deductions were applied under a plan
    version other than the one now applying is refused (they would otherwise stop counting
    silently): HR reverses them under review first."""
    performance = (MonthlyPerformance.objects.select_for_update()
                   .filter(employee=employee, year=year, month=month).first())
    if (performance is None or performance.calculation_model != CalculationModel.KRA_POINTS
            or performance.status not in (PerformanceStatus.DRAFT, PerformanceStatus.CALCULATED)):
        return  # nothing settled to set aside; the engine decides (and refuses) the rest
    standing = _standing(list(DeductionApplication.objects.filter(
        monthly_performance=performance).select_related("rule")))
    if standing:
        resolution = config_services.resolve_plan(employee, month_bounds(year, month)[1])
        version = resolution.version
        if version is not None and any(a.rule.plan_version_id != version.pk for a in standing):
            raise PlanChangedUnderDeductions()
    MonthlyKPIScore.objects.filter(monthly_performance=performance).update(
        adjustment_points=ZERO, deduction_points=ZERO
    )
    if performance.band_ceiling_id is not None:
        MonthlyPerformance.objects.filter(pk=performance.pk).update(band_ceiling=None)


def valid_adjustment(score, adjustment) -> bool:
    return (adjustment is not None and not score.not_applicable
            and ZERO <= adjustment.after_points <= score.weight)


def compute(performance) -> Settlement:
    """Read-only: the settled values of a KRA month (nothing is written)."""
    components = MonthlyComponentResult.objects.select_related("component").order_by("id")
    adjustments = ScoreAdjustment.objects.order_by("created_at", "id")
    scores = list(
        performance.kpi_scores.select_related("kpi").prefetch_related(
            Prefetch("component_results", queryset=components),
            Prefetch("adjustments", queryset=adjustments),
        ).order_by("id")
    )
    applications = list(
        DeductionApplication.objects.filter(monthly_performance=performance)
        .select_related("rule", "ceiling_band", "kpi_score__kpi", "component_result",
                        "applied_by")
        .order_by("applied_at", "id")
    )
    active = active_application_ids(performance, applications)
    latest, applied, inputs = {}, {}, []
    by_score = {}
    for score in scores:
        history = list(score.adjustments.all())
        latest[score.pk] = history[-1] if history else None
        applied[score.pk] = valid_adjustment(score, latest[score.pk])
        by_score[score.pk] = list(score.component_results.all())
        inputs.append(dm.KpiInput(
            id=score.pk, weight=Fraction(score.weight),
            auto=Fraction(score.auto_points or 0), applicable=not score.not_applicable,
            adjusted=Fraction(latest[score.pk].after_points) if applied[score.pk] else None,
            components=tuple(
                dm.ComponentInput(id=r.pk, share=Fraction(r.share_snapshot or 0),
                                  applicable=r.applicable)
                for r in by_score[score.pk]
            ),
        ))
    deductions, ceilings = [], []
    for item in applications:
        if item.pk not in active:
            continue
        if item.rule.kind == DeductionKind.BAND_CEILING:
            ceilings.append(item.ceiling_band or item.rule.ceiling_band)
            continue
        scope = item.rule.scope
        target = {dm.COMPONENT: item.component_result_id, dm.KPI: item.kpi_score_id}.get(scope)
        deductions.append(dm.Deduction(id=item.pk, rule=_rule(item.rule),
                                       percent=Fraction(item.percent), scope=scope,
                                       target_id=target))
    method = performance.weight_version.deduction_stacking_method if (
        performance.weight_version_id) else ""
    result = dm.settle(inputs, deductions, method)
    ceiling = min((c for c in ceilings if c is not None), key=lambda b: b.min_points,
                  default=None)
    return Settlement(
        performance=performance, scores=scores, components=by_score, latest_adjustment=latest,
        adjustment_applied=applied, applications=applications, active_ids=active,
        ceiling=ceiling, result=result,
        has_inputs=bool(applications) or any(latest.values()),
    )


def _set(instance, values: dict) -> list:
    changed = [name for name, value in values.items() if getattr(instance, name) != value]
    for name in changed:
        setattr(instance, name, values[name])
    return changed


def settle(performance) -> Settlement:
    """Recompute and store the settled points, totals and band of a non-finalized KRA month
    from the automatic points and every valid HR adjustment / deduction. Never changes the
    record's version and never audits: the calling action does both. Writes only what changed,
    so a month without adjustments or deductions keeps exactly the engine's values."""
    if performance.status == PerformanceStatus.FINALIZED:
        raise ValueError("A finalized month is never settled again.")
    data = compute(performance)
    totals = {"adjustment": ZERO, "deduction": ZERO, "final": ZERO, "max": ZERO, "auto": ZERO}
    for score in data.scores:
        outcome = data.result.kpis[score.pk]
        if score.not_applicable:
            values = {"adjustment_points": ZERO, "deduction_points": ZERO, "final_points": ZERO}
        else:
            points, final = to_points(outcome.points), to_points(outcome.final)
            values = {"adjustment_points": points - score.auto_points,
                      "deduction_points": points - final, "final_points": final}
            totals["max"] += score.weight
            totals["auto"] += score.auto_points
            totals["adjustment"] += values["adjustment_points"]
            totals["deduction"] += values["deduction_points"]
        totals["final"] += values["final_points"]
        changed = _set(score, values)
        if changed:
            score.save(update_fields=[*changed, "updated_at"])
        for component in data.components[score.pk]:
            taken = outcome.component_deductions.get(component.pk, 0)
            changed = _set(component, {"deduction_points": to_points(taken)})
            if changed:
                component.save(update_fields=changed)
    band = None
    if performance.band_scheme_id is not None:
        band = band_for(totals["final"], list(performance.band_scheme.bands.all()), data.ceiling)
    changed = _set(performance, {
        "max_points_applicable": totals["max"], "auto_total": totals["auto"],
        "adjustment_total": totals["adjustment"], "deduction_total": totals["deduction"],
        "final_total": totals["final"], "band": band, "band_name": band.name if band else "",
        "band_ceiling": data.ceiling,
    })
    if changed:
        performance.save(update_fields=[*changed, "updated_at"])
    return data


# --- presentation (HR / Admin detail, audit snapshots, the employee view) ---------------------


def _target_labels(data: Settlement):
    kpis = {s.pk: s for s in data.scores}
    components = {c.pk: (c, kpis[s_id]) for s_id, rows in data.components.items() for c in rows}
    return kpis, components


def lines(data: Settlement) -> list[dict]:
    """One row per rule and target: the rule, the affected KPI / component and the points."""
    kpis, components = _target_labels(data)
    rows = []
    for line in data.result.lines:
        kpi_label = component_label = ""
        kpi_id = component_id = None
        if line.scope == dm.COMPONENT:
            component, score = components[line.target_id]
            kpi_id, kpi_label = score.kpi_id, score.name_snapshot or score.kpi.name
            component_id, component_label = component.component_id, component.label_snapshot
        elif line.scope == dm.KPI:
            score = kpis[line.target_id]
            kpi_id, kpi_label = score.kpi_id, score.name_snapshot or score.kpi.name
        rows.append({
            "scope": line.scope, "rule_id": line.rule.id, "rule_code": line.rule.code,
            "rule_name": line.rule.name, "kpi_id": kpi_id, "kpi": kpi_label,
            "component_id": component_id, "component": component_label,
            "rate_pct": to_points(line.rate * 100), "effective": line.effective,
            "points": to_points(line.points), "application_ids": list(line.deduction_ids),
        })
    for item in data.applications:
        if item.pk in data.active_ids and item.rule.kind == DeductionKind.BAND_CEILING:
            band = item.ceiling_band or item.rule.ceiling_band
            score = item.kpi_score
            component = item.component_result
            rows.append({
                "scope": item.rule.scope, "rule_id": item.rule_id, "rule_code": item.rule.code,
                "rule_name": item.rule.name,
                "kpi_id": score.kpi_id if score else None,
                "kpi": (score.name_snapshot or score.kpi.name) if score else "",
                "component_id": component.component_id if component else None,
                "component": component.label_snapshot if component else "",
                "rate_pct": None, "effective": band is not None and data.ceiling is not None
                and band.pk == data.ceiling.pk,
                "points": ZERO, "application_ids": [item.pk],
                "ceiling_band": band.name if band else "",
            })
    return rows


def snapshot(data: Settlement) -> dict:
    """What the month's numbers are, for the audit log (finalize / reopen / settle)."""
    performance = data.performance
    return {
        "status": performance.status,
        "auto_total": str(performance.auto_total),
        "adjustment_total": str(performance.adjustment_total),
        "deduction_total": str(performance.deduction_total),
        "final_total": str(performance.final_total),
        "max_points_applicable": str(performance.max_points_applicable),
        "band": performance.band_name,
        "band_ceiling": performance.band_ceiling.name if performance.band_ceiling else None,
        "kpis": {
            s.kpi.code: {
                "not_applicable": s.not_applicable,
                "auto_points": str(s.auto_points),
                "adjustment_points": str(s.adjustment_points),
                "deduction_points": str(s.deduction_points),
                "final_points": str(s.final_points),
            }
            for s in data.scores
        },
        "deductions": [
            {**row, "rate_pct": str(row["rate_pct"]) if row["rate_pct"] is not None else None,
             "points": str(row["points"])}
            for row in lines(data)
        ],
        "active_deduction_ids": sorted(data.active_ids),
    }
