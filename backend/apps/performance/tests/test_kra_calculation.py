"""Phase 7.2 KRA calculation engine, end to end through the existing task, verification and
recurring services. One test (or block) per locked decision D1-D9, D12, plus safety."""

from datetime import date
from decimal import Decimal
from io import StringIO

import pytest
import time_machine
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.utils import timezone

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.org.models import Department
from apps.performance import config_services as svc
from apps.performance import kra_engine, services
from apps.performance.models import (
    ApprovedLeave,
    Band,
    GenerationGapDecision,
    KPIWeightVersion,
    ManualTaskOverride,
    MonthlyComponentResult,
    MonthlyPerformance,
    MonthlyTaskCredit,
)
from apps.recurring import generator
from apps.recurring import services as recurring_services
from apps.recurring.models import (
    RecurringSchedule,
    Responsibility,
    ScheduleOccurrence,
)
from apps.sla.models import ClockKind, TaskSla
from apps.tasks import services as task_services
from apps.tasks.models import Task, TaskCategory, TaskTemplate

from .test_config_v1 import _activate_seeded_rules

pytestmark = pytest.mark.django_db
OCT_8 = (2026, 10, 8, 12, 0)  # configuration time: the plan starts on 1 Nov, in the future
CLOSE = (2026, 12, 2, 9, 0)  # November is over: cutoff = 1 Dec 00:00 IST


@pytest.fixture
def hr(make_user):
    return make_user(roles.HR)


def _ops():
    return Department.objects.get(code="OPS")


def _feed():
    return Responsibility.objects.get(code="FEED_UPLOAD")


def _category(responsibility, scope="MANUAL", verification="NOT_REQUIRED"):
    return {"source_type": "RESPONSIBILITY_TASKS", "responsibility": responsibility,
            "task_scope": scope, "manual_match": "CATEGORY" if scope != "SCHEDULED" else "",
            "verification_policy": verification}


def _ready_deductions(plan, hr):
    ceiling = Band.objects.get(scheme=plan.band_scheme, name="Needs Improvement")
    for priority, rule in enumerate(plan.deduction_rules.order_by("code"), start=1):
        changes = {"scope": "OVERALL", "stacking": "STACK", "uncapped": True,
                   "priority": priority}
        if rule.kind == "BAND_CEILING":
            changes["ceiling_band"] = ceiling
        svc.update_deduction_rule(actor=hr, rule=rule, **changes)
    svc.update_plan_version(actor=hr, version=plan, deduction_stacking_method="ADDITIVE")


def _activate(admin, hr, ist, accuracy) -> KPIWeightVersion:
    """OPERATIONS_KRA v1 from 1 Nov: Accuracy = `accuracy` component, every other KPI a manual
    entry; default for OPS + Employee."""
    with time_machine.travel(ist(*OCT_8), tick=False):
        _activate_seeded_rules(admin)
        plan = KPIWeightVersion.objects.get(configuration="OPERATIONS_KRA", version=1)
        for line in plan.weights.select_related("kpi").order_by("position"):
            if line.kpi.code == "ACCURACY":
                svc.create_component(actor=hr, line=line, **accuracy)
            else:
                svc.create_component(actor=hr, line=line, source_type="MANUAL_ENTRY",
                                     label=f"HR {line.kpi.code}")
        _ready_deductions(plan, hr)
        plan = svc.activate_plan_version(actor=admin, version=plan)
        svc.create_plan_default(actor=hr, department=_ops(), role=roles.EMPLOYEE,
                                configuration="OPERATIONS_KRA", effective_from=date(2026, 11, 1))
    return plan


def _calc(employee, ist, *now, month=11):
    return services.calculate_month(actor=None, employee=employee, year=2026, month=month,
                                    now=ist(*now))


def _credits(record, kpi="ACCURACY"):
    rows = MonthlyTaskCredit.objects.filter(
        component_result__kpi_score__monthly_performance=record,
        component_result__kpi_score__kpi__code=kpi,
    )
    return {r.task_reference: (r.outcome, r.credit, r.na_reason) for r in rows}


def _kpi(record, code="ACCURACY"):
    return record.kpi_scores.get(kpi__code=code)


ON_TIME = ("ON_TIME", Decimal("1.00"), "")
LATE = ("LATE", Decimal("0.25"), "")
OVERDUE = ("OVERDUE", Decimal("0.00"), "")


def _na(reason):
    return ("NA", None, reason)


# --- month close: credits, normalisation, points, band, snapshot (D3, D4, P8) -----------------


def test_month_close_scores_and_snapshots_the_month(admin_user, hr, ops, work, sla_24h, ist):
    _activate(admin_user, hr, ist, _category(_feed()))
    rahul, emp = ops["rahul"], ops["rahul_emp"]
    for day, title in ((2, "A"), (3, "B"), (4, "C"), (5, "D")):
        task = work.raise_task(emp, 2026, 11, day, 10, 0, title=title)
        work.complete(task, rahul, 2026, 11, day, 12, 0)  # due next day 10:00 -> on time
    late = work.raise_task(emp, 2026, 11, 9, 10, 0, title="E")
    work.complete(late, rahul, 2026, 11, 11, 10, 0)  # due 10 Nov 10:00 -> late
    work.raise_task(emp, 2026, 11, 12, 10, 0, title="F")  # never done -> overdue
    cancelled = work.raise_task(emp, 2026, 11, 13, 10, 0, title="G")
    work.cancel(cancelled, 2026, 11, 13, 11, 0)
    work.raise_task(emp, 2026, 11, 16, 10, 0, priority="MEDIUM", title="I")  # no SLA (D3)
    work.raise_task(emp, 2026, 11, 30, 20, 0, title="H")  # due 1 Dec: December's work

    record = _calc(emp, ist, *CLOSE)
    assert _credits(record) == {
        "A": ON_TIME, "B": ON_TIME, "C": ON_TIME, "D": ON_TIME, "E": LATE, "F": OVERDUE,
        "G": _na("CANCELLED_BEFORE_CUTOFF"), "I": _na("NO_DEADLINE"),
    }
    accuracy = _kpi(record)
    assert accuracy.achievement_pct == Decimal("70.833333")  # 4.25 / 6
    assert (accuracy.benchmark_pct, accuracy.auto_points) == (Decimal("60"), Decimal("1.8"))
    assert (accuracy.name_snapshot, accuracy.weight, accuracy.score) == (
        "Accuracy", Decimal("3.00"), None,
    )
    result = accuracy.component_results.get()
    assert (result.on_time_count, result.late_count, result.overdue_count,
            result.excluded_count) == (4, 1, 1, 2)
    assert set(MonthlyTaskCredit.objects.filter(component_result=result)
               .values_list("match_reason", flat=True)) == {"CATEGORY"}
    others = record.kpi_scores.exclude(kpi__code="ACCURACY")
    assert all(s.not_applicable and s.final_points == 0 for s in others)  # no rescaling (P8)
    assert (record.calculation_model, record.status, record.plan_source, record.role_name) == (
        "KRA_POINTS", "CALCULATED", "DEFAULT", roles.EMPLOYEE,
    )
    assert record.cutoff_at == ist(2026, 12, 1, 0, 0)
    assert (record.max_points_applicable, record.auto_total, record.final_total) == (
        Decimal("3.00"), Decimal("1.8"), Decimal("1.8"),
    )
    assert (record.band_name, record.overall_score, record.performance_band) == (
        "Performance Concern", None, "",
    )
    assert record.department == emp.department
    blockers = kra_engine.finalization_blockers(record)
    assert [b["kind"] for b in blockers] == ["MANUAL_ENTRY_MISSING"] * 5  # D12


def test_provisional_then_month_close(admin_user, hr, ops, work, sla_24h, ist):
    _activate(admin_user, hr, ist, _category(_feed()))
    rahul, emp = ops["rahul"], ops["rahul_emp"]
    done = work.raise_task(emp, 2026, 11, 2, 10, 0, title="A")
    work.complete(done, rahul, 2026, 11, 2, 12, 0)
    work.raise_task(emp, 2026, 11, 3, 10, 0, title="F")  # due 4 Nov 10:00

    early = _calc(emp, ist, 2026, 11, 3, 12, 0)
    assert _credits(early) == {"A": ON_TIME, "F": _na("NOT_DUE_BY_CUTOFF")}
    assert early.cutoff_at == ist(2026, 11, 3, 12, 0)  # provisional
    assert early.final_total == Decimal("3")  # 100% -> 100%
    closed = _calc(emp, ist, *CLOSE)
    assert closed.pk == early.pk and closed.version == early.version + 1
    assert _credits(closed) == {"A": ON_TIME, "F": OVERDUE}
    assert closed.final_total == Decimal("1.2")  # 50% -> 40%


# --- D4: the cutoff is authoritative -------------------------------------------------------------


def test_cutoff_completion_and_cancellation(admin_user, hr, ops, work, sla_24h, ist):
    _activate(admin_user, hr, ist, _category(_feed()))
    rahul, emp = ops["rahul"], ops["rahul_emp"]
    late = work.raise_task(emp, 2026, 11, 29, 10, 0, title="Completed in December")
    work.complete(late, rahul, 2026, 12, 1, 9, 0)
    after = work.raise_task(emp, 2026, 11, 27, 10, 0, title="Cancelled in December")
    work.cancel(after, 2026, 12, 1, 10, 0)
    before = work.raise_task(emp, 2026, 11, 26, 10, 0, title="Cancelled in November")
    work.cancel(before, 2026, 11, 26, 11, 0)

    expected = {"Completed in December": OVERDUE, "Cancelled in December": OVERDUE,
                "Cancelled in November": _na("CANCELLED_BEFORE_CUTOFF")}
    record = _calc(emp, ist, *CLOSE)
    assert _credits(record) == expected
    again = _calc(emp, ist, 2026, 12, 10, 9, 0)  # later recalculation: same month-end cutoff
    assert _credits(again) == expected and again.cutoff_at == ist(2026, 12, 1, 0, 0)


def test_on_hold_tasks_use_the_sla_engines_deadline(admin_user, hr, ops, work, sla_24h, ist):
    """Never N/A just for being on hold: the SLA engine's adjusted deadline decides."""
    _activate(admin_user, hr, ist, _category(_feed()))
    emp = ops["rahul_emp"]
    held_early = work.raise_task(emp, 2026, 11, 25, 10, 0, title="Held before its deadline")
    held_late = work.raise_task(emp, 2026, 11, 20, 10, 0, title="Held after its deadline")
    for task, day in ((held_early, 25), (held_late, 22)):
        with time_machine.travel(ist(2026, 11, day, 12, 0), tick=False):
            task.refresh_from_db()
            task_services.block_task(actor=ops["manager"], task=task, version=task.version,
                                     reason="Waiting for the client")
    mid = _calc(emp, ist, 2026, 11, 27, 12, 0)
    assert _credits(mid) == {"Held before its deadline": _na("NOT_DUE_BY_CUTOFF"),
                             "Held after its deadline": OVERDUE}
    closed = _calc(emp, ist, *CLOSE)  # the paused deadline has moved into December
    assert _credits(closed) == {"Held after its deadline": OVERDUE}
    december = _calc(emp, ist, 2027, 1, 2, 9, 0, month=12)
    assert _credits(december) == {}  # counted once (November); the other is still paused


# --- D1: a rejected verification reopens the task (NOT_REQUIRED component) ---------------------


def _raise_verifiable(new_task, manager, employee, ist, *at, title, **fields):
    with time_machine.travel(ist(*at), tick=False):
        return new_task(manager, employee, title=title, priority="HIGH", **fields)


def _act(fn, task, actor, ist, *at, **kwargs):
    with time_machine.travel(ist(*at), tick=False):
        task.refresh_from_db()
        return fn(actor=actor, task=task, version=task.version, **kwargs)


def _reject(task, manager, ist, *at):
    return _act(task_services.reject_verification, task, manager, ist, *at,
                reason="Wrong amount", remarks="Recheck the figures")


def test_rejected_verification_reopens_the_task(admin_user, hr, ops, work, sla_24h, ist,
                                                new_task):
    _activate(admin_user, hr, ist, _category(_feed()))
    rahul, emp, manager = ops["rahul"], ops["rahul_emp"], ops["manager"]
    never = _raise_verifiable(new_task, manager, emp, ist, 2026, 11, 2, 10, 0,
                              title="Rejected, not redone", verification_required=True)
    work.complete(never, rahul, 2026, 11, 2, 12, 0)
    _reject(never, manager, ist, 2026, 11, 2, 15, 0)
    redone = _raise_verifiable(new_task, manager, emp, ist, 2026, 11, 4, 10, 0,
                               title="Rejected, redone in time", verification_required=True)
    work.complete(redone, rahul, 2026, 11, 4, 11, 0)
    _reject(redone, manager, ist, 2026, 11, 4, 12, 0)
    _act(task_services.complete_task, redone, rahul, ist, 2026, 11, 4, 16, 0)
    rejected_late = _raise_verifiable(new_task, manager, emp, ist, 2026, 11, 6, 10, 0,
                                      title="Rejected after month end",
                                      verification_required=True)
    work.complete(rejected_late, rahul, 2026, 11, 6, 11, 0)
    _reject(rejected_late, manager, ist, 2026, 12, 1, 10, 0)

    record = _calc(emp, ist, *CLOSE)
    assert _credits(record) == {
        "Rejected, not redone": OVERDUE,
        "Rejected, redone in time": ON_TIME,
        "Rejected after month end": ON_TIME,  # the cutoff decides (D4)
    }


# --- D2: verification-required components time the accepted completion -----------------------


@pytest.fixture
def kyc(ist):
    """A task type WITH a verification step and its responsibility (test data)."""
    with time_machine.travel(ist(*OCT_8), tick=False):
        template = TaskTemplate.objects.create(code="KYC_REVIEW", name="KYC review",
                                               department=_ops(), verification_required=True)
        responsibility = Responsibility.objects.create(
            code="KYC_REVIEW", name="KYC review", department=_ops(),
            category=TaskCategory.objects.get(code="OPERATIONS"), template=template,
        )
    return template, responsibility


def _kyc_component(responsibility):
    return {"source_type": "RESPONSIBILITY_TASKS", "responsibility": responsibility,
            "task_scope": "MANUAL", "manual_match": "TASK_TYPE",
            "verification_policy": "REQUIRED"}


def test_verification_required_uses_the_accepted_completion(admin_user, hr, ops, work, sla_24h,
                                                            ist, new_task, kyc):
    template, responsibility = kyc
    _activate(admin_user, hr, ist, _kyc_component(responsibility))
    rahul, emp, manager = ops["rahul"], ops["rahul_emp"], ops["manager"]

    def raise_kyc(day, title):
        return _raise_verifiable(new_task, manager, emp, ist, 2026, 11, day, 10, 0, title=title,
                                 template=template)

    first = raise_kyc(2, "V1 verified")
    work.complete(first, rahul, 2026, 11, 2, 12, 0)
    _act(task_services.verify_task, first, manager, ist, 2026, 11, 2, 13, 0)
    rework_late = raise_kyc(4, "V2 rework after the deadline")  # due 5 Nov 10:00
    work.complete(rework_late, rahul, 2026, 11, 4, 12, 0)  # first submission on time
    _reject(rework_late, manager, ist, 2026, 11, 4, 13, 0)
    _act(task_services.complete_task, rework_late, rahul, ist, 2026, 11, 6, 10, 0)
    _act(task_services.verify_task, rework_late, manager, ist, 2026, 11, 6, 11, 0)
    pending = raise_kyc(9, "V3 never verified")  # due 10 Nov 10:00
    work.complete(pending, rahul, 2026, 11, 9, 12, 0)
    rework_ok = raise_kyc(16, "V4 rework in time")
    work.complete(rework_ok, rahul, 2026, 11, 16, 11, 0)
    _reject(rework_ok, manager, ist, 2026, 11, 16, 12, 0)
    _act(task_services.complete_task, rework_ok, rahul, ist, 2026, 11, 16, 14, 0)
    _act(task_services.verify_task, rework_ok, manager, ist, 2026, 11, 16, 15, 0)

    clock = TaskSla.objects.get(task=rework_late, kind=ClockKind.RESOLUTION)
    sla_before = (clock.outcome, clock.due_at, clock.stopped_at)
    provisional = _calc(emp, ist, 2026, 11, 9, 13, 0)
    assert _credits(provisional)["V3 never verified"] == _na("PENDING_VERIFICATION_NOT_DUE")
    record = _calc(emp, ist, *CLOSE)
    assert _credits(record) == {
        "V1 verified": ON_TIME,
        "V2 rework after the deadline": LATE,  # D2: the accepted completion, not the first
        "V3 never verified": OVERDUE,  # P7: pending verification after the deadline = 0
        "V4 rework in time": ON_TIME,
    }
    assert _kpi(record).final_points == Decimal("1.2")  # 2.25 / 4 = 56.25% -> 40%
    clock.refresh_from_db()
    assert (clock.outcome, clock.due_at, clock.stopped_at) == sla_before  # SLA untouched
    assert clock.outcome == "MET"  # the SLA engine stays authoritative for SLA itself


# --- D5: verification configuration mismatch is refused ----------------------------------------


def test_verification_mismatch_is_refused_at_activation(admin_user, hr, ist, kyc):
    _, responsibility = kyc
    with time_machine.travel(ist(*OCT_8), tick=False):
        _activate_seeded_rules(admin_user)
        plan = KPIWeightVersion.objects.get(configuration="OPERATIONS_KRA", version=1)
        line = plan.weights.get(kpi__code="TIMELINESS")
        svc.create_component(actor=hr, line=line, **{**_category(_feed()),
                                                    "verification_policy": "REQUIRED"})
        svc.create_component(actor=hr, line=line, **{**_kyc_component(responsibility),
                                                    "manual_match": "CATEGORY"})
        problems = "\n".join(svc.plan_activation_problems(plan))
    assert "Feed Upload: verification is required, but the responsibility's task type" in problems
    assert "KYC review: verification is required, so manual tasks cannot be matched" in problems


def test_verification_mismatch_found_at_calculation_refuses_the_month(
    admin_user, hr, ops, work, sla_24h, ist, kyc
):
    _, responsibility = kyc
    _activate(admin_user, hr, ist, _kyc_component(responsibility))
    plain = work.raise_task(ops["rahul_emp"], 2026, 11, 2, 10, 0, title="No verification step")
    ManualTaskOverride.objects.create(task=plain, responsibility=responsibility,
                                      action="INCLUDE", reason="Belongs to KYC", created_by=hr)
    with pytest.raises(kra_engine.VerificationConfigurationError) as excinfo:
        _calc(ops["rahul_emp"], ist, *CLOSE)
    assert str(plain.pk) in excinfo.value.message
    assert not MonthlyPerformance.objects.exists()  # nothing written, never scored


# --- D6: generation gaps -------------------------------------------------------------------------


def test_generation_gaps(admin_user, hr, ops, ist, own):
    _activate(admin_user, hr, ist, _category(_feed(), scope="SCHEDULED"))
    feed = _feed()
    own(feed, ops["rahul_emp"], start=date(2026, 11, 1), end=date(2026, 11, 20))
    own(feed, ops["amit_emp"], start=date(2026, 11, 21))
    schedule = RecurringSchedule.objects.get(responsibility=feed)

    def occurrence(day, status):
        return ScheduleOccurrence.objects.create(
            schedule=schedule, occurrence_date=date(2026, 11, day), status=status
        )

    for day, status, decision in ((2, "MISSED", "SYSTEM_ISSUE_EXCLUDE"),
                                  (3, "FAILED", "EMPLOYEE_RESPONSIBLE")):
        GenerationGapDecision.objects.create(occurrence=occurrence(day, status),
                                             decision=decision, reason="Reviewed",
                                             decided_by=hr)
    undecided = occurrence(4, "MISSED")
    occurrence(5, "SKIPPED")  # nobody responsible: never charged
    occurrence(6, "MISSED")  # on leave (occurrence date, D7)
    occurrence(23, "MISSED")  # Amit's date
    ApprovedLeave.objects.create(employee=ops["rahul_emp"], start_date=date(2026, 11, 6),
                                 end_date=date(2026, 11, 6), recorded_by=hr)

    record = _calc(ops["rahul_emp"], ist, *CLOSE)
    assert _credits(record) == {
        "Feed Upload 2026-11-02 (MISSED)": _na("GAP_SYSTEM_ISSUE"),
        "Feed Upload 2026-11-03 (FAILED)": OVERDUE,  # employee responsible: 0, counted
        "Feed Upload 2026-11-04 (MISSED)": _na("GAP_UNDECIDED"),  # never silently 0
        "Feed Upload 2026-11-06 (MISSED)": _na("APPROVED_LEAVE"),
    }
    accuracy = _kpi(record)
    assert not accuracy.not_applicable and accuracy.auto_points == 0  # 0 / 1
    gaps = [b for b in kra_engine.finalization_blockers(record) if b["kind"] == "UNDECIDED_GAP"]
    assert [b["occurrence_id"] for b in gaps] == [undecided.pk]
    amit = _calc(ops["amit_emp"], ist, *CLOSE)
    assert _credits(amit) == {"Feed Upload 2026-11-23 (MISSED)": _na("GAP_UNDECIDED")}
    assert _kpi(amit).not_applicable  # nothing applicable yet


# --- D7: approved leave ---------------------------------------------------------------------------


def test_leave_on_the_manual_deadline_date(admin_user, hr, ops, work, sla_24h, ist):
    _activate(admin_user, hr, ist, _category(_feed()))
    emp = ops["rahul_emp"]
    ApprovedLeave.objects.create(employee=emp, start_date=date(2026, 11, 10),
                                 end_date=date(2026, 11, 10), recorded_by=hr)
    ApprovedLeave.objects.create(employee=emp, start_date=date(2026, 11, 20),
                                 end_date=date(2026, 11, 20), recorded_by=hr,
                                 cancelled_by=hr, cancelled_at=timezone.now())
    work.raise_task(emp, 2026, 11, 9, 10, 0, title="Due on the leave day")  # due 10 Nov
    work.raise_task(emp, 2026, 11, 10, 10, 0, title="Raised on the leave day")  # due 11 Nov
    work.raise_task(emp, 2026, 11, 19, 10, 0, title="Due on cancelled leave")  # due 20 Nov
    record = _calc(emp, ist, *CLOSE)
    assert _credits(record) == {"Due on the leave day": _na("APPROVED_LEAVE"),
                                "Raised on the leave day": OVERDUE,
                                "Due on cancelled leave": OVERDUE}


def test_leave_on_the_scheduled_occurrence_date(admin_user, hr, ops, ist, own):
    _activate(admin_user, hr, ist, _category(_feed(), scope="SCHEDULED"))
    feed = _feed()
    own(feed, ops["rahul_emp"], start=date(2026, 11, 1))
    RecurringSchedule.objects.filter(responsibility=feed).update(effective_from=date(2026, 11, 2))
    ApprovedLeave.objects.create(employee=ops["rahul_emp"], start_date=date(2026, 11, 2),
                                 end_date=date(2026, 11, 2), recorded_by=hr)
    with time_machine.travel(ist(2026, 11, 2, 10, 0), tick=False):
        generator.generate_due_occurrences()
    task = Task.objects.get(responsibility=feed, assigned_to=ops["rahul_emp"])
    record = _calc(ops["rahul_emp"], ist, *CLOSE)
    row = MonthlyTaskCredit.objects.get(
        component_result__kpi_score__monthly_performance=record, task_id=task.pk
    )
    assert (row.match_reason, row.outcome, row.na_reason) == ("SCHEDULED", "NA",
                                                              "APPROVED_LEAVE")


# --- D8: responsibility deactivation (read-only from the audit log) --------------------------


def test_manual_tasks_of_a_deactivated_responsibility(admin_user, hr, ops, work, sla_24h, ist):
    _activate(admin_user, hr, ist, _category(_feed()))
    emp = ops["rahul_emp"]
    work.raise_task(emp, 2026, 11, 10, 10, 0, title="Before deactivation")
    for day, active in ((15, False), (20, True)):
        with time_machine.travel(ist(2026, 11, day, 12, 0), tick=False):
            feed = _feed()
            recurring_services.update_responsibility(actor=admin_user, responsibility=feed,
                                                      version=feed.version, is_active=active)
    work.raise_task(emp, 2026, 11, 16, 10, 0, title="While deactivated")
    work.raise_task(emp, 2026, 11, 23, 10, 0, title="After reactivation")
    before = Responsibility.objects.values_list("pk", "is_active", "version")
    snapshot = list(before)
    record = _calc(emp, ist, *CLOSE)
    assert _credits(record) == {"Before deactivation": OVERDUE,
                                "While deactivated": _na("RESPONSIBILITY_DEACTIVATED"),
                                "After reactivation": OVERDUE}
    assert list(Responsibility.objects.values_list("pk", "is_active", "version")) == snapshot


# --- D9: the plan in effect on the month's last day ----------------------------------------


def test_plan_on_the_last_day_and_override_precedence(admin_user, hr, ops, ist):
    v1 = _activate(admin_user, hr, ist, _category(_feed()))
    provisional = _calc(ops["rahul_emp"], ist, 2026, 11, 10, 9, 0)  # on v1, not final
    assert provisional.weight_version == v1
    with time_machine.travel(ist(*OCT_8), tick=False):
        v2 = svc.clone_plan_version(actor=hr, version=v1)
        svc.retire_plan_version(actor=admin_user, version=v1, last_day=date(2026, 11, 19),
                                reason="Changes from 20 Nov")
        svc.update_plan_version(actor=hr, version=v2, effective_from=date(2026, 11, 20))
        v2 = svc.activate_plan_version(actor=admin_user, version=v2)
        svc.create_override(actor=hr, employee=ops["amit_emp"], version=v2,
                            effective_from=date(2026, 11, 20), reason="Pilot group")
    rahul = _calc(ops["rahul_emp"], ist, *CLOSE)  # the whole month follows its last day (D9)
    assert (rahul.pk, rahul.weight_version, rahul.plan_source) == (provisional.pk, v2, "DEFAULT")
    amit = _calc(ops["amit_emp"], ist, *CLOSE)
    assert (amit.weight_version, amit.plan_source, amit.role_name) == (
        v2, "OVERRIDE", roles.EMPLOYEE,
    )


def test_no_plan_and_ambiguous_role_write_nothing(admin_user, hr, ops, staff, ist):
    _activate(admin_user, hr, ist, _category(_feed()))
    svc.create_plan_default(actor=hr, department=_ops(), role=roles.HR,
                            configuration="OPERATIONS_KRA", effective_from=date(2026, 11, 1))
    with pytest.raises(kra_engine.NoKraPlan):
        _calc(ops["manager_emp"], ist, *CLOSE)  # no Operations Manager default
    user, both = staff(roles.EMPLOYEE)
    user.groups.add(Group.objects.get(name=roles.HR))
    with pytest.raises(kra_engine.AmbiguousRole):
        _calc(both, ist, *CLOSE)
    assert not MonthlyPerformance.objects.exists()


# --- D12: manual-entry components ---------------------------------------------------------------


def test_manual_entries_are_na_until_hr_acts_and_survive_recalculation(admin_user, hr, ops, ist):
    _activate(admin_user, hr, ist, _category(_feed()))
    record = _calc(ops["rahul_emp"], ist, *CLOSE)
    assert record.max_points_applicable == 0 and record.final_total == 0  # nothing is 0-filled
    reasons = set(MonthlyComponentResult.objects.filter(
        kpi_score__monthly_performance=record, component__source_type="MANUAL_ENTRY"
    ).values_list("na_reason", flat=True))
    assert reasons == {"AWAITING_HR_ENTRY"}

    def manual(code):
        return MonthlyComponentResult.objects.filter(
            kpi_score__monthly_performance=record, kpi_score__kpi__code=code
        )

    # what the Phase 7.3 HR actions will record (simulated here)
    manual("CLIENT_SERVICING").update(manual_achievement_pct=Decimal("90"), applicable=True,
                                      entered_by=hr, entered_at=timezone.now())
    manual("FINANCIAL_ACCURACY").update(applicable=False, na_reason="HR_MARKED_NA",
                                        entered_by=hr, entered_at=timezone.now())
    again = _calc(ops["rahul_emp"], ist, *CLOSE)
    client = _kpi(again, "CLIENT_SERVICING")
    assert (client.not_applicable, client.benchmark_pct, client.final_points) == (
        False, Decimal("80"), Decimal("1.2"),
    )
    assert _kpi(again, "FINANCIAL_ACCURACY").not_applicable
    assert manual("CLIENT_SERVICING").get().manual_achievement_pct == Decimal("90")
    assert manual("FINANCIAL_ACCURACY").get().na_reason == "HR_MARKED_NA"
    assert (again.max_points_applicable, again.final_total) == (Decimal("1.50"),
                                                                Decimal("1.2"))
    missing = kra_engine.finalization_blockers(again)
    assert sorted(b["component"] for b in missing) == [
        "HR COMPLIANCE", "HR DATA_SYSTEM", "HR TIMELINESS",
    ]


# --- joining date, month state and legacy safety --------------------------------------------------


def test_joining_date(admin_user, hr, ops, work, sla_24h, ist):
    _activate(admin_user, hr, ist, _category(_feed()))
    amit = ops["amit_emp"]
    work.raise_task(amit, 2026, 11, 2, 10, 0, title="Before joining")
    work.raise_task(amit, 2026, 11, 12, 10, 0, title="After joining")
    type(amit).objects.filter(pk=amit.pk).update(date_of_joining=date(2026, 11, 10))
    amit.refresh_from_db()
    record = _calc(amit, ist, *CLOSE)
    assert _credits(record) == {"Before joining": _na("BEFORE_JOINING"),
                                "After joining": OVERDUE}
    type(amit).objects.filter(pk=amit.pk).update(date_of_joining=date(2026, 12, 5))
    amit.refresh_from_db()
    with pytest.raises(kra_engine.NotEmployedInPeriod):
        _calc(amit, ist, *CLOSE)


def test_month_states_and_legacy_guards(admin_client, admin_user, hr, ops, ist):
    _activate(admin_user, hr, ist, _category(_feed()))
    emp = ops["rahul_emp"]
    with pytest.raises(kra_engine.MonthNotStarted):
        _calc(emp, ist, 2026, 11, 15, 9, 0, month=12)
    record = _calc(emp, ist, *CLOSE)
    with pytest.raises(services.CalculationModelNotSupported):  # legacy workflow refuses
        services.submit_review(actor=hr, performance=record, version=record.version)
    assert admin_client.get("/api/v1/performance/reports/").json()["count"] == 0
    MonthlyPerformance.objects.filter(pk=record.pk).update(status="UNDER_REVIEW")
    with pytest.raises(kra_engine.UnderReviewNotRecalculated):  # D13 deferred
        _calc(emp, ist, *CLOSE)
    MonthlyPerformance.objects.filter(pk=record.pk).update(status="FINALIZED")
    with pytest.raises(services.PerformanceError):
        _calc(emp, ist, *CLOSE)


def test_a_legacy_assignment_keeps_the_legacy_engine(admin_user, hr, ops, ist):
    _activate(admin_user, hr, ist, _category(_feed()))
    emp = ops["rahul_emp"]
    kra = _calc(emp, ist, *CLOSE)
    assert kra.calculation_model == "KRA_POINTS"
    services.assign_kpi_version(
        actor=admin_user, employee=emp, effective_from=date(2026, 11, 1),
        version=KPIWeightVersion.objects.get(configuration="OPERATIONS", version=1),
    )
    legacy = _calc(emp, ist, *CLOSE)
    assert legacy.pk == kra.pk and legacy.calculation_model == "LEGACY_WEIGHTED"
    assert not MonthlyComponentResult.objects.filter(
        kpi_score__monthly_performance=legacy
    ).exists()
    assert (legacy.cutoff_at, legacy.final_total, legacy.plan_source) == (None, None, "")


def test_calculation_changes_nothing_outside_performance(admin_user, hr, ops, work, sla_24h,
                                                         ist):
    _activate(admin_user, hr, ist, _category(_feed()))
    rahul, emp = ops["rahul"], ops["rahul_emp"]
    done = work.raise_task(emp, 2026, 11, 2, 10, 0, title="A")
    work.complete(done, rahul, 2026, 11, 2, 12, 0)
    work.raise_task(emp, 2026, 11, 3, 10, 0, title="B")

    def state():
        return (
            list(Task.objects.order_by("pk").values_list("pk", "status", "completed_at",
                                                          "version")),
            list(TaskSla.objects.order_by("pk").values_list("pk", "start_at", "due_at",
                                                             "stopped_at", "outcome", "state")),
            ScheduleOccurrence.objects.count(),
            list(Responsibility.objects.order_by("pk").values_list("pk", "is_active",
                                                                    "version")),
        )

    before, last_audit = state(), AuditLog.objects.order_by("-id").first().pk
    _calc(emp, ist, *CLOSE)
    assert state() == before
    assert set(AuditLog.objects.filter(pk__gt=last_audit).values_list("action", flat=True)) == {
        "performance.kra_calculated",
    }


def test_management_command_calculates_kra_months(admin_user, hr, ops, ist):
    _activate(admin_user, hr, ist, _category(_feed()))
    out = StringIO()
    with time_machine.travel(ist(*CLOSE), tick=False):
        call_command("calculate_performance", "--year", "2026", "--month", "11", stdout=out)
    text = out.getvalue()
    assert "Performance 2026-11: 2 calculated." in text  # Rahul and Amit (Employee default)
    assert "Skipped (no KPI assignment)" in text and ops["manager_emp"].full_name in text
    assert set(MonthlyPerformance.objects.values_list("calculation_model", flat=True)) == {
        "KRA_POINTS",
    }
    assert not MonthlyPerformance.objects.exclude(calculated_by=None).exists()  # system run
