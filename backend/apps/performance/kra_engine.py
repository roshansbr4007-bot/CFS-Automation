"""Phase 7.2 KRA calculation engine. A CONSUMER of Task / SLA / recurring data: it reads them
and writes only performance rows (MonthlyPerformance and its KRA breakdown).

Approved rules (Phase 7.1 decisions P1-P15 and Phase 7.2 decisions D1-D9, D12):
- Plan: the one in effect on the LAST day of the month (D9), via config_services.resolve_plan.
- Credit goes to the task's current assignee (P4). A task belongs to the month of its
  applicable deadline; a task without a started deadline to the month it was assigned in, where
  it is N/A (P1, P6, D3). The applicable deadline is ALWAYS the SLA engine's own answer
  (apps.sla.services.task_sla at the cutoff, which includes the HOLD rule); nothing here
  recomputes it, and being on hold never makes a task N/A by itself.
- The cutoff is authoritative (D4): min(now, month end). Completion after the cutoff does not
  count; cancellation before the cutoff excludes the task; cancellation after it does not.
- A rejected verification reopens the task (D1). On time / late compares the ACCEPTED
  completion with the applicable deadline (D2): for a verification-required component the
  submission that was verified; otherwise the latest submission not rejected by the cutoff. The
  SLA clock itself is never touched.
- Credits come from the plan version (on time / late / overdue); N/A is excluded from the
  denominator. Components are normalised over the applicable ones; a KPI whose components are all
  N/A is N/A, earns 0 and is left out of the applicable maximum - nothing is rescaled (P8).
- Verification-required components count a task only once VERIFIED; a pending verification is
  N/A until the deadline, then 0 (P7). A matched task that cannot be verified is a configuration
  error: the month is refused, never scored (D5).
- Applicability: joining date; approved leave on the occurrence date (scheduled) or the deadline
  date (manual) (D7); manual matches of a responsibility deactivated at the deadline are N/A,
  read from the responsibility.deactivated audit events (D8); generation gaps (D6).
- Manual-entry components are N/A until HR enters a score or marks them N/A (D12); undecided
  gaps and missing manual entries are reported by finalization_blockers() for Phase 7.3.
No deduction or adjustment is created here; stored ones (Phase 7.3) are carried into totals.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from django.db import transaction
from django.db.models import Exists, OuterRef, Prefetch, Q
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.audit.services import record
from apps.core.timeutils import to_ist
from apps.recurring.models import OccurrenceStatus, ScheduleOccurrence
from apps.recurring.services import current_owner_row
from apps.sla import services as sla
from apps.sla.models import ClockKind, TaskSla
from apps.tasks.models import Task, TaskSource, TaskStatus, VerificationDecision

from . import config_services
from .models import (
    ApprovedLeave,
    CalculationModel,
    ComponentSource,
    DeductionApplication,
    GapDecision,
    GenerationGapDecision,
    KPIComponent,
    KPIWeight,
    ManualMatch,
    ManualTaskOverride,
    MatchReason,
    MonthlyComponentResult,
    MonthlyKPIScore,
    MonthlyPerformance,
    MonthlyTaskCredit,
    OverrideAction,
    PerformanceStatus,
    ScoreAdjustment,
    ScoreSource,
    TaskOutcome,
    TaskScope,
    VerificationPolicy,
)
from .services import (
    NoKPIAssignment,
    PerformanceError,
    _period_window,
    authoritative_resolution_clock,
    month_bounds,
)

SIX = Decimal("0.000001")
ZERO = Decimal("0")
HUNDRED = Decimal("100")
MANUAL_SCOPES = (TaskScope.MANUAL, TaskScope.BOTH)
SCHEDULED_SCOPES = (TaskScope.SCHEDULED, TaskScope.BOTH)


class NA:
    """Reason codes stored with every N/A result (never shown as free text guesses)."""

    CANCELLED = "CANCELLED_BEFORE_CUTOFF"
    NO_DEADLINE = "NO_DEADLINE"
    NOT_STARTED = "DEADLINE_NOT_STARTED"
    NOT_DUE = "NOT_DUE_BY_CUTOFF"
    PENDING_VERIFICATION = "PENDING_VERIFICATION_NOT_DUE"
    BEFORE_JOINING = "BEFORE_JOINING"
    APPROVED_LEAVE = "APPROVED_LEAVE"
    RESPONSIBILITY_DEACTIVATED = "RESPONSIBILITY_DEACTIVATED"
    GAP_SYSTEM_ISSUE = "GAP_SYSTEM_ISSUE"
    GAP_UNDECIDED = "GAP_UNDECIDED"
    AWAITING_HR_ENTRY = "AWAITING_HR_ENTRY"
    HR_MARKED_NA = "HR_MARKED_NA"  # written by the Phase 7.3 manual-entry action
    NO_APPLICABLE_TASKS = "NO_APPLICABLE_TASKS"
    ALL_COMPONENTS_NA = "ALL_COMPONENTS_NA"


# --- errors -------------------------------------------------------------------------------------


class NoKraPlan(NoKPIAssignment):
    code = "no_kpi_plan"
    message = "No KPI plan applies to the employee for this month."


class AmbiguousRole(NoKraPlan):
    code = "ambiguous_role"
    message = "Several of the employee's system roles have a plan default; set an HR override."


class MonthNotStarted(PerformanceError):
    code = "month_not_started"
    message = "This month has not started yet."


class NotEmployedInPeriod(PerformanceError):
    code = "not_employed_in_period"
    message = "The employee joined after this month."


class VerificationConfigurationError(PerformanceError):
    """D5: a verification-required component matched tasks that have no verification step.
    The month is refused (nothing is written) until the configuration or the task is fixed."""

    code = "verification_configuration"
    message = "A verification-required component matched tasks without verification."


class UnderReviewNotRecalculated(PerformanceError):
    code = "under_review"
    message = "This month is under review; it is not recalculated (policy: Phase 7.3)."


# =============================================================================================
# Pure functions (no database access)
# =============================================================================================


@dataclass(frozen=True)
class Credits:
    on_time: Decimal
    late: Decimal
    overdue: Decimal


@dataclass(frozen=True)
class TaskFacts:
    """What the engine knows about one task AT THE CUTOFF."""

    task_id: int
    reference: str
    source: str
    responsibility_id: int | None
    template_id: int | None
    category_id: int | None
    department_id: int | None
    occurrence_date: date | None
    has_clock: bool
    deadline: datetime | None  # the SLA engine's applicable deadline; None: not started
    accepted_completion: datetime | None  # latest submission not rejected by the cutoff (D1)
    verified_completion: datetime | None  # submission verified by the cutoff (D2)
    pending_verification_at_cutoff: bool
    cancelled_before_cutoff: bool
    verification_required: bool


@dataclass(frozen=True)
class Applicability:
    """Per-task applicability facts decided by the caller."""

    before_joining: bool = False
    on_leave: bool = False
    responsibility_inactive: bool = False


@dataclass
class CreditRow:
    outcome: str
    credit: Decimal | None
    na_reason: str = ""
    task_id: int | None = None
    occurrence_id: int | None = None
    reference: str = ""
    match_reason: str = ""
    deadline_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def applicable(self) -> bool:
        return self.outcome != TaskOutcome.NA


def _na(reason: str) -> tuple[str, None, str]:
    return TaskOutcome.NA, None, reason


def accepted_for(facts: TaskFacts, verification: str) -> datetime | None:
    """The completion that counts for the component (D1, D2)."""
    if verification == VerificationPolicy.REQUIRED:
        return facts.verified_completion
    return facts.accepted_completion


def classify_task(facts: TaskFacts, *, verification: str, credits: Credits, cutoff: datetime,
                  applicability: Applicability | None = None) -> tuple[str, Decimal | None, str]:
    """(outcome, credit, na_reason) of one task for one component at the cutoff. The caller
    has already refused verification mismatches (D5)."""
    applicability = applicability or Applicability()
    if facts.cancelled_before_cutoff:
        return _na(NA.CANCELLED)
    if not facts.has_clock:
        return _na(NA.NO_DEADLINE)
    if facts.deadline is None:
        return _na(NA.NOT_STARTED)
    if applicability.before_joining:
        return _na(NA.BEFORE_JOINING)
    if applicability.on_leave:
        return _na(NA.APPROVED_LEAVE)
    if applicability.responsibility_inactive:
        return _na(NA.RESPONSIBILITY_DEACTIVATED)
    accepted = accepted_for(facts, verification)
    if accepted is not None:
        if accepted <= facts.deadline:  # the SLA engine's own boundary (on time if <=)
            return TaskOutcome.ON_TIME, credits.on_time, ""
        return TaskOutcome.LATE, credits.late, ""
    if facts.deadline < cutoff:
        return TaskOutcome.OVERDUE, credits.overdue, ""
    if verification == VerificationPolicy.REQUIRED and facts.pending_verification_at_cutoff:
        return _na(NA.PENDING_VERIFICATION)
    return _na(NA.NOT_DUE)


def classify_gap(decision: str | None, *, before_joining=False, on_leave=False):
    """(outcome, credit, na_reason) of an expected-but-not-generated occurrence (D6)."""
    if before_joining:
        return _na(NA.BEFORE_JOINING)
    if on_leave:
        return _na(NA.APPROVED_LEAVE)
    if decision == GapDecision.SYSTEM_ISSUE_EXCLUDE:
        return _na(NA.GAP_SYSTEM_ISSUE)
    if decision == GapDecision.EMPLOYEE_RESPONSIBLE:
        return TaskOutcome.OVERDUE, ZERO, ""
    return _na(NA.GAP_UNDECIDED)


@dataclass(frozen=True)
class ComponentSpec:
    id: int
    position: int
    source_type: str
    responsibility_id: int | None
    template_id: int | None
    category_id: int | None
    department_id: int | None
    task_scope: str
    manual_match: str


def match_line(components, task: TaskFacts, includes=frozenset(), excludes=frozenset()):
    """[(component_id, match_reason)] of one KPI line for one task. Within a line a manual
    task counts at most once, at the most specific level: HR include override > the task's own
    responsibility > task type > category. An include override makes the task belong to the
    included responsibilities only; an exclude override removes that responsibility."""
    resp = sorted(
        (c for c in components if c.source_type == ComponentSource.RESPONSIBILITY_TASKS),
        key=lambda c: (c.position, c.id),
    )
    if task.source == TaskSource.SCHEDULED:
        return [(c.id, MatchReason.SCHEDULED) for c in resp
                if c.task_scope in SCHEDULED_SCOPES
                and c.responsibility_id == task.responsibility_id]
    manual = [c for c in resp
              if c.task_scope in MANUAL_SCOPES and c.responsibility_id not in excludes]
    if includes:
        return [(c.id, MatchReason.OVERRIDE) for c in manual if c.responsibility_id in includes]
    if task.responsibility_id is not None:
        hits = [c for c in manual if c.responsibility_id == task.responsibility_id]
        return [(hits[0].id, MatchReason.TASK_RESPONSIBILITY)] if hits else []
    if task.template_id is not None:
        hits = [c for c in manual if c.manual_match == ManualMatch.TASK_TYPE
                and c.template_id == task.template_id]
        if hits:
            return [(hits[0].id, MatchReason.TASK_TYPE)]
    hits = [c for c in manual if c.manual_match == ManualMatch.CATEGORY
            and c.category_id is not None and c.category_id == task.category_id
            and c.department_id == task.department_id]
    return [(hits[0].id, MatchReason.CATEGORY)] if hits else []


def component_achievement(rows) -> Fraction | None:
    """Credit sum / applicable count x 100; None when nothing is applicable."""
    applicable = [r for r in rows if r.applicable]
    if not applicable:
        return None
    return Fraction(sum((r.credit for r in applicable), ZERO)) * 100 / len(applicable)


def kpi_achievement(parts) -> tuple[Fraction | None, dict]:
    """parts: [(component_id, share, achievement-or-None)]. Shares are normalised over the
    applicable components; returns (achievement, {component_id: normalised share})."""
    applicable = [(cid, Fraction(share), ach) for cid, share, ach in parts if ach is not None]
    total = sum((share for _, share, _ in applicable), Fraction(0))
    if not applicable or total == 0:
        return None, {}
    shares = {cid: share / total for cid, share, _ in applicable}
    return sum(shares[cid] * ach for cid, _, ach in applicable), shares


def benchmark(achievement: Fraction, steps, below_min: Decimal) -> Decimal:
    """The score % of the highest step whose minimum is at most the achievement (exact
    comparison: 84.9999... never reaches the 85 step); below the lowest step, `below_min`."""
    for minimum, score in sorted(steps, key=lambda s: s[0], reverse=True):
        if achievement >= Fraction(minimum):
            return score
    return below_min


def kpi_points(weight: Decimal, score_pct: Decimal) -> Decimal:
    return (weight * score_pct / HUNDRED).quantize(SIX, ROUND_HALF_UP)


def band_for(total: Decimal, bands, ceiling=None):
    """The band whose lower bound (>=) the total reaches, capped by a band ceiling."""
    ordered = sorted(bands, key=lambda b: b.min_points, reverse=True)
    band = next((b for b in ordered if total >= b.min_points), None)
    if band is not None and ceiling is not None and band.min_points > ceiling.min_points:
        band = ceiling
    return band


def _q(value: Fraction | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Fraction):
        value = Decimal(value.numerator) / Decimal(value.denominator)
    return value.quantize(SIX, ROUND_HALF_UP)


# =============================================================================================
# Reading the facts (read-only queries)
# =============================================================================================


def completion_history(submissions, decisions, cutoff: datetime):
    """(accepted, verified, pending) at the cutoff from submission instants and verification
    decisions [(submitted_at, decision, decided_at)]. Only facts dated at or before the cutoff
    count (D4); a rejection reopens its submission (D1)."""
    decided = {s: d for s, d, at in decisions if at <= cutoff}
    standing = sorted(s for s in submissions
                      if s <= cutoff and decided.get(s) != VerificationDecision.REJECTED)
    verified = sorted(s for s, d in decided.items()
                      if d == VerificationDecision.VERIFIED and s <= cutoff)
    pending = any(s not in decided for s in standing)
    return (standing[-1] if standing else None), (verified[0] if verified else None), pending


def task_facts(task: Task, cutoff: datetime) -> TaskFacts:
    clock = authoritative_resolution_clock(task)
    deadline = None
    if clock is not None and clock.start_at is not None:
        # The SLA engine's applicable deadline as at the cutoff (HOLD rule included).
        deadline = sla.task_sla(task, cutoff)["resolution"]["due_at"]
    verifications = list(task.verifications.all())
    submissions = {v.submitted_at for v in verifications}
    if task.completed_at is not None:
        submissions.add(task.completed_at)
    accepted, verified, pending = completion_history(
        submissions, [(v.submitted_at, v.decision, v.decided_at) for v in verifications], cutoff
    )
    cancelled = (task.status == TaskStatus.CANCELLED and task.cancelled_at is not None
                 and task.cancelled_at <= cutoff)
    return TaskFacts(
        task_id=task.pk, reference=task.title[:200], source=task.source,
        responsibility_id=task.responsibility_id, template_id=task.template_id,
        category_id=task.category_id, department_id=task.department_id,
        occurrence_date=task.occurrence_date, has_clock=clock is not None, deadline=deadline,
        accepted_completion=accepted, verified_completion=verified,
        pending_verification_at_cutoff=pending, cancelled_before_cutoff=cancelled,
        verification_required=task.verification_required,
    )


def month_tasks(employee, start: datetime, end: datetime, cutoff: datetime):
    """[(task, facts)] of the employee's (current assignee) tasks belonging to the month: by
    the SLA engine's applicable deadline, or by assignment time when no deadline has started.
    The query only narrows candidates (a task on hold may carry an earlier stored deadline)."""
    due_in = TaskSla.objects.filter(task=OuterRef("pk"), kind=ClockKind.RESOLUTION,
                                    due_at__gte=start, due_at__lt=end)
    held_earlier = TaskSla.objects.filter(task=OuterRef("pk"), kind=ClockKind.RESOLUTION,
                                          due_at__lt=end, stopped_at__isnull=True)
    candidates = (
        Task.objects.filter(assigned_to=employee)
        .filter(Exists(due_in) | Q(assigned_at__gte=start, assigned_at__lt=end)
                | (Q(status=TaskStatus.BLOCKED) & Exists(held_earlier)))
        .select_related("template")
        .prefetch_related("sla_clocks", "verifications")
        .order_by("pk")
    )
    selected = []
    for task in candidates:
        facts = task_facts(task, cutoff)
        when = facts.deadline if facts.deadline is not None else task.assigned_at
        if not start <= when < end:
            continue
        if task.status == TaskStatus.BLOCKED and facts.deadline is not None:
            # On hold after its deadline had passed: the SLA engine's deadline as at the month
            # start already lies in an earlier month, where the task was counted. Never twice.
            at_start = sla.task_sla(task, start)["resolution"]["due_at"]
            if at_start < start:
                continue
        selected.append((task, facts))
    return selected


class ResponsibilityActivity:
    """Was a responsibility active at an instant? Read-only from the audit log (D8)."""

    def __init__(self, responsibility):
        self.currently_active = responsibility.is_active
        self.events = list(
            AuditLog.objects.filter(
                entity_type="responsibility", entity_id=str(responsibility.pk),
                action__in=["responsibility.activated", "responsibility.deactivated"],
            ).order_by("occurred_at", "id").values_list("occurred_at", "action")
        )

    def active_at(self, at: datetime) -> bool:
        if not self.events:
            return self.currently_active
        state = self.events[0][1] != "responsibility.activated"  # before the first change
        for occurred_at, action in self.events:
            if occurred_at > at:
                break
            state = action == "responsibility.activated"
        return state


def _leave_days(employee, period_start: date, period_end: date):
    rows = ApprovedLeave.objects.filter(
        employee=employee, cancelled_at__isnull=True,
        start_date__lte=period_end, end_date__gte=period_start,
    )
    spans = [(r.start_date, r.end_date) for r in rows]
    return lambda day: day is not None and any(a <= day <= b for a, b in spans)


def _ist_date(value: datetime | None) -> date | None:
    return to_ist(value).date() if value is not None else None


# =============================================================================================
# The calculation
# =============================================================================================


@dataclass
class ComponentOutcome:
    component: KPIComponent
    rows: list = field(default_factory=list)
    achievement: Fraction | None = None
    applicable: bool = False
    na_reason: str = ""
    manual: dict | None = None  # preserved HR manual entry fields


def _plan_lines(version):
    components = KPIComponent.objects.select_related("responsibility").order_by("position", "id")
    return list(
        KPIWeight.objects.filter(weight_version=version)
        .select_related("kpi", "scoring_rule")
        .prefetch_related("scoring_rule__steps", Prefetch("components", queryset=components))
        .order_by("position", "id")
    )


def _spec(component) -> ComponentSpec:
    resp = component.responsibility
    return ComponentSpec(
        id=component.pk, position=component.position, source_type=component.source_type,
        responsibility_id=component.responsibility_id,
        template_id=resp.template_id if resp else None,
        category_id=resp.category_id if resp else None,
        department_id=resp.department_id if resp else None,
        task_scope=component.task_scope, manual_match=component.manual_match,
    )


def _expected_gaps(employee, component, period_start, period_end):
    """MISSED / FAILED occurrences of the responsibility in the month whose owner on that date
    was the employee (SKIPPED = nobody responsible: never charged)."""
    occurrences = (
        ScheduleOccurrence.objects.filter(
            schedule__responsibility_id=component.responsibility_id,
            occurrence_date__gte=period_start, occurrence_date__lte=period_end,
            status__in=[OccurrenceStatus.MISSED, OccurrenceStatus.FAILED],
        ).order_by("occurrence_date", "id")
    )
    gaps = []
    for occurrence in occurrences:
        owner = current_owner_row(component.responsibility, occurrence.occurrence_date)
        if owner is not None and owner.employee_id == employee.pk:
            gaps.append(occurrence)
    return gaps


def evaluate(employee, version, period_start: date, period_end: date, cutoff: datetime,
             previous_manual: dict):
    """All component and KPI results of the month (no writes). previous_manual maps a
    MANUAL_ENTRY component id to the HR entry kept from an earlier calculation."""
    start, end = _period_window(period_start, period_end)
    credits = Credits(version.credit_on_time, version.credit_late, version.credit_overdue)
    joined = employee.date_of_joining
    on_leave = _leave_days(employee, period_start, period_end)
    tasks = month_tasks(employee, start, end, cutoff)
    overrides = defaultdict(lambda: (set(), set()))
    for row in ManualTaskOverride.objects.filter(task_id__in=[t.pk for t, _ in tasks]):
        overrides[row.task_id][0 if row.action == OverrideAction.INCLUDE else 1].add(
            row.responsibility_id
        )
    activity = {}

    def inactive(component, facts):
        resp = component.responsibility
        if resp.pk not in activity:
            activity[resp.pk] = ResponsibilityActivity(resp)
        return not activity[resp.pk].active_at(facts.deadline)

    gap_decisions = {}
    mismatches = []
    results = []
    for line in _plan_lines(version):
        components = list(line.components.all())
        specs = [_spec(c) for c in components]
        outcomes = {c.pk: ComponentOutcome(c) for c in components}
        for task, facts in tasks:
            includes, excludes = overrides.get(task.pk, (set(), set()))
            for component_id, reason in match_line(specs, facts, includes, excludes):
                component = outcomes[component_id].component
                if (component.verification_policy == VerificationPolicy.REQUIRED
                        and not task.verification_required):
                    mismatches.append(task.pk)  # D5: refused below, never scored
                    continue
                manual = facts.source == TaskSource.MANUAL
                day = facts.occurrence_date if not manual else _ist_date(facts.deadline)
                applicability = Applicability(
                    before_joining=bool(joined and day and day < joined),
                    on_leave=on_leave(day),
                    responsibility_inactive=bool(manual and facts.deadline
                                                 and inactive(component, facts)),
                )
                outcome, credit, na_reason = classify_task(
                    facts, verification=component.verification_policy, credits=credits,
                    cutoff=cutoff, applicability=applicability,
                )
                outcomes[component_id].rows.append(CreditRow(
                    outcome=outcome, credit=credit, na_reason=na_reason, task_id=task.pk,
                    reference=facts.reference, match_reason=reason, deadline_at=facts.deadline,
                    completed_at=accepted_for(facts, component.verification_policy),
                ))
        for component in components:
            result = outcomes[component.pk]
            if component.source_type == ComponentSource.MANUAL_ENTRY:
                kept = previous_manual.get(component.pk)
                result.manual = kept
                if kept and kept["applicable"] and kept["manual_achievement_pct"] is not None:
                    result.achievement = Fraction(kept["manual_achievement_pct"])
                    result.applicable = True
                else:
                    result.na_reason = kept["na_reason"] if kept else NA.AWAITING_HR_ENTRY
                continue
            if component.task_scope in SCHEDULED_SCOPES:
                for occurrence in _expected_gaps(employee, component, period_start, period_end):
                    if occurrence.pk not in gap_decisions:
                        decision = GenerationGapDecision.objects.filter(
                            occurrence=occurrence
                        ).values_list("decision", flat=True).first()
                        gap_decisions[occurrence.pk] = decision
                    day = occurrence.occurrence_date
                    outcome, credit, na_reason = classify_gap(
                        gap_decisions[occurrence.pk],
                        before_joining=bool(joined and day < joined), on_leave=on_leave(day),
                    )
                    result.rows.append(CreditRow(
                        outcome=outcome, credit=credit, na_reason=na_reason,
                        occurrence_id=occurrence.pk, match_reason=MatchReason.GAP,
                        reference=f"{component.responsibility.name} {day.isoformat()} "
                                  f"({occurrence.status})"[:200],
                    ))
            result.achievement = component_achievement(result.rows)
            result.applicable = result.achievement is not None
            if not result.applicable:
                result.na_reason = NA.NO_APPLICABLE_TASKS
        parts = [(c.pk, c.contribution_share, outcomes[c.pk].achievement) for c in components]
        achievement, shares = kpi_achievement(parts)
        score_pct = points = None
        if achievement is not None:
            rule = line.scoring_rule
            steps = [(s.min_achievement_pct, s.score_pct) for s in rule.steps.all()]
            score_pct = benchmark(achievement, steps, rule.below_min_score_pct)
            points = kpi_points(line.weight, score_pct)
        results.append({
            "line": line, "outcomes": [outcomes[c.pk] for c in components], "shares": shares,
            "achievement": achievement, "score_pct": score_pct, "points": points,
        })
    if mismatches:
        ids = ", ".join(str(pk) for pk in sorted(set(mismatches)))
        raise VerificationConfigurationError(
            "A verification-required component matched tasks that have no verification step "
            f"(task ids: {ids}). Fix the component or the tasks; the month was not calculated.",
        )
    return results


# --- persistence ------------------------------------------------------------------------------


def _referenced(kpi_scores=(), component_results=()) -> bool:
    return (
        ScoreAdjustment.objects.filter(kpi_score__in=kpi_scores).exists()
        or DeductionApplication.objects.filter(
            Q(kpi_score__in=kpi_scores) | Q(component_result__in=component_results)
        ).exists()
    )


def _clear_kra_breakdown(performance) -> None:
    scores = list(performance.kpi_scores.all())
    results = list(MonthlyComponentResult.objects.filter(kpi_score__in=scores))
    if _referenced(scores, results):
        raise PerformanceError(
            "Adjustments or deductions exist for this month; it cannot be rebuilt."
        )
    MonthlyTaskCredit.objects.filter(component_result__in=results).delete()
    MonthlyComponentResult.objects.filter(pk__in=[r.pk for r in results]).delete()
    performance.kpi_scores.all().delete()


def release_to_legacy(employee, year: int, month: int) -> None:
    """Before the legacy engine takes a month that was calculated with a KRA plan (the plan
    changed to a legacy one): remove the KRA breakdown of the non-finalized record."""
    with transaction.atomic():
        performance = (
            MonthlyPerformance.objects.select_for_update()
            .filter(employee=employee, year=year, month=month).first()
        )
        if performance is None or performance.calculation_model != CalculationModel.KRA_POINTS:
            return
        if performance.status == PerformanceStatus.FINALIZED:
            raise PerformanceError("Finalized performance cannot be changed.")
        _clear_kra_breakdown(performance)
        _reset_kra_fields(performance)
        performance.calculation_model = CalculationModel.LEGACY_WEIGHTED
        performance.version += 1
        performance.save()


def _reset_kra_fields(performance) -> None:
    for name in ("department", "band_scheme", "band", "band_ceiling", "max_points_applicable",
                 "auto_total", "adjustment_total", "deduction_total", "final_total",
                 "cutoff_at"):
        setattr(performance, name, None)
    performance.role_name = performance.band_name = performance.plan_source = ""


def _reset_legacy_fields(performance) -> None:
    for name in ("assigned_tasks", "scheduled_tasks", "manual_tasks", "completed_tasks",
                 "pending_tasks", "overdue_tasks", "sla_met_tasks", "sla_breached_tasks",
                 "on_time_completed_tasks", "completed_sla_tasks"):
        setattr(performance, name, 0)
    performance.completion_rate = performance.sla_compliance_rate = None
    performance.overall_score = None
    performance.performance_band = ""


def _previous_manual(performance) -> dict:
    """HR manual entries (Phase 7.3) survive recalculation: keyed by component id."""
    rows = MonthlyComponentResult.objects.filter(
        kpi_score__monthly_performance=performance, entered_by__isnull=False,
        component__source_type=ComponentSource.MANUAL_ENTRY,
    )
    return {
        r.component_id: {
            "applicable": r.applicable, "na_reason": r.na_reason,
            "manual_achievement_pct": r.manual_achievement_pct,
            "entered_by_id": r.entered_by_id, "entered_at": r.entered_at,
        }
        for r in rows
    }


def _save_results(performance, results) -> None:
    scores = {s.kpi_id: s for s in performance.kpi_scores.all()}
    wanted = {r["line"].kpi_id for r in results}
    stale = [s for kpi_id, s in scores.items() if kpi_id not in wanted]
    if stale:
        stale_results = list(MonthlyComponentResult.objects.filter(kpi_score__in=stale))
        if _referenced(stale, stale_results):
            raise PerformanceError(
                "Adjustments or deductions exist for KPIs no longer in the plan."
            )
        MonthlyTaskCredit.objects.filter(component_result__in=stale_results).delete()
        MonthlyComponentResult.objects.filter(pk__in=[r.pk for r in stale_results]).delete()
        MonthlyKPIScore.objects.filter(pk__in=[s.pk for s in stale]).delete()
    for item in results:
        line = item["line"]
        row = scores.get(line.kpi_id) or MonthlyKPIScore(
            monthly_performance=performance, kpi=line.kpi, source=ScoreSource.SYSTEM
        )
        applicable = item["points"] is not None
        row.weight = line.weight
        row.source = ScoreSource.SYSTEM
        row.score = None  # legacy column, never used by KRA records
        row.name_snapshot = line.label
        row.scoring_rule = line.scoring_rule
        row.not_applicable = not applicable
        row.na_reason = "" if applicable else NA.ALL_COMPONENTS_NA
        row.achievement_pct = _q(item["achievement"])
        row.benchmark_pct = item["score_pct"]
        row.auto_points = item["points"] if applicable else ZERO
        if applicable:
            row.final_points = row.auto_points + row.adjustment_points - row.deduction_points
        else:
            row.final_points = ZERO  # a whole-KPI N/A earns nothing; nothing is rescaled (P8)
        row.save()
        _save_components(row, item)


def _save_components(kpi_score, item) -> None:
    existing = {r.component_id: r for r in kpi_score.component_results.all()}
    wanted = {o.component.pk for o in item["outcomes"]}
    stale = [r for cid, r in existing.items() if cid not in wanted]
    if stale:
        if DeductionApplication.objects.filter(component_result__in=stale).exists():
            raise PerformanceError("Deductions exist for components no longer in the plan.")
        MonthlyTaskCredit.objects.filter(component_result__in=stale).delete()
        MonthlyComponentResult.objects.filter(pk__in=[r.pk for r in stale]).delete()
    for outcome in item["outcomes"]:
        component = outcome.component
        result = existing.get(component.pk) or MonthlyComponentResult(
            kpi_score=kpi_score, component=component
        )
        rows = outcome.rows
        result.label_snapshot = (component.label or (
            component.responsibility.name if component.responsibility else ""))[:120]
        result.share_snapshot = component.contribution_share
        result.normalized_share = _q(item["shares"].get(component.pk))
        result.applicable = outcome.applicable
        result.na_reason = "" if outcome.applicable else outcome.na_reason
        result.on_time_count = sum(r.outcome == TaskOutcome.ON_TIME for r in rows)
        result.late_count = sum(r.outcome == TaskOutcome.LATE for r in rows)
        result.overdue_count = sum(r.outcome == TaskOutcome.OVERDUE for r in rows)
        result.excluded_count = sum(r.outcome == TaskOutcome.NA for r in rows)
        result.credit_sum = (sum((r.credit for r in rows if r.applicable), ZERO)
                             if component.source_type != ComponentSource.MANUAL_ENTRY else None)
        result.achievement_pct = _q(outcome.achievement)
        if outcome.manual:  # keep HR's entry exactly as entered
            result.manual_achievement_pct = outcome.manual["manual_achievement_pct"]
            result.entered_by_id = outcome.manual["entered_by_id"]
            result.entered_at = outcome.manual["entered_at"]
        result.save()
        result.task_credits.all().delete()
        MonthlyTaskCredit.objects.bulk_create(
            MonthlyTaskCredit(
                component_result=result, task_id=r.task_id, occurrence_id=r.occurrence_id,
                task_reference=r.reference, match_reason=r.match_reason, outcome=r.outcome,
                na_reason=r.na_reason, credit=r.credit, deadline_at=r.deadline_at,
                completed_at=r.completed_at,
            )
            for r in rows
        )


def calculate_kra_month(*, actor, employee, year: int, month: int,
                        now=None) -> MonthlyPerformance:
    """Create or recalculate an employee's KRA month (CALCULATED). Provisional while the month
    is running (cutoff = now), final at month end (cutoff = month end). FINALIZED months are
    never touched; UNDER_REVIEW months are not recalculated (their policy is Phase 7.3, D13)."""
    now = now or timezone.now()
    period_start, period_end = month_bounds(year, month)
    start, end = _period_window(period_start, period_end)
    resolution = config_services.resolve_plan(employee, period_end)  # D9: last day of month
    if resolution.state == config_services.ResolutionState.AMBIGUOUS_ROLE:
        raise AmbiguousRole(resolution.reason)
    if resolution.state != config_services.ResolutionState.RESOLVED:
        raise NoKraPlan(resolution.reason)
    if now < start:
        raise MonthNotStarted()
    if employee.date_of_joining and employee.date_of_joining > period_end:
        raise NotEmployedInPeriod()
    version = resolution.version
    if version.calculation_model != CalculationModel.KRA_POINTS:
        raise PerformanceError("This plan is calculated by the legacy engine.")
    cutoff = min(now, end)

    with transaction.atomic():
        performance, created = MonthlyPerformance.objects.get_or_create(
            employee=employee, year=year, month=month,
            defaults={"period_start": period_start, "period_end": period_end,
                      "calculation_model": CalculationModel.KRA_POINTS},
        )
        performance = MonthlyPerformance.objects.select_for_update().get(pk=performance.pk)
        if performance.status == PerformanceStatus.FINALIZED:
            raise PerformanceError("Finalized performance cannot be changed.")
        if performance.status == PerformanceStatus.UNDER_REVIEW:
            raise UnderReviewNotRecalculated()
        previous_status = performance.status
        if performance.calculation_model != CalculationModel.KRA_POINTS:
            _clear_kra_breakdown(performance)  # legacy KPI rows of this non-final month
            _reset_legacy_fields(performance)
            performance.calculation_model = CalculationModel.KRA_POINTS
        # A different KRA version of a non-final month is reconciled KPI by KPI below.
        results = evaluate(employee, version, period_start, period_end, cutoff,
                           _previous_manual(performance))
        _save_results(performance, results)

        scores = list(performance.kpi_scores.all())
        applicable = [s for s in scores if not s.not_applicable]
        performance.weight_version = version
        performance.department_id = employee.department_id
        performance.role_name = (
            resolution.default.role.name if resolution.default is not None
            else ", ".join(resolution.roles)
        )[:40]
        performance.plan_source = resolution.source or ""
        performance.band_scheme = version.band_scheme
        performance.max_points_applicable = sum((s.weight for s in applicable), ZERO)
        performance.auto_total = sum((s.auto_points for s in applicable), ZERO)
        performance.adjustment_total = sum((s.adjustment_points for s in applicable), ZERO)
        performance.deduction_total = sum((s.deduction_points for s in applicable), ZERO)
        performance.final_total = sum((s.final_points for s in scores), ZERO)
        band = band_for(performance.final_total, list(version.band_scheme.bands.all()),
                        performance.band_ceiling)
        performance.band = band
        performance.band_name = band.name if band else ""
        performance.overall_score = None
        performance.performance_band = ""
        performance.cutoff_at = cutoff
        performance.status = PerformanceStatus.CALCULATED
        performance.calculated_at = now
        performance.calculated_by = actor
        if not created:
            performance.version += 1
        performance.save()
        record(
            action="performance.kra_calculated",
            entity_type="monthly_performance",
            entity_id=performance.pk,
            actor=actor,
            use_request_user=actor is not None,
            old={"status": previous_status} if not created else None,
            new={
                "status": performance.status,
                "plan_version_id": version.pk,
                "plan_source": performance.plan_source,
                "cutoff_at": cutoff.isoformat(),
                "provisional": cutoff < end,
                "max_points_applicable": str(performance.max_points_applicable),
                "final_total": str(performance.final_total),
                "band": performance.band_name,
                "kpis": {
                    s.kpi.code: {
                        "not_applicable": s.not_applicable,
                        "achievement_pct": str(s.achievement_pct)
                        if s.achievement_pct is not None else None,
                        "auto_points": str(s.auto_points),
                    }
                    for s in performance.kpi_scores.select_related("kpi")
                },
            },
            extra={"employee_id": employee.pk, "year": year, "month": month},
        )
    return performance


def finalization_blockers(performance) -> list[dict]:
    """What must be resolved before a KRA month may be finalized (enforced in Phase 7.3):
    undecided generation gaps (D6) and manual-entry components with neither an HR score nor an
    HR-marked N/A (D12). Nothing is ever turned into 0 to get past these."""
    blockers = [
        {"kind": "UNDECIDED_GAP", "occurrence_id": row.occurrence_id,
         "component": row.component_result.label_snapshot}
        for row in MonthlyTaskCredit.objects.filter(
            component_result__kpi_score__monthly_performance=performance,
            na_reason=NA.GAP_UNDECIDED,
        ).select_related("component_result").order_by("occurrence_id")
    ]
    blockers += [
        {"kind": "MANUAL_ENTRY_MISSING", "component_id": result.component_id,
         "component": result.label_snapshot}
        for result in MonthlyComponentResult.objects.filter(
            kpi_score__monthly_performance=performance,
            component__source_type=ComponentSource.MANUAL_ENTRY,
            entered_by__isnull=True,
        ).order_by("id")
    ]
    return blockers
