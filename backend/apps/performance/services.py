"""Performance calculation, review and finalization (Phase 7, Stage A).

This module CONSUMES existing Task / SLA data and never changes it. Approved rules:

Eligibility (P1)
- A task counts for its CURRENT assignee; CANCELLED tasks never count.
- A task with an SLA (a resolution clock with a deadline) counts in the month of that deadline;
  a task without one counts in the month it was assigned.
- Daily activities (SCHEDULED) and assigned tasks (MANUAL) both count, with separate subtotals.

Metrics (P2) - a rate whose denominator is zero is None ("not applicable"), never 0 or 100.
- completion_rate      = completed / eligible x 100
- sla_breached         = resolution outcome MISSED, or still open past its deadline at the end
                         of the month (or now, for the current month)
- sla_compliance_rate  = MET / (MET + breached) x 100
- Timeliness (KPI)     = on-time completed / completed tasks with an SLA x 100

KPIs (P3/P4) - Timeliness is SYSTEM; the others are MANAGER. Finalization is blocked until every
KPI of the record has a score (missing scores are never treated as 0 and weights are never
re-normalised). Overall = sum(score x weight) / sum(weight). When Timeliness is not applicable
(no completed SLA task that month) the manager may score it explicitly; that row is recorded
with source MANAGER and audited (approved Option B).

Configuration integrity - an active weight version contains every active KPI exactly once and
totals exactly 10.0; an employee assignment lies inside its version's effective period.

SLA clock - a task's month and SLA outcome come from ONE authoritative resolution clock, chosen
exactly as the SLA engine chooses it (sla.services.task_sla).

Finalized records are locked (P9). Calculation is on demand (P7); recalculation is allowed until
finalization. Who may do what is enforced by the API layer (Stage B).
"""

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.audit.services import record
from apps.core.errors import AppError, ConflictError, FieldValidationError
from apps.core.timeutils import ist_datetime
from apps.sla.models import ClockKind, Outcome, TaskSla
from apps.tasks.models import Task, TaskSource, TaskStatus

from .models import (
    KPI,
    CalculationModel,
    EmployeeKPIAssignment,
    KPIWeight,
    KPIWeightVersion,
    MonthlyKPIScore,
    MonthlyPerformance,
    PerformanceStatus,
    ScoreSource,
    WeightVersionStatus,
)

REQUIRED_WEIGHT_TOTAL = Decimal("10.00")
TIMELINESS = "TIMELINESS"
OPEN = (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED)
CENT = Decimal("0.01")
# (lower bound, band) - checked from the top; anything below 60 is Unsatisfactory.
BANDS = (
    (Decimal("90"), "Excellent"),
    (Decimal("80"), "Very Good"),
    (Decimal("70"), "Good"),
    (Decimal("60"), "Needs Improvement"),
)
UNSATISFACTORY = "Unsatisfactory"


class PerformanceError(AppError):
    status_code = 409
    code = "performance_conflict"
    message = "This performance action is not possible in the current state."


class PerformanceVersionConflict(ConflictError):
    code = "version_conflict"
    message = "This record was changed by someone else. Reload and try again."


class NoKPIAssignment(PerformanceError):
    code = "no_kpi_assignment"
    message = "The employee has no KPI configuration for this period."


class CalculationModelNotSupported(PerformanceError):
    """The applicable version is a KRA plan: the legacy engine never scores it (Phase 7.1).
    The KRA calculation engine arrives in Phase 7.2."""

    code = "calculation_model_not_supported"
    message = "This KPI plan uses KRA points; the legacy calculation does not score it."


# --- small helpers ----------------------------------------------------------------------------


def percent(numerator: int, denominator: int) -> Decimal | None:
    """numerator / denominator x 100, rounded to 2 places; None when nothing to measure."""
    if denominator == 0:
        return None
    return (Decimal(numerator) * 100 / Decimal(denominator)).quantize(CENT, ROUND_HALF_UP)


def performance_band(score: Decimal | None) -> str:
    if score is None:
        return ""
    for lower, band in BANDS:
        if score >= lower:
            return band
    return UNSATISFACTORY


def overall_score(scores) -> Decimal | None:
    """sum(score x weight) / sum(weight) over (score, weight) pairs; None if any score is
    missing or the weights total 0 (never renormalised, never treating missing as 0)."""
    pairs = list(scores)
    if not pairs or any(score is None for score, _ in pairs):
        return None
    total_weight = sum(weight for _, weight in pairs)
    if total_weight == 0:
        return None
    value = sum(score * weight for score, weight in pairs) / total_weight
    return value.quantize(CENT, ROUND_HALF_UP)


def month_bounds(year: int, month: int) -> tuple[date, date]:
    if not 1 <= month <= 12:
        raise FieldValidationError(fields={"month": ["Use a month from 1 to 12."]})
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _period_window(start: date, end: date) -> tuple[datetime, datetime]:
    """IST [start 00:00, day after end 00:00) as aware datetimes."""
    return ist_datetime(start, time(0, 0)), ist_datetime(end + timedelta(days=1), time(0, 0))


def _audit(action, performance, actor, *, old=None, new=None, extra=None):
    record(
        action=action,
        entity_type="monthly_performance",
        entity_id=performance.pk,
        actor=actor,
        use_request_user=actor is not None,
        old=old,
        new=new,
        extra={
            "employee_id": performance.employee_id,
            "year": performance.year,
            "month": performance.month,
            **(extra or {}),
        },
    )


# --- KPI master -------------------------------------------------------------------------------


def set_kpi_active(*, actor, kpi: KPI, is_active: bool) -> KPI:
    """Activate or deactivate a KPI. Deactivating a KPI used by an ACTIVE weight version is
    refused (KPI.save); no version is modified and no weight is renormalised."""
    with transaction.atomic():
        kpi = KPI.objects.select_for_update().get(pk=kpi.pk)
        if kpi.is_active == is_active:
            return kpi
        kpi.is_active = is_active
        kpi.save()  # raises FieldValidationError when in use by an active version
        record(
            action="performance.kpi_activation_changed",
            entity_type="kpi",
            entity_id=kpi.pk,
            actor=actor,
            old={"is_active": not is_active},
            new={"is_active": is_active},
            extra={"code": kpi.code},
        )
    return kpi


# --- KPI weight versions and assignments ------------------------------------------------------


def create_weight_version(
    *, actor, configuration: str, name: str, effective_from: date, weights: dict,
    effective_to: date | None = None,
) -> KPIWeightVersion:
    """A DRAFT version. `weights` maps KPI code -> weight (>= 0). Activate it separately."""
    configuration = (configuration or "").strip().upper()
    if not configuration:
        raise FieldValidationError(fields={"configuration": ["This field is required."]})
    if effective_to is not None and effective_to < effective_from:
        raise FieldValidationError(fields={"effective_to": ["Must be on or after the start."]})
    kpis = {k.code: k for k in KPI.objects.filter(code__in=weights)}
    unknown = sorted(set(weights) - set(kpis))
    if unknown:
        raise FieldValidationError(fields={"weights": [f"Unknown KPI: {', '.join(unknown)}."]})
    parsed = {}
    for code, value in weights.items():
        weight = Decimal(str(value))
        if weight < 0:
            raise FieldValidationError(
                fields={"weights": [f"{code}: a weight cannot be negative."]}
            )
        parsed[code] = weight.quantize(CENT)
    with transaction.atomic():
        latest = (
            KPIWeightVersion.objects.select_for_update()
            .filter(configuration=configuration)
            .order_by("-version")
            .first()
        )
        version = KPIWeightVersion.objects.create(
            configuration=configuration,
            version=(latest.version + 1) if latest else 1,
            name=(name or "").strip() or configuration,
            effective_from=effective_from,
            effective_to=effective_to,
            created_by=actor,
        )
        KPIWeight.objects.bulk_create(
            KPIWeight(weight_version=version, kpi=kpis[code], weight=weight)
            for code, weight in parsed.items()
        )
        record(
            action="performance.weight_version_created",
            entity_type="kpi_weight_version",
            entity_id=version.pk,
            actor=actor,
            new={"configuration": configuration, "version": version.version,
                 "weights": {code: str(w) for code, w in parsed.items()}},
        )
    return version


def _overlaps(qs, start: date, end: date | None):
    """Rows of qs (with effective_from / effective_to) overlapping [start, end]."""
    qs = qs.exclude(effective_to__lt=start)
    if end is not None:
        qs = qs.exclude(effective_from__gt=end)
    return qs


def activate_weight_version(*, actor, version: KPIWeightVersion) -> KPIWeightVersion:
    """DRAFT -> ACTIVE. The version must contain every active KPI exactly once (a weight may be
    0), the weights must total exactly 10.0 (P10), and the period must not overlap another
    ACTIVE version of the same configuration."""
    with transaction.atomic():
        version = KPIWeightVersion.objects.select_for_update().get(pk=version.pk)
        if version.status != WeightVersionStatus.DRAFT:
            raise PerformanceError("Only a draft version can be activated.")
        if version.calculation_model != CalculationModel.LEGACY_WEIGHTED:
            raise PerformanceError(
                "A KRA plan is activated through the KRA configuration (Admin approval)."
            )
        weights = list(version.weights.select_related("kpi"))
        present = {w.kpi.code for w in weights}  # unique per version (database constraint)
        required = set(KPI.objects.filter(is_active=True).values_list("code", flat=True))
        if present != required:
            problems = []
            if required - present:
                problems.append(f"missing: {', '.join(sorted(required - present))}")
            if present - required:
                problems.append(f"inactive KPI: {', '.join(sorted(present - required))}")
            detail = "; ".join(problems)
            raise FieldValidationError(
                fields={"weights": [f"A version needs every active KPI exactly once ({detail})."]}
            )
        total = sum(w.weight for w in weights)
        if total != REQUIRED_WEIGHT_TOTAL:
            raise FieldValidationError(
                fields={"weights": [f"Weights must total exactly 10.0 (they total {total})."]}
            )
        clash = _overlaps(
            KPIWeightVersion.objects.filter(
                configuration=version.configuration, status=WeightVersionStatus.ACTIVE
            ),
            version.effective_from,
            version.effective_to,
        )
        if clash.exists():
            raise PerformanceError(
                "Another active version of this configuration covers part of this period."
            )
        version.status = WeightVersionStatus.ACTIVE
        version.activated_by = actor
        version.activated_at = timezone.now()
        version.save(update_fields=["status", "activated_by", "activated_at"])
        record(
            action="performance.weight_version_activated",
            entity_type="kpi_weight_version",
            entity_id=version.pk,
            actor=actor,
            new={"configuration": version.configuration, "version": version.version,
                 "total": str(total)},
        )
    return version


def end_weight_version(*, actor, version: KPIWeightVersion, last_day: date) -> KPIWeightVersion:
    """Close an open-ended version so a successor can take over. Only the end date changes;
    the weights of an activated version never change, so finalized history stays exact."""
    with transaction.atomic():
        version = KPIWeightVersion.objects.select_for_update().get(pk=version.pk)
        if version.effective_to is not None:
            raise PerformanceError("This version already has an end date.")
        if last_day < version.effective_from:
            raise FieldValidationError(fields={"last_day": ["Cannot end before it starts."]})
        beyond = version.assignments.filter(
            Q(effective_to__isnull=True) | Q(effective_to__gt=last_day)
        )
        if beyond.exists():
            raise PerformanceError(
                "Employee assignments of this version run past that day; end them first."
            )
        version.effective_to = last_day
        version.save(update_fields=["effective_to"])
        record(
            action="performance.weight_version_ended",
            entity_type="kpi_weight_version",
            entity_id=version.pk,
            actor=actor,
            old={"effective_to": None},
            new={"effective_to": last_day.isoformat()},
        )
    return version


def assign_kpi_version(
    *, actor, employee, version: KPIWeightVersion, effective_from: date,
    effective_to: date | None = None,
) -> EmployeeKPIAssignment:
    """Assign an ACTIVE weight version to an employee for a period that lies inside the
    version's own effective period (open-ended only if the version is). Periods of one employee
    never overlap; earlier assignments are kept as history.

    The caller's `version` may be a stale instance: every check runs against the version as it
    is in the database now, re-read under a row lock inside this transaction."""
    with transaction.atomic():
        version = KPIWeightVersion.objects.select_for_update().get(pk=version.pk)
        if version.status != WeightVersionStatus.ACTIVE:
            raise PerformanceError("Only an active weight version can be assigned.")
        if effective_to is not None and effective_to < effective_from:
            raise FieldValidationError(
                fields={"effective_to": ["Must be on or after the start."]}
            )
        if effective_from < version.effective_from:
            raise FieldValidationError(
                fields={"effective_from": ["Cannot start before the weight version takes effect."]}
            )
        version_ends = version.effective_to
        if version_ends is not None and (effective_to is None or effective_to > version_ends):
            raise FieldValidationError(
                fields={"effective_to": ["Must end by the weight version's last day."]}
            )
        current = EmployeeKPIAssignment.objects.select_for_update().filter(employee=employee)
        if _overlaps(current, effective_from, effective_to).exists():
            raise PerformanceError("The employee already has a KPI assignment in this period.")
        assignment = EmployeeKPIAssignment.objects.create(
            employee=employee,
            weight_version=version,
            effective_from=effective_from,
            effective_to=effective_to,
            assigned_by=actor,
        )
        record(
            action="performance.kpi_assigned",
            entity_type="employee",
            entity_id=employee.pk,
            actor=actor,
            new={"weight_version_id": version.pk, "effective_from": effective_from.isoformat(),
                 "effective_to": effective_to.isoformat() if effective_to else None},
        )
    return assignment


def end_kpi_assignment(*, actor, assignment: EmployeeKPIAssignment, last_day: date):
    with transaction.atomic():
        assignment = EmployeeKPIAssignment.objects.select_for_update().get(pk=assignment.pk)
        if assignment.effective_to is not None:
            raise PerformanceError("This assignment has already ended.")
        if last_day < assignment.effective_from:
            raise FieldValidationError(fields={"last_day": ["Cannot end before it starts."]})
        assignment.effective_to = last_day
        assignment.save(update_fields=["effective_to"])
        record(
            action="performance.kpi_assignment_ended",
            entity_type="employee",
            entity_id=assignment.employee_id,
            actor=actor,
            old={"effective_to": None},
            new={"effective_to": last_day.isoformat()},
        )
    return assignment


def assignment_for(employee, period_end: date) -> EmployeeKPIAssignment | None:
    """The assignment in effect on the LAST day of the period (documented choice)."""
    return (
        EmployeeKPIAssignment.objects.select_related("weight_version")
        .filter(employee=employee, effective_from__lte=period_end)
        .exclude(effective_to__lt=period_end)
        .first()
    )


# --- eligibility and operational metrics ------------------------------------------------------


def authoritative_resolution_clock(task) -> TaskSla | None:
    """The task's resolution clock exactly as the SLA engine reads it (sla.services.task_sla):
    the latest RESOLUTION clock by (created_at, pk). Today the SLA module creates at most one
    per task (at creation; reassignment never resets it); this keeps both readers identical
    even for unusual data. Needs `sla_clocks` prefetched."""
    clocks = [c for c in task.sla_clocks.all() if c.kind == ClockKind.RESOLUTION]
    return max(clocks, key=lambda c: (c.created_at, c.pk)) if clocks else None


def eligible_tasks(employee, period_start: date, period_end: date) -> list:
    """The tasks that count for `employee` in the period (approved P1). Read-only.

    A task's month is decided by its authoritative resolution deadline when it has one, else
    by its assignment time. The query only narrows candidates; the decision uses the same
    clock the metrics use."""
    start, end = _period_window(period_start, period_end)
    deadline_in_period = TaskSla.objects.filter(
        task=OuterRef("pk"), kind=ClockKind.RESOLUTION, due_at__gte=start, due_at__lt=end
    )
    candidates = (
        Task.objects.filter(assigned_to=employee)
        .exclude(status=TaskStatus.CANCELLED)
        .filter(Exists(deadline_in_period) | Q(assigned_at__gte=start, assigned_at__lt=end))
        .prefetch_related("sla_clocks")
        .order_by("pk")
    )
    eligible = []
    for task in candidates:
        clock = authoritative_resolution_clock(task)
        when = clock.due_at if clock is not None and clock.due_at is not None else task.assigned_at
        if start <= when < end:
            eligible.append(task)
    return eligible


@dataclass
class Metrics:
    assigned_tasks: int = 0
    scheduled_tasks: int = 0
    manual_tasks: int = 0
    completed_tasks: int = 0
    pending_tasks: int = 0
    overdue_tasks: int = 0
    sla_met_tasks: int = 0
    sla_breached_tasks: int = 0
    on_time_completed_tasks: int = 0
    completed_sla_tasks: int = 0

    @property
    def completion_rate(self) -> Decimal | None:
        return percent(self.completed_tasks, self.assigned_tasks)

    @property
    def sla_compliance_rate(self) -> Decimal | None:
        return percent(self.sla_met_tasks, self.sla_met_tasks + self.sla_breached_tasks)

    @property
    def timeliness(self) -> Decimal | None:
        return percent(self.on_time_completed_tasks, self.completed_sla_tasks)


def calculate_operational_metrics(employee, period_start: date, period_end: date, now=None):
    """Counts from existing Task / SLA rows. `now` caps "past its deadline" for the current
    month; a finished month is judged at its end."""
    now = now or timezone.now()
    _, end = _period_window(period_start, period_end)
    cutoff = min(now, end)
    m = Metrics()
    for task in eligible_tasks(employee, period_start, period_end):
        m.assigned_tasks += 1
        if task.source == TaskSource.SCHEDULED:
            m.scheduled_tasks += 1
        else:
            m.manual_tasks += 1
        clock = authoritative_resolution_clock(task)
        if clock is not None and clock.due_at is None:
            clock = None  # an SLA that never started has no deadline to measure
        if task.status == TaskStatus.COMPLETED:
            m.completed_tasks += 1
            if clock is not None and clock.outcome in (Outcome.MET, Outcome.MISSED):
                m.completed_sla_tasks += 1
                if clock.outcome == Outcome.MET:
                    m.on_time_completed_tasks += 1
                    m.sla_met_tasks += 1
                else:
                    m.sla_breached_tasks += 1
        elif task.status in OPEN:
            m.pending_tasks += 1
            if clock is not None and clock.due_at < cutoff:
                m.overdue_tasks += 1
                m.sla_breached_tasks += 1
    return m


# --- monthly workflow -------------------------------------------------------------------------


def _lock(performance, version: int | None = None) -> MonthlyPerformance:
    locked = MonthlyPerformance.objects.select_for_update().get(pk=performance.pk)
    if version is not None and locked.version != version:
        raise PerformanceVersionConflict()
    if locked.status == PerformanceStatus.FINALIZED:
        raise PerformanceError("Finalized performance cannot be changed.")
    return locked


def _require_legacy(performance) -> None:
    """The legacy manager-score workflow never touches a KRA month (Phase 7.2)."""
    if performance.calculation_model != CalculationModel.LEGACY_WEIGHTED:
        raise CalculationModelNotSupported(
            "This month uses KRA points; it has its own review workflow."
        )


def _refresh_overall(performance: MonthlyPerformance) -> None:
    pairs = [(s.score, s.weight) for s in performance.kpi_scores.all()]
    performance.overall_score = overall_score(pairs)
    performance.performance_band = performance_band(performance.overall_score)


METRIC_FIELDS = (
    "assigned_tasks", "scheduled_tasks", "manual_tasks", "completed_tasks", "pending_tasks",
    "overdue_tasks", "sla_met_tasks", "sla_breached_tasks", "on_time_completed_tasks",
    "completed_sla_tasks",
)


def calculate_monthly_performance(*, actor, employee, year: int, month: int, now=None):
    """Create or recalculate an employee's month (approved P7: on demand; allowed until
    finalization). Manager scores already entered are kept; the SYSTEM KPI is recalculated."""
    now = now or timezone.now()
    period_start, period_end = month_bounds(year, month)
    assignment = assignment_for(employee, period_end)
    if assignment is None:
        raise NoKPIAssignment()
    version = assignment.weight_version
    if version.calculation_model != CalculationModel.LEGACY_WEIGHTED:
        raise CalculationModelNotSupported()
    metrics = calculate_operational_metrics(employee, period_start, period_end, now)
    weights = list(
        KPIWeight.objects.select_related("kpi").filter(weight_version=version, kpi__is_active=True)
    )
    with transaction.atomic():
        performance, created = MonthlyPerformance.objects.get_or_create(
            employee=employee, year=year, month=month,
            defaults={"period_start": period_start, "period_end": period_end},
        )
        performance = _lock(performance)
        if performance.calculation_model != CalculationModel.LEGACY_WEIGHTED:
            raise CalculationModelNotSupported()  # use calculate_month (it releases the month)
        previous_status = performance.status
        if performance.weight_version_id not in (None, version.pk):
            # The KPI configuration changed: rebuild this (non-finalized) month's KPI rows.
            performance.kpi_scores.all().delete()
        existing = {s.kpi_id: s for s in performance.kpi_scores.all()}
        for w in weights:
            row = existing.get(w.kpi_id)
            if row is None:
                row = MonthlyKPIScore(
                    monthly_performance=performance, kpi=w.kpi, source=w.kpi.score_source
                )
            row.weight = w.weight
            if w.kpi.code == TIMELINESS and w.kpi.score_source == ScoreSource.SYSTEM:
                if metrics.timeliness is not None:  # measurable: always the system's value
                    row.source = ScoreSource.SYSTEM
                    row.score = metrics.timeliness
                    row.manager_remark, row.reviewed_by, row.reviewed_at = "", None, None
                elif row.source != ScoreSource.MANAGER:  # not applicable, no manager score yet
                    row.score = None
            row.save()
        stale = set(existing) - {w.kpi_id for w in weights}
        performance.kpi_scores.filter(kpi_id__in=stale).delete()
        for field in METRIC_FIELDS:
            setattr(performance, field, getattr(metrics, field))
        performance.completion_rate = metrics.completion_rate
        performance.sla_compliance_rate = metrics.sla_compliance_rate
        performance.weight_version = version
        performance.status = PerformanceStatus.CALCULATED
        performance.calculated_at = now
        performance.calculated_by = actor
        _refresh_overall(performance)
        if not created:
            performance.version += 1
        performance.save()
        _audit(
            "performance.calculated", performance, actor,
            old={"status": previous_status} if not created else None,
            new={
                "status": performance.status,
                "weight_version_id": version.pk,
                **{f: getattr(metrics, f) for f in METRIC_FIELDS},
                "completion_rate": str(metrics.completion_rate),
                "sla_compliance_rate": str(metrics.sla_compliance_rate),
                "timeliness": str(metrics.timeliness),
            },
        )
    return performance


def update_kpi_score(
    *, actor, performance, version: int, kpi_code: str, score, remark: str = ""
) -> MonthlyPerformance:
    """A manager-entered score (0-100) for a MANAGER (or HYBRID) KPI. SYSTEM scores and the
    operational metrics are never edited here, with one approved exception (Option B): when
    Timeliness is not applicable this month (no completed SLA task), the manager may score it;
    the row is then recorded with source MANAGER and the override is audited."""
    try:
        value = Decimal(str(score)).quantize(CENT, ROUND_HALF_UP)
    except Exception:
        raise FieldValidationError(fields={"score": ["Enter a number."]}) from None
    if not Decimal("0") <= value <= Decimal("100"):
        raise FieldValidationError(fields={"score": ["Use a score from 0 to 100."]})
    with transaction.atomic():
        performance = _lock(performance, version)
        _require_legacy(performance)
        if performance.status not in (PerformanceStatus.CALCULATED, PerformanceStatus.UNDER_REVIEW):
            raise PerformanceError("Calculate the month before entering scores.")
        row = performance.kpi_scores.select_related("kpi").filter(kpi__code=kpi_code).first()
        if row is None:
            raise FieldValidationError(fields={"kpi": ["This KPI is not part of this month."]})
        override = False
        if row.source == ScoreSource.SYSTEM:
            not_applicable = row.kpi.code == TIMELINESS and performance.completed_sla_tasks == 0
            if not not_applicable:
                raise FieldValidationError(
                    fields={"kpi": ["This KPI is calculated by the system."]}
                )
            override = True
        old = {"score": str(row.score) if row.score is not None else None,
               "manager_remark": row.manager_remark, "source": row.source}
        row.score = value
        row.source = ScoreSource.MANAGER if override else row.source
        row.manager_remark = (remark or "").strip()
        row.reviewed_by = actor
        row.reviewed_at = timezone.now()
        row.save()
        _refresh_overall(performance)
        performance.version += 1
        performance.save()
        _audit(
            "performance.kpi_score_updated", performance, actor,
            old=old,
            new={"score": str(value), "manager_remark": row.manager_remark, "source": row.source},
            extra={"kpi": kpi_code, "system_not_applicable_override": override},
        )
    return performance


def submit_review(*, actor, performance, version: int, remark: str = "") -> MonthlyPerformance:
    """CALCULATED -> UNDER_REVIEW, with the manager's overall remark."""
    with transaction.atomic():
        performance = _lock(performance, version)
        _require_legacy(performance)
        if performance.status != PerformanceStatus.CALCULATED:
            raise PerformanceError("Only a calculated month can be submitted for review.")
        performance.status = PerformanceStatus.UNDER_REVIEW
        performance.manager_remark = (remark or "").strip()
        performance.reviewed_by = actor
        performance.reviewed_at = timezone.now()
        performance.version += 1
        performance.save()
        _audit(
            "performance.submitted", performance, actor,
            old={"status": PerformanceStatus.CALCULATED},
            new={"status": performance.status, "manager_remark": performance.manager_remark},
        )
    return performance


def finalize_performance(*, actor, performance, version: int) -> MonthlyPerformance:
    """UNDER_REVIEW -> FINALIZED. Every KPI must have a score (P4); the record is then locked,
    keeping the copied weights, scores, metrics, overall score and band."""
    with transaction.atomic():
        performance = _lock(performance, version)
        _require_legacy(performance)
        if performance.status != PerformanceStatus.UNDER_REVIEW:
            raise PerformanceError("Submit the review before finalizing.")
        missing = sorted(
            s.kpi.code for s in performance.kpi_scores.select_related("kpi") if s.score is None
        )
        if missing:
            raise FieldValidationError(
                "Every KPI needs a score before finalization.",
                fields={"kpi_scores": [f"Missing: {', '.join(missing)}."]},
            )
        _refresh_overall(performance)
        performance.status = PerformanceStatus.FINALIZED
        performance.finalized_by = actor
        performance.finalized_at = timezone.now()
        performance.version += 1
        performance.save()
        _audit(
            "performance.finalized", performance, actor,
            old={"status": PerformanceStatus.UNDER_REVIEW},
            new={
                "status": performance.status,
                "overall_score": str(performance.overall_score),
                "performance_band": performance.performance_band,
                "kpi_scores": {
                    s.kpi.code: {"score": str(s.score), "weight": str(s.weight)}
                    for s in performance.kpi_scores.select_related("kpi")
                },
            },
        )
    return performance


# --- Phase 7.2: one entry point for both engines ----------------------------------------------


def calculate_month(*, actor, employee, year: int, month: int, now=None) -> MonthlyPerformance:
    """Calculate a month with the engine of the plan that applies on its last day: a legacy
    weight assignment keeps the legacy engine exactly as before; otherwise the KRA engine
    (HR override or department + system role default). See apps.performance.kra_engine.

    Phase 7.3 (E14): around a KRA calculation, in the same transaction, HR's settled points are
    set aside so the engine computes the automatic score only, and the month's existing HR
    adjustments and deductions are then re-applied (apps.performance.settlement). A month
    without any keeps exactly the engine's values and audit."""
    from . import kra_engine, settlement  # imported here: both build on this module

    _, period_end = month_bounds(year, month)
    assignment = assignment_for(employee, period_end)
    if (assignment is not None
            and assignment.weight_version.calculation_model == CalculationModel.LEGACY_WEIGHTED):
        with transaction.atomic():  # releasing a KRA month and recalculating are one change
            kra_engine.release_to_legacy(employee, year, month)
            return calculate_monthly_performance(
                actor=actor, employee=employee, year=year, month=month, now=now
            )
    with transaction.atomic():  # the calculation and re-applying HR's review are one change
        settlement.prepare_recalculation(employee, year, month)
        performance = kra_engine.calculate_kra_month(
            actor=actor, employee=employee, year=year, month=month, now=now
        )
        settled = settlement.settle(performance)
        if settled.has_inputs:
            _audit("performance.kra_settled", performance, actor,
                   new=settlement.snapshot(settled), extra={"after": "calculation"})
    return performance
