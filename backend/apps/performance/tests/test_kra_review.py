"""Phase 7.3 KRA review, end to end through the services: HR inputs, adjustments, deductions,
settlement, the workflow (submit, return, finalize, reopen), annual figures, the scheduled runs
and what an employee may see. One block per approved decision (E1-E21)."""

import json
from datetime import date
from decimal import Decimal

import pytest
import time_machine
from celery.schedules import crontab
from django.conf import settings
from django.utils import timezone

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.core.errors import FieldValidationError
from apps.performance import config_services as svc
from apps.performance import kra_engine, scheduling, services
from apps.performance import review_services as review
from apps.performance.annual import annual_summary
from apps.performance.models import (
    AppendOnly,
    ApprovedLeave,
    Band,
    DeductionApplication,
    DeductionRule,
    KPIWeightVersion,
    ManualTaskOverride,
    MonthlyComponentResult,
    MonthlyPerformance,
    MonthlyTaskCredit,
    PerformanceLocked,
    ScoreAdjustment,
)
from apps.performance.tasks import daily_kra_calculation, kra_month_close
from apps.recurring.models import RecurringSchedule, ScheduleOccurrence

from .test_config_v1 import _activate_seeded_rules
from .test_kra_calculation import CLOSE, OCT_8, _category, _feed, _kpi, _ops

pytestmark = pytest.mark.django_db
D = Decimal

# Every seeded deduction rule, completed by HR before activation (the KRA gives no maths).
RULES = {
    "MISSED_RECONCILIATION": {"scope": "COMPONENT", "stacking": "STACK", "cap_pct": D("50"),
                              "priority": 1},
    "DELAYED_SYSTEM_UPDATE": {"scope": "KPI", "stacking": "STACK", "uncapped": True,
                              "priority": 2},
    "SERVICE_DELAY_BEYOND_TAT": {"scope": "KPI", "stacking": "NON_STACKING", "cap_pct": D("25"),
                                 "priority": 3},
    "DATA_INCONSISTENCY": {"scope": "OVERALL", "stacking": "STACK", "uncapped": True,
                           "priority": 4},
    "TRANSACTION_ERROR": {"scope": "OVERALL", "stacking": "STACK", "uncapped": True,
                          "priority": 5, "ceiling": "Needs Improvement"},
    "REVENUE_LEAKAGE": {"scope": "OVERALL", "stacking": "STACK", "uncapped": True,
                        "priority": 6, "ceiling": "Performance Concern"},
}
MANUAL = {"TIMELINESS": D("100"), "CLIENT_SERVICING": D("90"), "DATA_SYSTEM": D("75"),
          "COMPLIANCE": D("100")}  # -> 2.0, 1.2, 0.6, 1.0 points; FINANCIAL_ACCURACY: N/A


@pytest.fixture
def hr(make_user):
    return make_user(roles.HR)


def _activate_plan(admin, hr, ist, accuracy, *, rules=None, method="ADDITIVE"):
    with time_machine.travel(ist(*OCT_8), tick=False):
        _activate_seeded_rules(admin)
        plan = KPIWeightVersion.objects.get(configuration="OPERATIONS_KRA", version=1)
        for line in plan.weights.select_related("kpi").order_by("position"):
            if line.kpi.code == "ACCURACY":
                svc.create_component(actor=hr, line=line, **accuracy)
            else:
                svc.create_component(actor=hr, line=line, source_type="MANUAL_ENTRY",
                                     label=f"HR {line.kpi.code}")
        for rule in plan.deduction_rules.all():
            changes = dict((rules or RULES)[rule.code])
            ceiling = changes.pop("ceiling", None)
            if ceiling:
                changes["ceiling_band"] = Band.objects.get(scheme=plan.band_scheme, name=ceiling)
            svc.update_deduction_rule(actor=hr, rule=rule, **changes)
        svc.update_plan_version(actor=hr, version=plan, deduction_stacking_method=method)
        plan = svc.activate_plan_version(actor=admin, version=plan)
        svc.create_plan_default(actor=hr, department=_ops(), role=roles.EMPLOYEE,
                                configuration="OPERATIONS_KRA", effective_from=date(2026, 11, 1))
    return plan


def _calc(employee, ist, *now, month=11):
    return services.calculate_month(actor=None, employee=employee, year=2026, month=month,
                                    now=ist(*now))


def _fresh(record):
    return MonthlyPerformance.objects.get(pk=record.pk)


def _component(record, code):
    return MonthlyComponentResult.objects.get(kpi_score__monthly_performance=record,
                                              kpi_score__kpi__code=code)


def _kpi_id(record, code):
    return _kpi(record, code).kpi_id


def _fill_manual(record, hr):
    """HR scores the four manual KPIs and marks FINANCIAL_ACCURACY not applicable."""
    for code, value in MANUAL.items():
        record = review.enter_manual_score(actor=hr, performance=record, version=record.version,
                                           component_id=_component(record, code).component_id,
                                           achievement_pct=value, reason="Monthly HR review")
    return review.mark_manual_na(
        actor=hr, performance=record, version=record.version,
        component_id=_component(record, "FINANCIAL_ACCURACY").component_id,
        reason="No financial work this month",
    )


@pytest.fixture
def month(admin_user, hr, ops, work, sla_24h, ist):
    """A closed November: Accuracy 3.0 (two tasks on time) + manual KPIs 4.8 = 7.8 of 8.5."""
    plan = _activate_plan(admin_user, hr, ist, _category(_feed()))
    rahul, emp = ops["rahul"], ops["rahul_emp"]
    for day, title in ((2, "A"), (3, "B")):
        task = work.raise_task(emp, 2026, 11, day, 10, 0, title=title)
        work.complete(task, rahul, 2026, 11, day, 12, 0)
    record = _fill_manual(_calc(emp, ist, *CLOSE), hr)
    return plan, emp, record


def _submitted(record, hr):
    return review.submit(actor=hr, performance=record, version=_fresh(record).version)


def _rule(plan, code):
    return DeductionRule.objects.get(plan_version=plan, code=code)


def _deduct(record, hr, plan, code, percent=None, **target):
    return review.apply_deduction(actor=hr, performance=record, version=_fresh(record).version,
                                  rule_id=_rule(plan, code).pk, percent=percent,
                                  reason=f"{code} found in review", evidence="Ticket 42",
                                  **target)


def _totals(record):
    record = _fresh(record)
    return (record.final_total, record.band_name)


# --- E12 / D12: manual entries and HR-marked N/A ------------------------------------------------


def test_manual_entries_take_effect_at_once_and_match_a_recalculation(month, ist):
    _, emp, record = month
    record = _fresh(record)
    assert record.status == "CALCULATED"
    assert (record.max_points_applicable, record.auto_total, record.final_total) == (
        D("8.50"), D("7.8"), D("7.8"),
    )
    assert record.band_name == "Consistent Performer"
    assert kra_engine.finalization_blockers(record) == []
    client = _kpi(record, "CLIENT_SERVICING")
    assert (client.benchmark_pct, client.auto_points, client.final_points) == (
        D("80"), D("1.2"), D("1.2"),
    )
    na = _component(record, "FINANCIAL_ACCURACY")
    assert (na.applicable, na.na_reason) == (False, "HR_MARKED_NA")
    stored = [(s.kpi_id, s.auto_points, s.final_points, s.not_applicable)
              for s in record.kpi_scores.order_by("kpi_id")]
    again = _calc(emp, ist, 2026, 12, 9, 9, 0)  # the engine reaches the same values
    assert [(s.kpi_id, s.auto_points, s.final_points, s.not_applicable)
            for s in again.kpi_scores.order_by("kpi_id")] == stored
    assert AuditLog.objects.filter(action="performance.kra_manual_entry").count() == 5


def test_manual_entry_validation(month, hr):
    _, _, record = month
    record = _fresh(record)
    accuracy = _component(record, "ACCURACY").component_id
    with pytest.raises(FieldValidationError):  # a task component is not a manual entry
        review.enter_manual_score(actor=hr, performance=record, version=record.version,
                                  component_id=accuracy, achievement_pct=D("50"),
                                  reason="HR review")
    with pytest.raises(FieldValidationError):
        review.enter_manual_score(actor=hr, performance=record, version=record.version,
                                  component_id=_component(record, "COMPLIANCE").component_id,
                                  achievement_pct=D("100.5"), reason="HR review")
    with pytest.raises(FieldValidationError):  # N/A needs a reason (D12)
        review.mark_manual_na(actor=hr, performance=record, version=record.version,
                              component_id=_component(record, "COMPLIANCE").component_id,
                              reason=" ")
    with pytest.raises(services.PerformanceVersionConflict):
        review.enter_manual_score(actor=hr, performance=record, version=record.version - 1,
                                  component_id=_component(record, "COMPLIANCE").component_id,
                                  achievement_pct=D("50"), reason="HR review")


# --- E13 / E16: submit and finalize -----------------------------------------------------------


def test_submit_needs_a_closed_month_and_finalize_needs_no_blockers(admin_user, hr, ops, ist):
    _activate_plan(admin_user, hr, ist, _category(_feed()))
    emp = ops["rahul_emp"]
    provisional = _calc(emp, ist, 2026, 11, 20, 9, 0)
    with pytest.raises(review.KraStateError):  # E13: still provisional
        review.submit(actor=hr, performance=provisional, version=provisional.version)
    record = _calc(emp, ist, *CLOSE)
    record = review.submit(actor=hr, performance=record, version=record.version)
    assert (record.status, record.reviewed_by) == ("UNDER_REVIEW", hr)
    with pytest.raises(kra_engine.UnderReviewNotRecalculated):
        _calc(emp, ist, *CLOSE)
    with pytest.raises(review.FinalizationBlocked) as blocked:  # D12: manual entries missing
        review.finalize(actor=hr, performance=record, version=record.version)
    assert len(blocked.value.fields["blockers"]) == 5
    record = _fill_manual(record, hr)  # E12: allowed while under review
    record = review.finalize(actor=hr, performance=record, version=record.version)
    assert (record.status, record.finalized_by) == ("FINALIZED", hr)
    entry = AuditLog.objects.filter(action="performance.kra_finalized").get()
    assert entry.new_value["final_total"] == str(record.final_total)
    assert set(entry.new_value["kpis"]) == {"ACCURACY", "TIMELINESS", "CLIENT_SERVICING",
                                            "FINANCIAL_ACCURACY", "DATA_SYSTEM", "COMPLIANCE"}


def test_actions_are_refused_in_the_wrong_state(month, hr, ist):
    plan, emp, record = month
    record = _fresh(record)
    with pytest.raises(review.KraStateError):  # E12: adjustments only under review
        review.adjust_kpi(actor=hr, performance=record, version=record.version,
                          kpi_id=_kpi_id(record, "ACCURACY"), points=D("2"), reason="x")
    with pytest.raises(review.KraStateError):
        _deduct(record, hr, plan, "DATA_INCONSISTENCY", D("20"))
    with pytest.raises(review.KraStateError):
        review.finalize(actor=hr, performance=record, version=record.version)
    with pytest.raises(review.KraStateError):
        review.reopen(actor=hr, performance=record, version=record.version, reason="x")
    with pytest.raises(review.KraStateError):
        review.return_for_recalculation(actor=hr, performance=record, version=record.version,
                                        reason="Recheck")
    submitted = _submitted(record, hr)
    record = review.finalize(actor=hr, performance=submitted, version=submitted.version)
    for action, kwargs in (
        (review.enter_manual_score, {"component_id": _component(record, "COMPLIANCE")
                                     .component_id, "achievement_pct": D("1"), "reason": "x"}),
        (review.mark_manual_na, {"component_id": _component(record, "COMPLIANCE").component_id,
                                 "reason": "x"}),
        (review.adjust_kpi, {"kpi_id": _kpi_id(record, "ACCURACY"), "points": D("1"),
                             "reason": "x"}),
        (review.submit, {}),
    ):
        with pytest.raises(review.KraStateError):  # nothing changes a FINALIZED month
            action(actor=hr, performance=record, version=record.version, **kwargs)
    with pytest.raises(services.PerformanceError):
        _calc(emp, ist, *CLOSE)


# --- E15: reopen and the model guard ------------------------------------------------------------


def test_reopen_by_hr_or_admin_with_a_reason(month, hr, admin_user):
    _, _, record = month
    record = _submitted(record, hr)
    record = review.finalize(actor=hr, performance=record, version=record.version)
    finalized_total = record.final_total
    with pytest.raises(FieldValidationError):
        review.reopen(actor=admin_user, performance=record, version=record.version, reason="")
    record = review.reopen(actor=admin_user, performance=record, version=record.version,
                           reason="Client complaint received late")
    record = _fresh(record)
    assert (record.status, record.reopen_count, record.finalized_at, record.finalized_by) == (
        "UNDER_REVIEW", 1, None, None,
    )
    assert record.final_total == finalized_total  # nothing else of the record changed
    entry = AuditLog.objects.filter(action="performance.kra_reopened").get()
    assert entry.new_value["reason"] == "Client complaint received late"
    assert entry.old_value["final_total"] == str(finalized_total)
    assert entry.old_value["finalized_by_id"] == hr.pk
    assert entry.actor_user == admin_user
    record = review.finalize(actor=hr, performance=record, version=record.version)  # again
    record = review.reopen(actor=hr, performance=record, version=record.version, reason="Again")
    assert _fresh(record).reopen_count == 2


def test_a_finalized_month_is_saved_only_by_the_reopen_transition(month, hr):
    _, _, record = month
    submitted = _submitted(record, hr)
    review.finalize(actor=hr, performance=submitted, version=submitted.version)
    record = _fresh(record)
    with pytest.raises(PerformanceLocked):
        record.save()
    with pytest.raises(PerformanceLocked):  # still FINALIZED in memory
        record.save(reopen=True, update_fields=["status"])
    record.status = "UNDER_REVIEW"
    with pytest.raises(PerformanceLocked):  # a field outside the reopen transition
        record.final_total = D("10")
        record.save(reopen=True, update_fields=["status", "final_total"])
    with pytest.raises(PerformanceLocked):  # update_fields is mandatory
        record.save(reopen=True)
    score = _fresh(record).kpi_scores.first()
    with pytest.raises(PerformanceLocked):
        score.save()


def test_legacy_months_keep_the_legacy_workflow(admin_user, hr, ops, assign, ist):
    emp = ops["rahul_emp"]
    assign(emp)
    with time_machine.travel(ist(*CLOSE), tick=False):
        legacy = services.calculate_monthly_performance(actor=hr, employee=emp, year=2026,
                                                        month=11)
    for action, kwargs in ((review.submit, {}), (review.reopen, {"reason": "x"}),
                           (review.finalize, {})):
        with pytest.raises(review.NotKraMonth):
            action(actor=hr, performance=legacy, version=legacy.version, **kwargs)
    with pytest.raises(review.NotKraMonth):  # refused before anything is written
        review.calculate(actor=hr, employee=emp, year=2026, month=11, now=ist(*CLOSE))
    with pytest.raises(review.NotKraMonth):
        review.month_detail(legacy)
    assert _fresh(legacy).version == legacy.version


# --- E1 / E2: HR adjustment ---------------------------------------------------------------------


def test_adjustment_is_recorded_append_only_and_the_latest_wins(month, hr):
    _, _, record = month
    record = _submitted(record, hr)
    accuracy = _kpi_id(record, "ACCURACY")
    record = review.adjust_kpi(actor=hr, performance=record, version=record.version,
                               kpi_id=accuracy, points=D("2.5"), reason="Quality issue")
    row = ScoreAdjustment.objects.get()
    assert (row.before_points, row.adjustment_points, row.after_points, row.actor, row.reason) == (
        D("3"), D("-0.5"), D("2.5"), hr, "Quality issue",
    )
    score = _kpi(_fresh(record), "ACCURACY")
    assert (score.auto_points, score.adjustment_points, score.final_points) == (
        D("3"), D("-0.5"), D("2.5"),
    )
    assert (_fresh(record).adjustment_total, _fresh(record).final_total) == (D("-0.5"), D("7.3"))
    record = review.adjust_kpi(actor=hr, performance=record, version=record.version,
                               kpi_id=accuracy, points=D("2.75"), reason="Second look")
    latest = ScoreAdjustment.objects.order_by("-id").first()
    assert (latest.before_points, latest.after_points) == (D("2.5"), D("2.75"))
    assert _totals(record) == (D("7.55"), "Consistent Performer")
    with pytest.raises(AppendOnly):
        latest.save()
    with pytest.raises(AppendOnly):
        latest.delete()
    for points, kpi, reason in ((D("3.01"), accuracy, "x"), (D("-0.1"), accuracy, "x"),
                                (D("1"), accuracy, " "),
                                (D("1"), _kpi_id(record, "FINANCIAL_ACCURACY"), "x")):
        with pytest.raises(FieldValidationError):  # range, reason, N/A KPI
            review.adjust_kpi(actor=hr, performance=record, version=_fresh(record).version,
                              kpi_id=kpi, points=points, reason=reason)
    assert ScoreAdjustment.objects.count() == 2


# --- E3-E11: deductions --------------------------------------------------------------------------


def test_component_kpi_and_overall_deductions(month, hr):
    plan, _, record = month
    record = _submitted(record, hr)
    accuracy = _component(record, "ACCURACY")
    record = _deduct(record, hr, plan, "MISSED_RECONCILIATION", D("25"),
                     component_id=accuracy.component_id)
    record = _deduct(record, hr, plan, "DELAYED_SYSTEM_UPDATE", D("10"),
                     kpi_id=_kpi_id(record, "ACCURACY"))
    record = _deduct(record, hr, plan, "DATA_INCONSISTENCY", D("20"))
    # Accuracy: P 3 -> component 25% of 3 = 0.75 -> KPI 10% of 2.25 = 0.225 -> F 2.025;
    # S = 2.025 + 2 + 1.2 + 0.6 + 1 = 6.825 -> overall 20% spread pro rata -> 5.46
    record = _fresh(record)
    finals = {s.kpi.code: s.final_points for s in record.kpi_scores.select_related("kpi")}
    assert finals == {"ACCURACY": D("1.62"), "TIMELINESS": D("1.6"),
                      "CLIENT_SERVICING": D("0.96"), "FINANCIAL_ACCURACY": D("0"),
                      "DATA_SYSTEM": D("0.48"), "COMPLIANCE": D("0.8")}
    assert _kpi(record, "ACCURACY").deduction_points == D("1.38")
    assert _component(record, "ACCURACY").deduction_points == D("0.75")
    assert (record.final_total, record.deduction_total, record.band_name) == (
        D("5.46"), D("2.34"), "Performance Concern",
    )
    assert record.final_total == sum(finals.values())  # E4: the sum of the final KPI points
    lines = review.month_detail(record)["deduction_lines"]
    assert sorted((line["scope"], line["points"]) for line in lines) == [
        ("COMPONENT", D("0.75")), ("KPI", D("0.225")), ("OVERALL", D("1.365")),
    ]
    overall = DeductionApplication.objects.get(rule__code="DATA_INCONSISTENCY")
    record = review.reverse_deduction(actor=hr, performance=record, version=record.version,
                                      application_id=overall.pk, reason="Wrong month")
    assert _totals(record) == (D("6.825"), "Needs Improvement")
    with pytest.raises(FieldValidationError):
        review.reverse_deduction(actor=hr, performance=record, version=_fresh(record).version,
                                 application_id=overall.pk, reason="again")
    reversal = DeductionApplication.objects.get(reverses=overall)
    with pytest.raises(FieldValidationError):
        review.reverse_deduction(actor=hr, performance=record, version=_fresh(record).version,
                                 application_id=reversal.pk, reason="undo")
    assert DeductionApplication.objects.count() == 4  # nothing deleted (append-only)
    with pytest.raises(AppendOnly):
        overall.delete()


def test_deduction_validation(month, hr):
    plan, _, record = month
    record = _submitted(record, hr)
    accuracy_component = _component(record, "ACCURACY").component_id
    accuracy = _kpi_id(record, "ACCURACY")
    bad = [
        ("MISSED_RECONCILIATION", D("15"), {"component_id": accuracy_component}),  # below 20
        ("MISSED_RECONCILIATION", D("25"), {"kpi_id": accuracy}),  # component rule
        ("MISSED_RECONCILIATION", None, {"component_id": accuracy_component}),  # no percent
        ("DELAYED_SYSTEM_UPDATE", D("10"), {"component_id": accuracy_component}),  # KPI rule
        ("DELAYED_SYSTEM_UPDATE", D("10"), {"kpi_id": _kpi_id(record, "FINANCIAL_ACCURACY")}),
        ("DATA_INCONSISTENCY", D("20"), {"kpi_id": accuracy}),  # overall rule
        ("TRANSACTION_ERROR", D("10"), {}),  # a band ceiling has no percentage
        ("MISSED_RECONCILIATION", D("25"),
         {"component_id": _component(record, "FINANCIAL_ACCURACY").component_id}),  # N/A
    ]
    for code, percent, target in bad:
        with pytest.raises(FieldValidationError):
            _deduct(record, hr, plan, code, percent, **target)
    with pytest.raises(FieldValidationError):  # a rule of another plan
        review.apply_deduction(actor=hr, performance=record, version=_fresh(record).version,
                               rule_id=0, percent=D("20"), reason="x")
    with pytest.raises(FieldValidationError):  # reason required
        review.apply_deduction(actor=hr, performance=record, version=_fresh(record).version,
                               rule_id=_rule(plan, "DATA_INCONSISTENCY").pk, percent=D("20"),
                               reason="")
    assert not DeductionApplication.objects.exists()


def test_band_ceilings_cap_the_band_without_changing_points(month, hr):
    plan, _, record = month
    record = _submitted(record, hr)
    assert _totals(record) == (D("7.8"), "Consistent Performer")
    record = _deduct(record, hr, plan, "TRANSACTION_ERROR")
    record = _fresh(record)
    assert (record.final_total, record.band_name, record.band_ceiling.name) == (
        D("7.8"), "Needs Improvement", "Needs Improvement",
    )
    record = _deduct(record, hr, plan, "REVENUE_LEAKAGE")  # E9: the lowest ceiling wins
    assert _totals(record) == (D("7.8"), "Performance Concern")
    leakage = DeductionApplication.objects.get(rule__code="REVENUE_LEAKAGE")
    record = review.reverse_deduction(actor=hr, performance=record, version=_fresh(record).version,
                                      application_id=leakage.pk, reason="Recovered")
    assert _totals(record) == (D("7.8"), "Needs Improvement")
    error = DeductionApplication.objects.get(rule__code="TRANSACTION_ERROR", reverses=None)
    record = review.reverse_deduction(actor=hr, performance=record, version=_fresh(record).version,
                                      application_id=error.pk, reason="Not an error")
    record = _fresh(record)
    assert (record.band_name, record.band_ceiling) == ("Consistent Performer", None)


def test_caps_sequential_stacking_and_non_stacking(admin_user, hr, ops, work, sla_24h, ist):
    rules = {**RULES,
             "MISSED_RECONCILIATION": {"scope": "KPI", "stacking": "STACK", "cap_pct": D("30"),
                                       "priority": 1}}
    plan = _activate_plan(admin_user, hr, ist, _category(_feed()), rules=rules,
                          method="SEQUENTIAL")
    emp = ops["rahul_emp"]
    task = work.raise_task(emp, 2026, 11, 2, 10, 0, title="A")
    work.complete(task, ops["rahul"], 2026, 11, 2, 12, 0)
    record = _submitted(_fill_manual(_calc(emp, ist, *CLOSE), hr), hr)
    accuracy, timeliness = _kpi_id(record, "ACCURACY"), _kpi_id(record, "TIMELINESS")
    for percent in (D("20"), D("20")):  # E8: the rule takes at most 30% of this target
        record = _deduct(record, hr, plan, "MISSED_RECONCILIATION", percent, kpi_id=accuracy)
    record = _deduct(record, hr, plan, "DELAYED_SYSTEM_UPDATE", D("10"), kpi_id=accuracy)
    # E5 SEQUENTIAL: 1 - 0.7 x 0.9 = 37% of 3 -> 1.89
    assert _kpi(_fresh(record), "ACCURACY").final_points == D("1.89")
    record = _deduct(record, hr, plan, "DELAYED_SYSTEM_UPDATE", D("30"), kpi_id=timeliness)
    record = _deduct(record, hr, plan, "SERVICE_DELAY_BEYOND_TAT", D("10"), kpi_id=timeliness)
    # E6: the non-stacking rule alone applies on Timeliness: 2 x 90% = 1.8
    assert _kpi(_fresh(record), "TIMELINESS").final_points == D("1.8")
    lines = {(line["rule_code"], line["kpi"]): line
             for line in review.month_detail(_fresh(record))["deduction_lines"]}
    assert lines[("DELAYED_SYSTEM_UPDATE", "Timeliness & Task Discipline")]["effective"] is False
    assert lines[("MISSED_RECONCILIATION", "Accuracy")]["rate_pct"] == D("30")


# --- E14: return for recalculation ---------------------------------------------------------------


def test_return_recalculates_and_reapplies_adjustments_and_deductions(
    admin_user, hr, ops, work, sla_24h, ist
):
    plan = _activate_plan(admin_user, hr, ist, _category(_feed()))
    emp = ops["rahul_emp"]
    done = work.raise_task(emp, 2026, 11, 2, 10, 0, title="A")
    work.complete(done, ops["rahul"], 2026, 11, 2, 12, 0)
    work.raise_task(emp, 2026, 11, 9, 10, 0, title="F")  # due 10 Nov, never done
    record = _submitted(_fill_manual(_calc(emp, ist, *CLOSE), hr), hr)
    accuracy = _kpi_id(record, "ACCURACY")
    assert _kpi(record, "ACCURACY").auto_points == D("1.2")  # 50% -> 40%
    record = review.adjust_kpi(actor=hr, performance=record, version=record.version,
                               kpi_id=accuracy, points=D("2"), reason="Context")
    record = _deduct(record, hr, plan, "DELAYED_SYSTEM_UPDATE", D("10"), kpi_id=accuracy)
    assert _kpi(_fresh(record), "ACCURACY").final_points == D("1.8")
    ApprovedLeave.objects.create(employee=emp, start_date=date(2026, 11, 10),
                                 end_date=date(2026, 11, 10), recorded_by=hr)
    audit_before = AuditLog.objects.order_by("-id").first().pk
    record = review.return_for_recalculation(actor=hr, performance=record,
                                             version=_fresh(record).version,
                                             reason="Leave recorded late", now=ist(2026, 12, 5))
    record = _fresh(record)
    accuracy_row = _kpi(record, "ACCURACY")
    assert record.status == "CALCULATED" and record.reviewed_by is None
    assert accuracy_row.auto_points == D("3")  # recalculated: F is on leave now
    assert (accuracy_row.adjustment_points, accuracy_row.deduction_points,
            accuracy_row.final_points) == (D("-1"), D("0.2"), D("1.8"))  # T = 2 kept (E2)
    assert (ScoreAdjustment.objects.count(), DeductionApplication.objects.count()) == (1, 1)
    actions = list(AuditLog.objects.filter(pk__gt=audit_before).order_by("id")
                   .values_list("action", flat=True))
    assert actions == ["performance.kra_returned", "performance.kra_calculated",
                       "performance.kra_settled"]
    again = _calc(emp, ist, 2026, 12, 6)  # any later calculation re-applies them too
    assert _kpi(again, "ACCURACY").final_points == D("1.8")
    record = _submitted(again, hr)  # and the review goes on
    assert _fresh(record).status == "UNDER_REVIEW"


def test_an_adjustment_that_is_no_longer_valid_is_kept_but_not_applied(month, hr):
    _, _, record = month
    record = _submitted(record, hr)
    client = _kpi_id(record, "CLIENT_SERVICING")
    record = review.adjust_kpi(actor=hr, performance=record, version=record.version,
                               kpi_id=client, points=D("1.5"), reason="Excellent feedback")
    assert _totals(record) == (D("8.1"), "Consistent Performer")
    record = review.mark_manual_na(
        actor=hr, performance=record, version=_fresh(record).version,
        component_id=_component(record, "CLIENT_SERVICING").component_id,
        reason="Moved to back office",
    )
    record = _fresh(record)
    score = _kpi(record, "CLIENT_SERVICING")
    assert (score.not_applicable, score.adjustment_points, score.final_points) == (True, 0, 0)
    assert ScoreAdjustment.objects.filter(kpi_score=score).count() == 1
    detail = review.month_detail(record)
    row = next(k for k in detail["kpis"] if k["code"] == "CLIENT_SERVICING")
    assert (row["adjustment_applied"], len(row["adjustments"])) == (False, 1)
    assert (record.final_total, record.max_points_applicable) == (D("6.6"), D("7.00"))


def test_return_is_refused_as_a_whole_when_recalculation_is_refused(month, hr, ist):
    _, emp, record = month
    record = _submitted(record, hr)
    type(emp).objects.filter(pk=emp.pk).update(date_of_joining=date(2026, 12, 5))
    with pytest.raises(kra_engine.NotEmployedInPeriod):
        review.return_for_recalculation(actor=hr, performance=record, version=record.version,
                                        reason="Recheck", now=ist(2026, 12, 5))
    assert _fresh(record).status == "UNDER_REVIEW"  # nothing changed


# --- D6 / E21: generation gap decisions ------------------------------------------------------


def test_gap_decisions_take_effect_and_can_change_until_finalized(admin_user, hr, ops, ist, own):
    _activate_plan(admin_user, hr, ist, _category(_feed(), scope="SCHEDULED"))
    emp = ops["rahul_emp"]
    own(_feed(), emp, start=date(2026, 11, 1))
    schedule = RecurringSchedule.objects.get(responsibility=_feed())
    gap = ScheduleOccurrence.objects.create(schedule=schedule, occurrence_date=date(2026, 11, 4),
                                            status="MISSED")
    record = _fill_manual(_calc(emp, ist, *CLOSE), hr)
    assert [b["kind"] for b in review.blockers(record)] == ["UNDECIDED_GAP"]
    assert _kpi(record, "ACCURACY").not_applicable
    record = review.decide_gap(actor=hr, performance=record, version=record.version,
                               occurrence_id=gap.pk, decision="EMPLOYEE_RESPONSIBLE",
                               reason="Did not upload")
    accuracy = _kpi(_fresh(record), "ACCURACY")
    assert (accuracy.not_applicable, accuracy.auto_points) == (False, D("0"))
    assert review.blockers(_fresh(record)) == []
    assert _fresh(record).max_points_applicable == D("8.50")
    record = _submitted(record, hr)
    record = review.decide_gap(actor=hr, performance=record, version=record.version,
                               occurrence_id=gap.pk, decision="SYSTEM_ISSUE_EXCLUDE",
                               reason="Scheduler outage confirmed")  # E21: changed in review
    row = MonthlyTaskCredit.objects.get(occurrence_id=gap.pk)
    assert (row.outcome, row.na_reason) == ("NA", "GAP_SYSTEM_ISSUE")
    assert _kpi(_fresh(record), "ACCURACY").not_applicable
    entry = AuditLog.objects.filter(action="performance.kra_gap_decided").order_by("-id").first()
    assert entry.old_value["decision"] == "EMPLOYEE_RESPONSIBLE"
    record = review.finalize(actor=hr, performance=record, version=_fresh(record).version)
    with pytest.raises(review.KraStateError):
        review.decide_gap(actor=hr, performance=record, version=record.version,
                          occurrence_id=gap.pk, decision="EMPLOYEE_RESPONSIBLE", reason="x")
    amit = _calc(ops["amit_emp"], ist, *CLOSE)
    with pytest.raises(FieldValidationError):  # not a gap of Amit's month
        review.decide_gap(actor=hr, performance=amit, version=amit.version,
                          occurrence_id=gap.pk, decision="EMPLOYEE_RESPONSIBLE", reason="x")


# --- E20: approved leave and manual-task exceptions ------------------------------------------


def test_leave_and_task_exceptions_are_audited(hr, ops, work, sla_24h, ist):
    emp = ops["rahul_emp"]
    with pytest.raises(FieldValidationError):
        review.record_leave(actor=hr, employee=emp, start_date=date(2026, 11, 5),
                            end_date=date(2026, 11, 4))
    leave = review.record_leave(actor=hr, employee=emp, start_date=date(2026, 11, 4),
                                end_date=date(2026, 11, 5), reason="Medical")
    with pytest.raises(FieldValidationError):
        review.cancel_leave(actor=hr, leave=leave, reason="")
    leave = review.cancel_leave(actor=hr, leave=leave, reason="Entered by mistake")
    assert leave.cancelled_by == hr and leave.cancelled_at is not None
    with pytest.raises(review.KraStateError):
        review.cancel_leave(actor=hr, leave=leave, reason="again")
    task = work.raise_task(emp, 2026, 11, 2, 10, 0, title="Manual")
    row = review.create_task_override(actor=hr, task=task, responsibility=_feed(),
                                      action="EXCLUDE", reason="Different work")
    with pytest.raises(review.KraStateError):
        review.create_task_override(actor=hr, task=task, responsibility=_feed(),
                                    action="INCLUDE", reason="x")
    with pytest.raises(FieldValidationError):
        review.create_task_override(actor=hr, task=task, responsibility=_feed(),
                                    action="MAYBE", reason="x")
    review.remove_task_override(actor=hr, override=row, reason="Was right after all")
    assert not ManualTaskOverride.objects.exists()
    assert list(AuditLog.objects.filter(action__startswith="performance.")
                .exclude(action__startswith="performance.config")
                .order_by("id").values_list("action", flat=True)) == [
        "performance.leave_recorded", "performance.leave_cancelled",
        "performance.task_override_created", "performance.task_override_removed",
    ]


# --- E18: annual figures ------------------------------------------------------------------------


def _stored_month(employee, month, *, status="FINALIZED", total="0", maximum="10",
                  model="KRA_POINTS"):
    start, end = services.month_bounds(2026, month)
    return MonthlyPerformance.objects.create(
        employee=employee, year=2026, month=month, period_start=start, period_end=end,
        status=status, calculation_model=model, final_total=D(total),
        max_points_applicable=D(maximum), band_name="Band",
    )


def test_annual_uses_finalized_applicable_kra_months_only(ops):
    emp = ops["rahul_emp"]
    _stored_month(emp, 1, total="7.5")
    _stored_month(emp, 2, total="8.25")
    _stored_month(emp, 3, total="9", status="UNDER_REVIEW")
    _stored_month(emp, 4, total="0", maximum="0")  # everything N/A
    _stored_month(emp, 5, model="LEGACY_WEIGHTED")
    summary = annual_summary(emp, 2026)
    assert (summary["applicable_months"], summary["annual_total"], summary["annual_average"]) == (
        2, D("15.75"), D("7.875"),
    )
    assert summary["maximum_total"] == D("120")
    reasons = {row["month"]: row["reason"] for row in summary["excluded"]}
    assert (reasons[3], reasons[4], reasons[5], reasons[6]) == (
        "NOT_FINALIZED", "NOTHING_APPLICABLE", "LEGACY_SCALE", "NO_RECORD",
    )  # missing months are never counted as 0
    empty = annual_summary(ops["amit_emp"], 2026)
    assert (empty["applicable_months"], empty["annual_total"], empty["annual_average"]) == (
        0, None, None,
    )


# --- P12 / E17: scheduled runs ------------------------------------------------------------------


def test_scheduled_runs_calculate_kra_months_only(admin_user, hr, ops, assign, ist):
    _activate_plan(admin_user, hr, ist, _category(_feed()))
    assign(ops["amit_emp"])  # a legacy assignment: never calculated by the schedule (P7)
    with time_machine.travel(ist(2026, 11, 15, 1, 0), tick=False):
        result = daily_kra_calculation()
    assert result["current"] == {"calculated": 1, "legacy_not_scheduled": 1, "no_plan": 1}
    rahul = MonthlyPerformance.objects.get(employee=ops["rahul_emp"])
    assert rahul.cutoff_at == ist(2026, 11, 15, 1, 0)  # provisional
    assert not MonthlyPerformance.objects.filter(employee=ops["amit_emp"]).exists()
    with time_machine.travel(ist(2026, 12, 1, 2, 0), tick=False):
        closed = kra_month_close()
    assert closed["calculated"] == 1 and (closed["year"], closed["month"]) == (2026, 11)
    rahul.refresh_from_db()
    assert rahul.cutoff_at == ist(2026, 12, 1, 0, 0) and rahul.calculated_by is None
    review.submit(actor=hr, performance=rahul, version=rahul.version)
    with time_machine.travel(ist(2026, 12, 2, 1, 0), tick=False):
        result = scheduling.run_daily()
    assert result["previous"] == {}  # the month under review is not touched
    assert MonthlyPerformance.objects.get(pk=rahul.pk).status == "UNDER_REVIEW"
    with time_machine.travel(ist(2027, 1, 1, 2, 0), tick=False):
        assert scheduling.run_month_close()["calculated"] == 1  # December
    schedule = settings.CELERY_BEAT_SCHEDULE
    assert schedule["kra-daily-calculation"]["schedule"] == crontab(minute=0, hour=1)
    assert schedule["kra-month-close"]["schedule"] == crontab(minute=0, hour=2, day_of_month=1)


# --- E19: what the employee may see -------------------------------------------------------------


def test_the_employee_view_shows_rule_target_and_points_only(month, hr):
    plan, _, record = month
    record = _submitted(record, hr)
    record = review.adjust_kpi(actor=hr, performance=record, version=record.version,
                               kpi_id=_kpi_id(record, "ACCURACY"), points=D("2.5"),
                               reason="SECRET ADJUSTMENT REASON")
    record = _deduct(record, hr, plan, "DELAYED_SYSTEM_UPDATE", D("10"),
                     kpi_id=_kpi_id(record, "ACCURACY"))
    view = review.employee_view(_fresh(record))
    assert view["deductions"] == [{"rule": "Delayed system update", "kpi": "Accuracy",
                                   "component": "", "points": D("0.25")}]
    text = json.dumps(view, default=str)
    for hidden in ("SECRET ADJUSTMENT REASON", "found in review", "Ticket 42", "evidence",
                   "reason", "applied_by", "actor", "entered_by"):
        assert hidden not in text
    detail = json.dumps(review.month_detail(_fresh(record)), default=str)
    assert "Ticket 42" in detail and "SECRET ADJUSTMENT REASON" in detail  # HR / Admin only


# --- safety: calculation without review inputs is unchanged ------------------------------------


def test_without_review_inputs_calculation_writes_the_engine_values_only(
    admin_user, hr, ops, work, sla_24h, ist
):
    _activate_plan(admin_user, hr, ist, _category(_feed()))
    emp = ops["rahul_emp"]
    task = work.raise_task(emp, 2026, 11, 2, 10, 0, title="A")
    work.complete(task, ops["rahul"], 2026, 11, 2, 12, 0)
    before = AuditLog.objects.order_by("-id").first().pk
    record = _calc(emp, ist, *CLOSE)
    assert (record.final_total, record.version, record.band_ceiling) == (D("3"), 1, None)
    assert list(AuditLog.objects.filter(pk__gt=before).values_list("action", flat=True)) == [
        "performance.kra_calculated",
    ]
    assert timezone.is_aware(record.cutoff_at)


# --- validation edges ---------------------------------------------------------------------------


def test_input_and_target_validation_edges(month, hr, ist):
    plan, _, record = month
    record = _submitted(record, hr)
    accuracy = _kpi_id(record, "ACCURACY")
    for points in ("abc", "NaN", "1.0000001", None):
        with pytest.raises(FieldValidationError):
            review.adjust_kpi(actor=hr, performance=record, version=record.version,
                              kpi_id=accuracy, points=points, reason="x")
    with pytest.raises(FieldValidationError):  # not a KPI of this month
        review.adjust_kpi(actor=hr, performance=record, version=record.version, kpi_id=0,
                          points=D("1"), reason="x")
    for code, target in (("MISSED_RECONCILIATION", {"component_id": 0}),
                         ("MISSED_RECONCILIATION",
                          {"component_id": _component(record, "ACCURACY").component_id,
                           "kpi_id": _kpi_id(record, "TIMELINESS")}),
                         ("DELAYED_SYSTEM_UPDATE", {"kpi_id": 0})):
        with pytest.raises(FieldValidationError):
            _deduct(record, hr, plan, code, D("25"), **target)
    with pytest.raises(FieldValidationError):
        review.reverse_deduction(actor=hr, performance=record, version=record.version,
                                 application_id=0, reason="x")
    with pytest.raises(FieldValidationError):
        review.decide_gap(actor=hr, performance=record, version=record.version,
                          occurrence_id=1, decision="MAYBE", reason="x")
    MonthlyPerformance.objects.filter(pk=record.pk).update(cutoff_at=ist(2026, 11, 20))
    with pytest.raises(review.KraStateError):  # never finalizes a provisional month
        review.finalize(actor=hr, performance=record, version=record.version)


def test_a_gap_decision_respects_leave_and_finalized_months(admin_user, hr, ops, ist, own):
    _activate_plan(admin_user, hr, ist, _category(_feed(), scope="SCHEDULED"))
    emp = ops["rahul_emp"]
    own(_feed(), emp, start=date(2026, 11, 1))
    schedule = RecurringSchedule.objects.get(responsibility=_feed())
    on_leave = ScheduleOccurrence.objects.create(schedule=schedule,
                                                 occurrence_date=date(2026, 11, 6),
                                                 status="MISSED")
    ApprovedLeave.objects.create(employee=emp, start_date=date(2026, 11, 6),
                                 end_date=date(2026, 11, 6), recorded_by=hr)
    record = _calc(emp, ist, *CLOSE)
    record = review.decide_gap(actor=hr, performance=record, version=record.version,
                               occurrence_id=on_leave.pk, decision="EMPLOYEE_RESPONSIBLE",
                               reason="Recorded anyway")
    row = MonthlyTaskCredit.objects.get(occurrence_id=on_leave.pk)
    assert (row.outcome, row.na_reason) == ("NA", "APPROVED_LEAVE")  # leave still wins (D7)
    ScheduleOccurrence.objects.filter(pk=on_leave.pk).update(status="GENERATED")
    with pytest.raises(FieldValidationError):
        review.decide_gap(actor=hr, performance=record, version=_fresh(record).version,
                          occurrence_id=on_leave.pk, decision="EMPLOYEE_RESPONSIBLE",
                          reason="x")
    ScheduleOccurrence.objects.filter(pk=on_leave.pk).update(status="MISSED")
    other = _calc(ops["amit_emp"], ist, *CLOSE)  # e.g. the owner history was changed later
    MonthlyTaskCredit.objects.create(
        component_result=_component(other, "ACCURACY"), occurrence_id=on_leave.pk,
        outcome="NA", na_reason="GAP_UNDECIDED", match_reason="GAP",
    )
    MonthlyPerformance.objects.filter(pk=other.pk).update(status="FINALIZED")
    with pytest.raises(review.KraStateError):  # the same gap in a finalized month
        review.decide_gap(actor=hr, performance=record, version=_fresh(record).version,
                          occurrence_id=on_leave.pk, decision="EMPLOYEE_RESPONSIBLE",
                          reason="x")


def test_scheduled_runs_count_every_skip(admin_user, hr, ops, ist, monkeypatch):
    _activate_plan(admin_user, hr, ist, _category(_feed()))
    with time_machine.travel(ist(2026, 12, 1, 2, 0), tick=False):
        assert scheduling.run_month_close()["calculated"] == 2  # Rahul and Amit
        assert scheduling.run_daily()["previous"] == {"calculated": 2}  # still CALCULATED
    rahul = MonthlyPerformance.objects.get(employee=ops["rahul_emp"], month=11)
    review.submit(actor=hr, performance=rahul, version=rahul.version)
    type(ops["amit_emp"]).objects.filter(pk=ops["amit_emp"].pk).update(
        date_of_joining=date(2027, 1, 5)
    )
    with time_machine.travel(ist(2026, 12, 1, 3, 0), tick=False):
        result = scheduling.run_month_close()
    assert (result["under_review"], result["not_in_period"], result["no_plan"]) == (1, 1, 1)

    def refuse(**kwargs):
        raise services.PerformanceError("Refused")

    monkeypatch.setattr(scheduling, "calculate_month", refuse)
    type(ops["amit_emp"]).objects.filter(pk=ops["amit_emp"].pk).update(date_of_joining=None)
    with time_machine.travel(ist(2026, 12, 1, 4, 0), tick=False):
        assert scheduling.run_month_close()["refused"] == 2  # never stops the run


def test_ceiling_lines_are_shown_to_hr_and_the_employee(month, hr):
    plan, _, record = month
    record = _deduct(_submitted(record, hr), hr, plan, "TRANSACTION_ERROR")
    lines = review.month_detail(_fresh(record))["deduction_lines"]
    assert [(line["rule_code"], line["ceiling_band"], line["effective"], line["points"])
            for line in lines] == [("TRANSACTION_ERROR", "Needs Improvement", True, 0)]
    view = review.employee_view(_fresh(record))
    assert view["deductions"] == [{"rule": "Transaction error", "kpi": "", "component": "",
                                   "points": 0}]
    assert view["band"] == "Needs Improvement"


# --- independent review findings (Phase 7.3) ---------------------------------------------------


def test_return_never_turns_a_kra_month_into_a_legacy_month(month, hr, admin_user, assign, ist):
    _, emp, record = month
    submitted = _submitted(record, hr)
    record = review.finalize(actor=hr, performance=submitted, version=submitted.version)
    record = review.reopen(actor=admin_user, performance=record, version=record.version,
                           reason="Recheck")
    assign(emp, start=date(2026, 11, 1))  # a legacy weight assignment now covers November
    components = MonthlyComponentResult.objects.filter(
        kpi_score__monthly_performance=record).count()
    with pytest.raises(review.NotKraMonth):
        review.return_for_recalculation(actor=hr, performance=record, version=record.version,
                                        reason="Recheck", now=ist(2026, 12, 9))
    record = _fresh(record)
    assert (record.calculation_model, record.status) == ("KRA_POINTS", "UNDER_REVIEW")
    assert MonthlyComponentResult.objects.filter(
        kpi_score__monthly_performance=record).count() == components


def test_the_engine_audit_records_the_automatic_score_on_recalculation(month, hr, ist):
    _, emp, record = month
    record = _submitted(record, hr)
    record = review.adjust_kpi(actor=hr, performance=record, version=record.version,
                               kpi_id=_kpi_id(record, "ACCURACY"), points=D("2"), reason="x")
    record = review.return_for_recalculation(actor=hr, performance=record,
                                             version=_fresh(record).version, reason="Recheck",
                                             now=ist(2026, 12, 9))
    calculated = AuditLog.objects.filter(action="performance.kra_calculated").order_by("-id")[0]
    settled = AuditLog.objects.filter(action="performance.kra_settled").order_by("-id")[0]
    assert calculated.new_value["final_total"] == "7.800000"  # automatic only
    assert settled.new_value["final_total"] == "6.800000"  # HR's adjustment re-applied
    assert _fresh(record).final_total == D("6.8")


def test_deductions_of_another_plan_version_stop_a_recalculation(month, hr, ist):
    plan, emp, record = month
    record = _deduct(_submitted(record, hr), hr, plan, "DATA_INCONSISTENCY", D("20"))
    record = review.return_for_recalculation(actor=hr, performance=record,
                                             version=_fresh(record).version, reason="Recheck",
                                             now=ist(2026, 12, 9))
    with time_machine.travel(ist(*OCT_8), tick=False):
        draft = svc.clone_plan_version(actor=hr, version=plan)
    other = DeductionRule.objects.get(plan_version=draft, code="DATA_INCONSISTENCY")
    DeductionApplication.objects.filter(monthly_performance=record).update(rule=other)
    with pytest.raises(services.PerformanceError) as refused:
        _calc(emp, ist, 2026, 12, 10)
    assert refused.value.code == "plan_changed_under_deductions"
    assert _fresh(record).final_total == D("6.24")  # unchanged (7.8 x 80%)


def test_a_failure_never_stops_a_scheduled_run_and_legacy_records_are_left_alone(
    admin_user, hr, ops, ist, monkeypatch
):
    _activate_plan(admin_user, hr, ist, _category(_feed()))
    start, end = services.month_bounds(2026, 11)
    MonthlyPerformance.objects.create(employee=ops["manager_emp"], year=2026, month=11,
                                      period_start=start, period_end=end, status="CALCULATED")
    original = scheduling.calculate_month

    def flaky(*, employee, **kwargs):
        if employee == ops["rahul_emp"]:
            raise RuntimeError("database hiccup")
        return original(employee=employee, **kwargs)

    monkeypatch.setattr(scheduling, "calculate_month", flaky)
    with time_machine.travel(ist(2026, 12, 1, 2, 0), tick=False):
        result = scheduling.run_month_close()
    assert (result["error"], result["calculated"], result["legacy_not_scheduled"]) == (1, 1, 1)
    assert MonthlyPerformance.objects.get(employee=ops["manager_emp"]).calculation_model == (
        "LEGACY_WEIGHTED"
    )


# --- 7.3 decision 6: a reason is required to return a month and to enter a manual score ---------

BAD_REASONS = (None, "", "   ")


def _quiet_since(audit_id):
    return not AuditLog.objects.filter(pk__gt=audit_id).exists()


@pytest.mark.parametrize("reason", BAD_REASONS)
def test_return_for_recalculation_requires_a_reason(month, hr, ist, reason):
    _, _, record = month
    record = _submitted(record, hr)
    before = (record.status, record.version, record.final_total)
    last_audit = AuditLog.objects.order_by("-id").first().pk
    with pytest.raises(FieldValidationError) as refused:
        review.return_for_recalculation(actor=hr, performance=record, version=record.version,
                                        reason=reason, now=ist(2026, 12, 9))
    assert set(refused.value.fields) == {"reason"}
    fresh = _fresh(record)
    assert (fresh.status, fresh.version, fresh.final_total) == before  # nothing changed
    assert _quiet_since(last_audit)


def test_return_for_recalculation_records_its_reason(month, hr, ist):
    _, _, record = month
    record = _submitted(record, hr)
    record = review.return_for_recalculation(actor=hr, performance=record,
                                             version=record.version,
                                             reason="  Client data corrected  ",
                                             now=ist(2026, 12, 9))
    assert _fresh(record).status == "CALCULATED"
    entry = AuditLog.objects.filter(action="performance.kra_returned").get()
    assert entry.new_value["reason"] == "Client data corrected"


@pytest.mark.parametrize("reason", BAD_REASONS)
def test_a_manual_score_requires_a_reason(admin_user, hr, ops, ist, reason):
    _activate_plan(admin_user, hr, ist, _category(_feed()))
    record = _calc(ops["rahul_emp"], ist, *CLOSE)
    component = _component(record, "COMPLIANCE")
    last_audit = AuditLog.objects.order_by("-id").first().pk
    with pytest.raises(FieldValidationError) as refused:
        review.enter_manual_score(actor=hr, performance=record, version=record.version,
                                  component_id=component.component_id,
                                  achievement_pct=D("90"), reason=reason)
    assert set(refused.value.fields) == {"reason"}
    component.refresh_from_db()
    assert (component.entered_by, component.manual_achievement_pct, component.na_reason) == (
        None, None, "AWAITING_HR_ENTRY",
    )
    assert _kpi(_fresh(record), "COMPLIANCE").not_applicable
    assert _fresh(record).version == record.version
    assert _quiet_since(last_audit)


def test_a_manual_score_records_its_reason(admin_user, hr, ops, ist):
    _activate_plan(admin_user, hr, ist, _category(_feed()))
    record = _calc(ops["rahul_emp"], ist, *CLOSE)
    record = review.enter_manual_score(
        actor=hr, performance=record, version=record.version,
        component_id=_component(record, "COMPLIANCE").component_id, achievement_pct=D("100"),
        reason="  Audit file complete  ",
    )
    assert _kpi(_fresh(record), "COMPLIANCE").final_points == D("1")
    entry = AuditLog.objects.filter(action="performance.kra_manual_entry").get()
    assert entry.new_value["reason"] == "Audit file complete"


# --- 7.3 decision 4: ADDITIVE above 100%, stored ------------------------------------------------

OVERFLOW = {"MISSED_RECONCILIATION": D("50"), "DELAYED_SYSTEM_UPDATE": D("30"),
            "DATA_INCONSISTENCY": D("40")}  # 120% on Accuracy, all KPI-scope stacking rules


@pytest.mark.parametrize(("priorities", "allocation"), [
    ((1, 2, 3), {"MISSED_RECONCILIATION": D("1.5"), "DELAYED_SYSTEM_UPDATE": D("0.9"),
                 "DATA_INCONSISTENCY": D("0.6")}),
    ((3, 2, 1), {"MISSED_RECONCILIATION": D("0.9"), "DELAYED_SYSTEM_UPDATE": D("0.9"),
                 "DATA_INCONSISTENCY": D("1.2")}),
])
def test_additive_overflow_is_capped_and_priority_only_moves_the_allocation(
    admin_user, hr, ops, work, sla_24h, ist, priorities, allocation
):
    rules = {**RULES, "SERVICE_DELAY_BEYOND_TAT": {**RULES["SERVICE_DELAY_BEYOND_TAT"],
                                                   "priority": 4}}
    for code, priority in zip(OVERFLOW, priorities, strict=True):
        rules[code] = {"scope": "KPI", "stacking": "STACK", "uncapped": True,
                       "priority": priority}
    plan = _activate_plan(admin_user, hr, ist, _category(_feed()), rules=rules)
    emp = ops["rahul_emp"]
    for day, title in ((2, "A"), (3, "B")):
        task = work.raise_task(emp, 2026, 11, day, 10, 0, title=title)
        work.complete(task, ops["rahul"], 2026, 11, day, 12, 0)
    record = _submitted(_fill_manual(_calc(emp, ist, *CLOSE), hr), hr)
    for code, percent in OVERFLOW.items():
        record = _deduct(record, hr, plan, code, percent, kpi_id=_kpi_id(record, "ACCURACY"))
    record = _fresh(record)
    accuracy = _kpi(record, "ACCURACY")
    # Same stored result for either priority order: 120% is capped at 100% of 3 points (E11)
    assert (accuracy.deduction_points, accuracy.final_points) == (D("3"), D("0"))
    assert (record.final_total, record.deduction_total, record.band_name) == (
        D("4.8"), D("3"), "Performance Concern",
    )
    lines = review.month_detail(record)["deduction_lines"]
    assert {line["rule_code"]: line["points"] for line in lines} == allocation
    assert sum(line["points"] for line in lines) == record.deduction_total  # reconciles
