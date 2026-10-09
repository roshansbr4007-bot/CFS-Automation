"""Phase 7.3 KRA monthly review: HR inputs, adjustments, deductions, the review workflow and
reopen. The only write path for these; every change is audited in the same transaction. The API
layer checks WHO (apps.performance.perms); these services check WHAT.

Workflow (KRA months only; the legacy workflow in services.py is unchanged):
    CALCULATED --submit--> UNDER_REVIEW --finalize--> FINALIZED --reopen--> UNDER_REVIEW
    UNDER_REVIEW --return for recalculation--> CALCULATED (recalculated at once, E14)

Approved rules:
- E12  Gap decisions, manual entries and HR-marked N/A: CALCULATED or UNDER_REVIEW. Adjustments
       and deductions: UNDER_REVIEW only. Nothing changes a FINALIZED month.
- E13  Submit only once the month has closed (calculated with the month-end cutoff); the
       blockers (undecided gaps D6, missing manual entries D12) are checked at finalize.
- E14  Return for recalculation recomputes the automatic score and re-applies every valid
       adjustment / deduction through the settlement layer; nothing is deleted.
- E15  Reopen: FINALIZED -> UNDER_REVIEW, KRA months only, mandatory reason, audited.
- E16  HR finalizes; there is no Admin approval step.
- E21  A gap decision may be changed until its month is finalized (audited).
A gap decision, manual entry or N/A takes effect on the stored month at once: the affected
component and KPI are re-scored from the month's stored task credits with the engine's own
functions (task facts are not re-read), then the month is settled. The next calculation of a
CALCULATED month reaches the same result from the same inputs.
"""

from decimal import Decimal, InvalidOperation
from fractions import Fraction

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.services import record
from apps.core.errors import FieldValidationError
from apps.recurring.models import OccurrenceStatus, ScheduleOccurrence
from apps.tasks.models import TaskSource

from . import kra_engine, settlement
from .kra_engine import (
    NA,
    CreditRow,
    benchmark,
    classify_gap,
    component_achievement,
    kpi_achievement,
    kpi_points,
)
from .models import (
    ApprovedLeave,
    CalculationModel,
    ComponentSource,
    DeductionApplication,
    DeductionKind,
    DeductionRule,
    DeductionScope,
    GapDecision,
    GenerationGapDecision,
    ManualTaskOverride,
    MonthlyComponentResult,
    MonthlyPerformance,
    MonthlyTaskCredit,
    OverrideAction,
    PerformanceStatus,
    ScoreAdjustment,
    TaskOutcome,
)
from .services import (
    PerformanceError,
    PerformanceVersionConflict,
    _audit,
    _period_window,
    assignment_for,
    calculate_month,
    month_bounds,
)

CALCULATED = PerformanceStatus.CALCULATED
UNDER_REVIEW = PerformanceStatus.UNDER_REVIEW
FINALIZED = PerformanceStatus.FINALIZED
HR_INPUT_STATES = (CALCULATED, UNDER_REVIEW)  # E12 (corrected)
REVIEW_STATES = (UNDER_REVIEW,)  # E12: adjustments and deductions
SIX = Decimal("0.000001")
CENT = Decimal("0.01")
ZERO = Decimal("0")
HUNDRED = Decimal("100")
STATUS_LABELS = dict(PerformanceStatus.choices)


class NotKraMonth(PerformanceError):
    code = "not_kra_month"
    message = "This month is not a KRA month; the legacy review workflow applies to it."


class KraStateError(PerformanceError):
    code = "kra_review_state"
    message = "This action is not possible in the month's current state."


class FinalizationBlocked(PerformanceError):
    code = "finalization_blocked"
    message = "Resolve the open items before finalizing this month."


# --- small helpers ----------------------------------------------------------------------------


def _required_text(value, field: str) -> str:
    text = (value or "").strip()
    if not text:
        raise FieldValidationError(fields={field: ["This field is required."]})
    return text


def _number(value, field: str, *, places: Decimal, low: Decimal, high: Decimal) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise FieldValidationError(fields={field: ["Enter a number."]}) from None
    if not number.is_finite():
        raise FieldValidationError(fields={field: ["Enter a number."]})
    if number != number.quantize(places):
        raise FieldValidationError(
            fields={field: [f"Use at most {abs(places.as_tuple().exponent)} decimal places."]}
        )
    if not low <= number <= high:
        raise FieldValidationError(fields={field: [f"Use a value from {low} to {high}."]})
    return number


def _points(value) -> Decimal | None:
    return None if value is None else settlement.to_points(value)


def _lock(performance, version, states, action: str) -> MonthlyPerformance:
    locked = MonthlyPerformance.objects.select_for_update().get(pk=performance.pk)
    if locked.calculation_model != CalculationModel.KRA_POINTS:
        raise NotKraMonth()
    if version is not None and locked.version != version:
        raise PerformanceVersionConflict()
    if locked.status not in states:
        raise KraStateError(
            f"{action} is not possible while the month is "
            f"{STATUS_LABELS.get(locked.status, locked.status).lower()}."
        )
    return locked


def month_closed(performance) -> bool:
    """Calculated with the month-end cutoff (not provisional)."""
    _, end = _period_window(performance.period_start, performance.period_end)
    return performance.cutoff_at is not None and performance.cutoff_at >= end


def _bump(performance) -> None:
    performance.version += 1
    performance.save(update_fields=["version", "updated_at"])


def _settle_and_bump(performance):
    data = settlement.settle(performance)
    _bump(performance)
    return data


def describe_blocker(blocker: dict) -> str:
    if blocker["kind"] == "UNDECIDED_GAP":
        return (f"Generation gap {blocker['occurrence_id']} ({blocker['component']}): "
                "decide whether it was a system issue or the employee's responsibility.")
    return f"{blocker['component']}: enter the HR score or mark it not applicable."


# --- workflow -----------------------------------------------------------------------------------


def calculate(*, actor, employee, year: int, month: int, now=None) -> MonthlyPerformance:
    """HR's KRA calculation (API): refused, before anything is written, when the employee's
    plan for the month is a legacy weight assignment (the legacy engine stays on demand)."""
    _, period_end = month_bounds(year, month)
    assignment = assignment_for(employee, period_end)
    if (assignment is not None
            and assignment.weight_version.calculation_model == CalculationModel.LEGACY_WEIGHTED):
        raise NotKraMonth("This employee's plan for the month is a legacy weight assignment.")
    return calculate_month(actor=actor, employee=employee, year=year, month=month, now=now)


def submit(*, actor, performance, version: int) -> MonthlyPerformance:
    """CALCULATED -> UNDER_REVIEW, once the month has closed (E13)."""
    with transaction.atomic():
        performance = _lock(performance, version, (CALCULATED,), "Submitting for review")
        if not month_closed(performance):
            raise KraStateError(
                "This month is still provisional. Recalculate it after the month has ended, "
                "then submit it for review."
            )
        performance.status = UNDER_REVIEW
        performance.reviewed_by = actor
        performance.reviewed_at = timezone.now()
        performance.version += 1
        performance.save()
        _audit("performance.kra_submitted", performance, actor,
               old={"status": CALCULATED}, new={"status": UNDER_REVIEW})
    return performance


def return_for_recalculation(*, actor, performance, version: int, reason: str,
                             now=None) -> MonthlyPerformance:
    """UNDER_REVIEW -> CALCULATED and recalculated at once (E14): the automatic score is
    recomputed and every valid adjustment / deduction is re-applied by the settlement layer.
    If the recalculation is refused, nothing changes (one transaction). A reason is required
    (7.3 decision 6) and checked before anything is locked or written."""
    reason = _required_text(reason, "reason")
    with transaction.atomic():
        performance = _lock(performance, version, REVIEW_STATES, "Returning for recalculation")
        performance.status = CALCULATED
        performance.reviewed_by = None
        performance.reviewed_at = None
        performance.version += 1
        performance.save()
        _audit("performance.kra_returned", performance, actor,
               old={"status": UNDER_REVIEW}, new={"status": CALCULATED, "reason": reason})
        # KRA path only (E15): a month never becomes a legacy month on its way back.
        return calculate(actor=actor, employee=performance.employee, year=performance.year,
                         month=performance.month, now=now)


def finalize(*, actor, performance, version: int) -> MonthlyPerformance:
    """UNDER_REVIEW -> FINALIZED (HR, E16). Refused while any blocker is open (D6, D12)."""
    with transaction.atomic():
        performance = _lock(performance, version, REVIEW_STATES, "Finalizing")
        if not month_closed(performance):
            raise KraStateError("This month is still provisional; it cannot be finalized.")
        blockers = kra_engine.finalization_blockers(performance)
        if blockers:
            raise FinalizationBlocked(fields={"blockers": [describe_blocker(b) for b in blockers]})
        data = settlement.settle(performance)
        performance.status = FINALIZED
        performance.finalized_by = actor
        performance.finalized_at = timezone.now()
        performance.version += 1
        performance.save()
        _audit("performance.kra_finalized", performance, actor,
               old={"status": UNDER_REVIEW}, new=settlement.snapshot(data))
    return performance


def reopen(*, actor, performance, version: int, reason: str) -> MonthlyPerformance:
    """FINALIZED -> UNDER_REVIEW (HR or Admin, E15). Mandatory reason; KRA months only. The
    finalized numbers are kept in the audit log; nothing else of the record changes."""
    reason = _required_text(reason, "reason")
    with transaction.atomic():
        performance = _lock(performance, version, (FINALIZED,), "Reopening")
        finalized = settlement.snapshot(settlement.compute(performance))
        finalized["finalized_by_id"] = performance.finalized_by_id
        finalized["finalized_at"] = (
            performance.finalized_at.isoformat() if performance.finalized_at else None
        )
        performance.status = UNDER_REVIEW
        performance.reopen_count += 1
        performance.finalized_by = None
        performance.finalized_at = None
        performance.version += 1
        performance.save(reopen=True, update_fields=[
            "status", "reopen_count", "finalized_by", "finalized_at", "version", "updated_at",
        ])
        _audit("performance.kra_reopened", performance, actor, old=finalized,
               new={"status": UNDER_REVIEW, "reopen_count": performance.reopen_count,
                    "reason": reason})
    return performance


# --- HR adjustment (E1, E2) ---------------------------------------------------------------------


def adjust_kpi(*, actor, performance, version: int, kpi_id: int, points, reason: str):
    """HR sets a KPI's points (before deductions) anywhere within [0, KPI maximum]. Recorded
    append-only with before / adjustment / after, reason, HR user and time; the latest wins."""
    reason = _required_text(reason, "reason")
    with transaction.atomic():
        performance = _lock(performance, version, REVIEW_STATES, "Adjusting a KPI")
        score = performance.kpi_scores.select_related("kpi").filter(kpi_id=kpi_id).first()
        if score is None:
            raise FieldValidationError(fields={"kpi": ["This KPI is not part of this month."]})
        if score.not_applicable:
            raise FieldValidationError(
                fields={"kpi": ["This KPI is not applicable this month; it earns no points."]}
            )
        value = _number(points, "points", places=SIX, low=ZERO, high=score.weight)
        latest = score.adjustments.order_by("-created_at", "-id").first()
        before = (latest.after_points if settlement.valid_adjustment(score, latest)
                  else score.auto_points)
        ScoreAdjustment.objects.create(
            kpi_score=score, before_points=before, adjustment_points=value - before,
            after_points=value, reason=reason, actor=actor,
        )
        data = _settle_and_bump(performance)
        score.refresh_from_db()
        _audit("performance.kra_adjusted", performance, actor,
               old={"kpi": score.kpi.code, "points": str(before)},
               new={"kpi": score.kpi.code, "points": str(value),
                    "adjustment": str(value - before), "reason": reason,
                    "final_points": str(score.final_points),
                    "final_total": str(performance.final_total)},
               extra={"auto_points": str(score.auto_points),
                      "settled": bool(data.adjustment_applied.get(score.pk))})
    return performance


# --- deductions (E3-E11) ------------------------------------------------------------------------


def apply_deduction(*, actor, performance, version: int, rule_id: int, reason: str,
                    percent=None, kpi_id: int | None = None, component_id: int | None = None,
                    evidence: str = ""):
    """HR applies a configured deduction rule of the month's plan, with a reason (and optional
    evidence). PERCENT_RANGE: a percentage within the rule's range. BAND_CEILING: no
    percentage; the rule's ceiling band caps the month's band. The target follows the rule's
    scope: COMPONENT (component), KPI (kpi) or OVERALL (neither)."""
    reason = _required_text(reason, "reason")
    evidence = (evidence or "").strip()
    with transaction.atomic():
        performance = _lock(performance, version, REVIEW_STATES, "Applying a deduction")
        rule = (DeductionRule.objects.select_related("ceiling_band")
                .filter(pk=rule_id, plan_version_id=performance.weight_version_id).first())
        if rule is None:
            raise FieldValidationError(
                fields={"rule": ["Choose a deduction rule of this month's KPI plan."]}
            )
        kpi_score, component_result = _deduction_target(performance, rule, kpi_id, component_id)
        value, ceiling = None, None
        if rule.kind == DeductionKind.PERCENT_RANGE:
            if percent is None or percent == "":
                raise FieldValidationError(fields={"percent": ["This field is required."]})
            value = _number(percent, "percent", places=CENT, low=rule.min_pct,
                            high=rule.max_pct)
        else:
            if percent not in (None, ""):
                raise FieldValidationError(
                    fields={"percent": ["A band-ceiling rule has no percentage."]}
                )
            ceiling = rule.ceiling_band
        application = DeductionApplication.objects.create(
            monthly_performance=performance, rule=rule, kpi_score=kpi_score,
            component_result=component_result, percent=value, ceiling_band=ceiling,
            evidence=evidence, reason=reason, applied_by=actor,
        )
        _settle_and_bump(performance)
        _audit("performance.kra_deduction_applied", performance, actor,
               new={"application_id": application.pk, "rule": rule.code, "scope": rule.scope,
                    "kpi_score_id": kpi_score.pk if kpi_score else None,
                    "component_result_id": component_result.pk if component_result else None,
                    "percent": str(value) if value is not None else None,
                    "ceiling_band": ceiling.name if ceiling else None,
                    "evidence": evidence, "reason": reason,
                    "deduction_total": str(performance.deduction_total),
                    "final_total": str(performance.final_total), "band": performance.band_name})
    return performance


def _deduction_target(performance, rule, kpi_id, component_id):
    if rule.scope == DeductionScope.COMPONENT:
        if component_id is None:
            raise FieldValidationError(
                fields={"component": ["This rule applies to a component; choose one."]}
            )
        result = (MonthlyComponentResult.objects.select_related("kpi_score")
                  .filter(kpi_score__monthly_performance=performance,
                          component_id=component_id).first())
        if result is None or (kpi_id is not None and result.kpi_score.kpi_id != kpi_id):
            raise FieldValidationError(
                fields={"component": ["This component is not part of this month."]}
            )
        if not result.applicable or result.kpi_score.not_applicable:
            raise FieldValidationError(
                fields={"component": ["This component is not applicable this month."]}
            )
        return result.kpi_score, result
    if rule.scope == DeductionScope.KPI:
        if kpi_id is None or component_id is not None:
            raise FieldValidationError(
                fields={"kpi": ["This rule applies to a whole KPI; choose the KPI only."]}
            )
        score = performance.kpi_scores.filter(kpi_id=kpi_id).first()
        if score is None:
            raise FieldValidationError(fields={"kpi": ["This KPI is not part of this month."]})
        if score.not_applicable:
            raise FieldValidationError(fields={"kpi": ["This KPI is not applicable this month."]})
        return score, None
    if kpi_id is not None or component_id is not None:
        raise FieldValidationError(
            fields={"rule": ["This rule applies to the whole month; choose no KPI or component."]}
        )
    return None, None


def reverse_deduction(*, actor, performance, version: int, application_id: int, reason: str):
    """Append-only reversal: a new row pointing at the application it cancels."""
    reason = _required_text(reason, "reason")
    with transaction.atomic():
        performance = _lock(performance, version, REVIEW_STATES, "Reversing a deduction")
        original = (DeductionApplication.objects.select_related("rule")
                    .filter(pk=application_id, monthly_performance=performance).first())
        if original is None:
            raise FieldValidationError(
                fields={"application": ["This deduction is not part of this month."]}
            )
        if original.reverses_id is not None:
            raise FieldValidationError(fields={"application": ["A reversal cannot be reversed."]})
        if DeductionApplication.objects.filter(reverses=original).exists():
            raise FieldValidationError(
                fields={"application": ["This deduction has already been reversed."]}
            )
        reversal = DeductionApplication.objects.create(
            monthly_performance=performance, rule=original.rule, kpi_score=original.kpi_score,
            component_result=original.component_result, percent=original.percent,
            ceiling_band=original.ceiling_band, reason=reason, applied_by=actor,
            reverses=original,
        )
        _settle_and_bump(performance)
        _audit("performance.kra_deduction_reversed", performance, actor,
               old={"application_id": original.pk, "rule": original.rule.code},
               new={"reversal_id": reversal.pk, "reason": reason,
                    "deduction_total": str(performance.deduction_total),
                    "final_total": str(performance.final_total), "band": performance.band_name})
    return performance


# --- HR inputs that change the automatic score (E12, E21) ---------------------------------------


def _rescore_kpi(score) -> None:
    """Re-score one KPI from its STORED component results and task credits with the engine's
    own functions (benchmark, normalisation, points); the same values the engine stores."""
    results = list(
        score.component_results.select_related("component").prefetch_related("task_credits")
        .order_by("id")
    )
    parts = []
    for result in results:
        if result.component.source_type == ComponentSource.MANUAL_ENTRY:
            achievement = None
            if (result.entered_by_id is not None and result.applicable
                    and result.manual_achievement_pct is not None):
                achievement = Fraction(result.manual_achievement_pct)
            result.applicable = achievement is not None
            result.na_reason = "" if result.applicable else (
                result.na_reason or NA.AWAITING_HR_ENTRY
            )
        else:
            rows = [CreditRow(outcome=c.outcome, credit=c.credit)
                    for c in result.task_credits.all()]
            achievement = component_achievement(rows)
            result.applicable = achievement is not None
            result.na_reason = "" if result.applicable else NA.NO_APPLICABLE_TASKS
            result.on_time_count = sum(r.outcome == TaskOutcome.ON_TIME for r in rows)
            result.late_count = sum(r.outcome == TaskOutcome.LATE for r in rows)
            result.overdue_count = sum(r.outcome == TaskOutcome.OVERDUE for r in rows)
            result.excluded_count = sum(r.outcome == TaskOutcome.NA for r in rows)
            result.credit_sum = sum((r.credit for r in rows if r.applicable), ZERO)
        result.achievement_pct = _points(achievement)
        parts.append((result.component_id, result.share_snapshot, achievement))
    achievement, shares = kpi_achievement(parts)
    for result in results:
        result.normalized_share = _points(shares.get(result.component_id))
        result.save()
    score.achievement_pct = _points(achievement)
    if achievement is None:
        score.not_applicable, score.na_reason = True, NA.ALL_COMPONENTS_NA
        score.benchmark_pct, score.auto_points = None, ZERO
    else:
        rule = score.scoring_rule
        steps = [(s.min_achievement_pct, s.score_pct) for s in rule.steps.all()]
        score.not_applicable, score.na_reason = False, ""
        score.benchmark_pct = benchmark(achievement, steps, rule.below_min_score_pct)
        score.auto_points = kpi_points(score.weight, score.benchmark_pct)
    score.save()  # final points are written by the settlement that follows


def decide_gap(*, actor, performance, version: int, occurrence_id: int, decision: str,
               reason: str):
    """D6 / E21: HR decides (or changes, until the month is finalized) whether an
    expected-but-not-generated occurrence of this month was a system issue (excluded) or the
    employee's responsibility (counts as 0)."""
    reason = _required_text(reason, "reason")
    if decision not in GapDecision.values:
        raise FieldValidationError(
            fields={"decision": [f"Use one of: {', '.join(GapDecision.values)}."]}
        )
    with transaction.atomic():
        performance = _lock(performance, version, HR_INPUT_STATES, "Deciding a generation gap")
        rows = list(
            MonthlyTaskCredit.objects.filter(
                component_result__kpi_score__monthly_performance=performance,
                occurrence_id=occurrence_id,
            ).select_related("component_result__kpi_score").order_by("id")
        )
        if not rows:
            raise FieldValidationError(fields={"occurrence": [
                "This occurrence is not a generation gap of this month's calculation."
            ]})
        finalized_elsewhere = MonthlyTaskCredit.objects.filter(
            occurrence_id=occurrence_id,
            component_result__kpi_score__monthly_performance__status=FINALIZED,
        ).exists()
        if finalized_elsewhere:
            raise KraStateError("This gap is part of a finalized month; reopen that month first.")
        occurrence = ScheduleOccurrence.objects.get(pk=occurrence_id)
        if occurrence.status not in (OccurrenceStatus.MISSED, OccurrenceStatus.FAILED):
            raise FieldValidationError(
                fields={"occurrence": ["This occurrence is no longer missed or failed."]}
            )
        existing = (GenerationGapDecision.objects.select_for_update()
                    .filter(occurrence=occurrence).first())
        old = ({"decision": existing.decision, "reason": existing.reason,
                "decided_by_id": existing.decided_by_id} if existing else None)
        if existing is None:
            GenerationGapDecision.objects.create(occurrence=occurrence, decision=decision,
                                                 reason=reason, decided_by=actor)
        else:
            existing.decision, existing.reason = decision, reason
            existing.decided_by, existing.decided_at = actor, timezone.now()
            existing.save()
        scores = {}
        for row in rows:
            if row.na_reason in (NA.BEFORE_JOINING, NA.APPROVED_LEAVE):
                continue  # not the employee's day: the decision does not change it
            row.outcome, row.credit, row.na_reason = classify_gap(decision)
            row.save(update_fields=["outcome", "credit", "na_reason"])
            score = row.component_result.kpi_score
            scores[score.pk] = score
        for score in scores.values():
            _rescore_kpi(score)
        _settle_and_bump(performance)
        _audit("performance.kra_gap_decided", performance, actor, old=old,
               new={"decision": decision, "reason": reason,
                    "final_total": str(performance.final_total)},
               extra={"occurrence_id": occurrence_id})
    return performance


def _manual_result(performance, component_id) -> MonthlyComponentResult:
    result = (MonthlyComponentResult.objects.select_related("kpi_score", "component")
              .filter(kpi_score__monthly_performance=performance, component_id=component_id)
              .first())
    if result is None or result.component.source_type != ComponentSource.MANUAL_ENTRY:
        raise FieldValidationError(
            fields={"component": ["Choose a manual-entry component of this month."]}
        )
    return result


def _manual_snapshot(result) -> dict:
    return {
        "applicable": result.applicable, "na_reason": result.na_reason,
        "manual_achievement_pct": (str(result.manual_achievement_pct)
                                   if result.manual_achievement_pct is not None else None),
        "entered_by_id": result.entered_by_id,
    }


def _record_manual(actor, performance, version, component_id, action, apply, reason):
    with transaction.atomic():
        performance = _lock(performance, version, HR_INPUT_STATES, action)
        result = _manual_result(performance, component_id)
        old = _manual_snapshot(result)
        apply(result)
        result.entered_by = actor
        result.entered_at = timezone.now()
        result.save()
        _rescore_kpi(result.kpi_score)
        _settle_and_bump(performance)
        _audit("performance.kra_manual_entry", performance, actor, old=old,
               new={**_manual_snapshot(result), "reason": reason,
                    "final_total": str(performance.final_total)},
               extra={"component_id": component_id, "component": result.label_snapshot})
    return performance


def enter_manual_score(*, actor, performance, version: int, component_id: int, achievement_pct,
                       reason: str):
    """D12: HR's achievement % (0-100) for a manual-entry component; scored by the KPI's
    benchmark like any other achievement. Survives recalculation (7.2). A reason is required
    (7.3 decision 6) and checked before anything is locked or written."""
    reason = _required_text(reason, "reason")
    value = _number(achievement_pct, "achievement_pct", places=SIX, low=ZERO, high=HUNDRED)

    def apply(result):
        result.manual_achievement_pct = value
        result.applicable = True
        result.na_reason = ""

    return _record_manual(actor, performance, version, component_id, "Entering a manual score",
                          apply, reason)


def mark_manual_na(*, actor, performance, version: int, component_id: int, reason: str):
    """D12: HR marks a manual-entry component not applicable for the month, with a reason."""
    reason = _required_text(reason, "reason")

    def apply(result):
        result.manual_achievement_pct = None
        result.applicable = False
        result.na_reason = NA.HR_MARKED_NA

    return _record_manual(actor, performance, version, component_id,
                          "Marking a component not applicable", apply, reason)


# --- approved leave and manual-task exceptions (E20) ------------------------------------------
# Facts the engine reads at the NEXT calculation; they never change a stored month by themselves
# (a finalized month changes only through reopen + return for recalculation).


def _leave_snapshot(leave) -> dict:
    return {"employee_id": leave.employee_id, "start_date": leave.start_date.isoformat(),
            "end_date": leave.end_date.isoformat(), "reason": leave.reason,
            "cancelled": leave.cancelled_at is not None}


def record_leave(*, actor, employee, start_date, end_date, reason: str = "") -> ApprovedLeave:
    if end_date < start_date:
        raise FieldValidationError(fields={"end_date": ["Must be on or after the start date."]})
    with transaction.atomic():
        leave = ApprovedLeave.objects.create(employee=employee, start_date=start_date,
                                             end_date=end_date, reason=(reason or "").strip(),
                                             recorded_by=actor)
        record(action="performance.leave_recorded", entity_type="approved_leave",
               entity_id=leave.pk, actor=actor, new=_leave_snapshot(leave))
    return leave


def cancel_leave(*, actor, leave, reason: str) -> ApprovedLeave:
    reason = _required_text(reason, "reason")
    with transaction.atomic():
        leave = ApprovedLeave.objects.select_for_update().get(pk=leave.pk)
        if leave.cancelled_at is not None:
            raise KraStateError("This leave has already been cancelled.")
        old = _leave_snapshot(leave)
        leave.cancelled_by = actor
        leave.cancelled_at = timezone.now()
        leave.save(update_fields=["cancelled_by", "cancelled_at"])
        record(action="performance.leave_cancelled", entity_type="approved_leave",
               entity_id=leave.pk, actor=actor, old=old,
               new={**_leave_snapshot(leave), "cancel_reason": reason})
    return leave


def _override_snapshot(row) -> dict:
    return {"task_id": row.task_id, "responsibility_id": row.responsibility_id,
            "action": row.action, "reason": row.reason}


def create_task_override(*, actor, task, responsibility, action: str,
                         reason: str) -> ManualTaskOverride:
    """EXCEPTION ONLY: include or exclude one MANUAL task for one responsibility."""
    reason = _required_text(reason, "reason")
    if action not in OverrideAction.values:
        raise FieldValidationError(
            fields={"action": [f"Use one of: {', '.join(OverrideAction.values)}."]}
        )
    if task.source != TaskSource.MANUAL:
        raise FieldValidationError(
            fields={"task": ["Only manual tasks can be included or excluded by HR."]}
        )
    duplicate = KraStateError(
        "This task already has an exception for this responsibility; remove it first."
    )
    with transaction.atomic():
        if ManualTaskOverride.objects.filter(task=task, responsibility=responsibility).exists():
            raise duplicate
        try:
            with transaction.atomic():  # a simultaneous request: the unique constraint decides
                row = ManualTaskOverride.objects.create(
                    task=task, responsibility=responsibility, action=action, reason=reason,
                    created_by=actor,
                )
        except IntegrityError:
            raise duplicate from None
        record(action="performance.task_override_created", entity_type="manual_task_override",
               entity_id=row.pk, actor=actor, new=_override_snapshot(row))
    return row


def remove_task_override(*, actor, override, reason: str) -> None:
    reason = _required_text(reason, "reason")
    with transaction.atomic():
        row = ManualTaskOverride.objects.select_for_update().get(pk=override.pk)
        old = _override_snapshot(row)
        pk = row.pk
        row.delete()
        record(action="performance.task_override_removed", entity_type="manual_task_override",
               entity_id=pk, actor=actor, old=old, new={"removed": True, "reason": reason})


# --- read models ------------------------------------------------------------------------------


def blockers(performance) -> list[dict]:
    if performance.calculation_model != CalculationModel.KRA_POINTS:
        raise NotKraMonth()
    return [{**b, "message": describe_blocker(b)}
            for b in kra_engine.finalization_blockers(performance)]


def month_detail(performance) -> dict:
    """HR / Admin view of a KRA month: automatic and final points, every adjustment and
    deduction with its reason and evidence, the settlement lines and the open blockers."""
    if performance.calculation_model != CalculationModel.KRA_POINTS:
        raise NotKraMonth()
    data = settlement.compute(performance)
    kpis = []
    for score in data.scores:
        outcome = data.result.kpis[score.pk]
        latest = data.latest_adjustment[score.pk]
        kpis.append({
            "kpi_id": score.kpi_id, "code": score.kpi.code,
            "name": score.name_snapshot or score.kpi.name, "weight": score.weight,
            "not_applicable": score.not_applicable, "na_reason": score.na_reason,
            "achievement_pct": score.achievement_pct, "benchmark_pct": score.benchmark_pct,
            "auto_points": score.auto_points, "adjustment_points": score.adjustment_points,
            "deduction_points": score.deduction_points, "final_points": score.final_points,
            "points_before_deductions": _points(outcome.points),
            "adjustment_applied": data.adjustment_applied[score.pk],
            "latest_adjustment_id": latest.pk if latest else None,
            "components": [
                {"component_id": c.component_id, "label": c.label_snapshot,
                 "source_type": c.component.source_type, "applicable": c.applicable,
                 "na_reason": c.na_reason, "normalized_share": c.normalized_share,
                 "achievement_pct": c.achievement_pct,
                 "manual_achievement_pct": c.manual_achievement_pct,
                 "entered_by": c.entered_by_id, "entered_at": c.entered_at,
                 "on_time_count": c.on_time_count, "late_count": c.late_count,
                 "overdue_count": c.overdue_count, "excluded_count": c.excluded_count,
                 "deduction_points": c.deduction_points}
                for c in data.components[score.pk]
            ],
            "adjustments": [
                {"id": a.pk, "before_points": a.before_points,
                 "adjustment_points": a.adjustment_points, "after_points": a.after_points,
                 "reason": a.reason, "actor": a.actor_id, "created_at": a.created_at}
                for a in score.adjustments.all()
            ],
        })
    reversed_by = {a.reverses_id: a.pk for a in data.applications if a.reverses_id}
    applications = [
        {"id": a.pk, "rule_id": a.rule_id, "rule_code": a.rule.code, "rule_name": a.rule.name,
         "kind": a.rule.kind, "scope": a.rule.scope,
         "kpi_id": a.kpi_score.kpi_id if a.kpi_score else None,
         "component_id": a.component_result.component_id if a.component_result else None,
         "percent": a.percent, "ceiling_band": a.ceiling_band.name if a.ceiling_band else "",
         "evidence": a.evidence, "reason": a.reason, "applied_by": a.applied_by_id,
         "applied_at": a.applied_at, "reverses": a.reverses_id,
         "reversed_by": reversed_by.get(a.pk), "active": a.pk in data.active_ids}
        for a in data.applications
    ]
    return {
        "id": performance.pk, "employee": performance.employee_id,
        "year": performance.year, "month": performance.month, "status": performance.status,
        "version": performance.version, "plan_version": performance.weight_version_id,
        "cutoff_at": performance.cutoff_at, "provisional": not month_closed(performance),
        "max_points_applicable": performance.max_points_applicable,
        "auto_total": performance.auto_total, "adjustment_total": performance.adjustment_total,
        "deduction_total": performance.deduction_total, "final_total": performance.final_total,
        "band": performance.band_name,
        "band_ceiling": performance.band_ceiling.name if performance.band_ceiling else "",
        "reopen_count": performance.reopen_count,
        "reviewed_by": performance.reviewed_by_id, "reviewed_at": performance.reviewed_at,
        "finalized_by": performance.finalized_by_id, "finalized_at": performance.finalized_at,
        "kpis": kpis, "deduction_applications": applications,
        "deduction_lines": settlement.lines(data), "blockers": blockers(performance),
    }


def employee_view(performance) -> dict:
    """What the EMPLOYEE may see of a KRA month (E19; Employee Home is Phase 7.4): automatic
    and final points, the band, and each deduction as rule name, affected KPI / component and
    points only. No evidence, reason, HR user or adjustment reason."""
    data = settlement.compute(performance)
    return {
        "year": performance.year, "month": performance.month, "status": performance.status,
        "auto_total": performance.auto_total, "final_total": performance.final_total,
        "max_points_applicable": performance.max_points_applicable,
        "band": performance.band_name,
        "kpis": [
            {"name": s.name_snapshot or s.kpi.name, "not_applicable": s.not_applicable,
             "auto_points": s.auto_points, "final_points": s.final_points,
             "components": [{"label": c.label_snapshot, "applicable": c.applicable,
                             "achievement_pct": c.achievement_pct}
                            for c in data.components[s.pk]]}
            for s in data.scores
        ],
        "deductions": [
            {"rule": row["rule_name"], "kpi": row["kpi"], "component": row["component"],
             "points": row["points"]}
            for row in settlement.lines(data) if row["effective"]
        ],
    }
