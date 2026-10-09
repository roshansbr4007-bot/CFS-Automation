"""Review, finalization lock, historical integrity, recalculation, audit and the command."""

from datetime import date
from decimal import Decimal
from io import StringIO

import pytest
import time_machine
from django.core.management import call_command

from apps.audit.models import AuditLog
from apps.core.errors import FieldValidationError
from apps.performance import services
from apps.performance.models import KPI, MonthlyKPIScore, MonthlyPerformance, PerformanceLocked

pytestmark = pytest.mark.django_db
MANAGER_SCORES = {"ACCURACY": "92", "CLIENT_SERVICING": "90", "FINANCIAL_ACCURACY": "94",
                  "DATA_SYSTEM": "96", "COMPLIANCE": "98"}


@pytest.fixture
def calculated(ops, work, sla_24h, assign, ist):
    """Rahul's October: one task completed on time (Timeliness = 100), calculated on 5 Nov."""
    assign(ops["rahul_emp"])
    task = work.raise_task(ops["rahul_emp"], 2026, 10, 5, 10, 0)
    work.complete(task, ops["rahul"], 2026, 10, 5, 11, 0)
    with time_machine.travel(ist(2026, 11, 5, 9, 0), tick=False):
        return services.calculate_monthly_performance(
            actor=ops["manager"], employee=ops["rahul_emp"], year=2026, month=10
        )


def _score_all(performance, actor, scores=MANAGER_SCORES):
    for code, value in scores.items():
        performance.refresh_from_db()
        services.update_kpi_score(actor=actor, performance=performance,
                                  version=performance.version, kpi_code=code, score=value,
                                  remark=f"{code} reviewed")
    performance.refresh_from_db()
    return performance


def _submit_and_finalize(performance, actor):
    performance = services.submit_review(actor=actor, performance=performance,
                                         version=performance.version, remark="Solid month")
    return services.finalize_performance(actor=actor, performance=performance,
                                         version=performance.version)


def _scores(performance):
    return {s.kpi.code: s for s in MonthlyKPIScore.objects.filter(
        monthly_performance=performance).select_related("kpi")}


def test_manager_scores_and_finalization(ops, calculated):
    performance = _score_all(calculated, ops["manager"])
    # 92x3 + 100x2 + 90x1.5 + 94x1.5 + 96x1 + 98x1 = 946 / 10
    assert performance.overall_score == Decimal("94.60")
    assert performance.performance_band == "Excellent"
    performance = _submit_and_finalize(performance, ops["manager"])
    assert performance.status == "FINALIZED" and performance.finalized_by == ops["manager"]
    assert performance.reviewed_by == ops["manager"] and performance.manager_remark == "Solid month"
    accuracy = _scores(performance)["ACCURACY"]
    assert accuracy.score == Decimal("92.00") and accuracy.reviewed_by == ops["manager"]
    assert accuracy.manager_remark == "ACCURACY reviewed"


def test_score_validation(ops, calculated):
    for bad in ("100.5", "-1", "abc"):
        with pytest.raises(FieldValidationError):
            services.update_kpi_score(actor=ops["manager"], performance=calculated,
                                      version=calculated.version, kpi_code="ACCURACY", score=bad)
    with pytest.raises(FieldValidationError):  # the system calculates Timeliness
        services.update_kpi_score(actor=ops["manager"], performance=calculated,
                                  version=calculated.version, kpi_code="TIMELINESS", score="80")
    with pytest.raises(FieldValidationError):
        services.update_kpi_score(actor=ops["manager"], performance=calculated,
                                  version=calculated.version, kpi_code="NOPE", score="80")
    with pytest.raises(services.PerformanceVersionConflict):
        services.update_kpi_score(actor=ops["manager"], performance=calculated,
                                  version=calculated.version + 7, kpi_code="ACCURACY", score="80")


def test_finalization_is_blocked_until_every_kpi_has_a_score(ops, calculated):
    performance = _score_all(calculated, ops["manager"], {"ACCURACY": "90"})
    performance = services.submit_review(actor=ops["manager"], performance=performance,
                                         version=performance.version)
    with pytest.raises(FieldValidationError) as missing:
        services.finalize_performance(actor=ops["manager"], performance=performance,
                                      version=performance.version)
    assert "CLIENT_SERVICING" in str(missing.value.fields)
    assert performance.overall_score is None  # missing scores are never treated as 0


def test_workflow_order_is_enforced(ops, calculated):
    with pytest.raises(services.PerformanceError):  # must be submitted first
        services.finalize_performance(actor=ops["manager"], performance=calculated,
                                      version=calculated.version)
    submitted = services.submit_review(actor=ops["manager"], performance=calculated,
                                       version=calculated.version)
    with pytest.raises(services.PerformanceError):
        services.submit_review(actor=ops["manager"], performance=submitted,
                               version=submitted.version)


def test_finalized_performance_cannot_be_edited(ops, calculated, ist):
    performance = _submit_and_finalize(_score_all(calculated, ops["manager"]), ops["manager"])
    with pytest.raises(services.PerformanceError):
        services.update_kpi_score(actor=ops["manager"], performance=performance,
                                  version=performance.version, kpi_code="ACCURACY", score="50")
    with pytest.raises(services.PerformanceError), \
            time_machine.travel(ist(2026, 11, 6, 9, 0), tick=False):
        services.calculate_monthly_performance(actor=ops["manager"], employee=ops["rahul_emp"],
                                               year=2026, month=10)
    performance.manager_remark = "silently changed"
    with pytest.raises(PerformanceLocked):
        performance.save()
    row = _scores(performance)["ACCURACY"]
    row.score = Decimal("10")
    with pytest.raises(PerformanceLocked):
        row.save()
    performance.refresh_from_db()
    assert performance.manager_remark == "Solid month"
    assert _scores(performance)["ACCURACY"].score == Decimal("92.00")


def test_historical_weight_and_score_do_not_change(admin_user, ops, ops_version, calculated,
                                                   work, ist, assign):
    october = _submit_and_finalize(_score_all(calculated, ops["manager"]), ops["manager"])
    current = services.assignment_for(ops["rahul_emp"], date(2026, 10, 31))
    services.end_kpi_assignment(actor=admin_user, assignment=current, last_day=date(2026, 10, 31))
    services.end_weight_version(actor=admin_user, version=ops_version, last_day=date(2026, 10, 31))
    november = services.create_weight_version(
        actor=admin_user, configuration="OPERATIONS", name="Operations (Nov)",
        effective_from=date(2026, 11, 1),
        weights={"ACCURACY": "2.5", "TIMELINESS": "2.5", "CLIENT_SERVICING": "1.5",
                 "FINANCIAL_ACCURACY": "1.5", "DATA_SYSTEM": "1.0", "COMPLIANCE": "1.0"},
    )
    services.activate_weight_version(actor=admin_user, version=november)
    assign(ops["rahul_emp"], version=november, start=date(2026, 11, 1))
    late = work.raise_task(ops["rahul_emp"], 2026, 10, 8, 10, 0)  # more October work, later
    work.complete(late, ops["rahul"], 2026, 11, 2, 10, 0)
    october.refresh_from_db()
    assert _scores(october)["ACCURACY"].weight == Decimal("3.00")  # not 2.50
    assert october.overall_score == Decimal("94.60") and october.completed_tasks == 1
    with time_machine.travel(ist(2026, 12, 2, 9, 0), tick=False):
        nov = services.calculate_monthly_performance(actor=None, employee=ops["rahul_emp"],
                                                     year=2026, month=11)
    assert _scores(nov)["ACCURACY"].weight == Decimal("2.50")


def test_recalculation_keeps_manager_scores_and_refreshes_the_system_kpi(ops, calculated, work,
                                                                         ist):
    performance = _score_all(calculated, ops["manager"], {"ACCURACY": "88"})
    late = work.raise_task(ops["rahul_emp"], 2026, 10, 20, 10, 0)
    work.complete(late, ops["rahul"], 2026, 10, 22, 10, 0)  # MISSED
    with time_machine.travel(ist(2026, 11, 6, 9, 0), tick=False):
        again = services.calculate_monthly_performance(
            actor=ops["manager"], employee=ops["rahul_emp"], year=2026, month=10
        )
    scores = _scores(again)
    assert scores["ACCURACY"].score == Decimal("88.00")
    assert scores["TIMELINESS"].score == Decimal("50.00")  # 1 of 2 on time now
    assert again.completed_tasks == 2 and again.version > performance.version
    assert MonthlyPerformance.objects.filter(employee=ops["rahul_emp"]).count() == 1


def test_recalculation_during_review_returns_to_calculated(ops, calculated, ist):
    submitted = services.submit_review(actor=ops["manager"], performance=calculated,
                                       version=calculated.version)
    assert submitted.status == "UNDER_REVIEW"
    with time_machine.travel(ist(2026, 11, 6, 9, 0), tick=False):
        again = services.calculate_monthly_performance(
            actor=ops["manager"], employee=ops["rahul_emp"], year=2026, month=10
        )
    assert again.status == "CALCULATED"


def test_rejected_deactivation_and_changed_version_rebuild_rows(admin_user, ops, ops_version,
                                                               calculated, ist):
    with pytest.raises(FieldValidationError):  # DATA_SYSTEM belongs to the active Operations v1
        services.set_kpi_active(actor=admin_user, kpi=KPI.objects.get(code="DATA_SYSTEM"),
                                is_active=False)
    with time_machine.travel(ist(2026, 11, 6, 9, 0), tick=False):
        again = services.calculate_monthly_performance(
            actor=ops["manager"], employee=ops["rahul_emp"], year=2026, month=10
        )
    assert set(_scores(again)) == {"ACCURACY", "TIMELINESS", "CLIENT_SERVICING",
                                   "FINANCIAL_ACCURACY", "DATA_SYSTEM", "COMPLIANCE"}
    assignment = services.assignment_for(ops["rahul_emp"], date(2026, 10, 31))
    services.end_kpi_assignment(actor=admin_user, assignment=assignment, last_day=date(2026, 9, 30))
    services.end_weight_version(actor=admin_user, version=ops_version, last_day=date(2026, 9, 30))
    october_version = services.create_weight_version(
        actor=admin_user, configuration="OPERATIONS", name="Operations (Oct fix)",
        effective_from=date(2026, 10, 1),
        weights={"ACCURACY": "4.0", "TIMELINESS": "2.0", "CLIENT_SERVICING": "1.0",
                 "FINANCIAL_ACCURACY": "1.0", "DATA_SYSTEM": "1.0", "COMPLIANCE": "1.0"},
    )
    services.activate_weight_version(actor=admin_user, version=october_version)
    services.assign_kpi_version(actor=admin_user, employee=ops["rahul_emp"],
                                version=october_version, effective_from=date(2026, 10, 1))
    with time_machine.travel(ist(2026, 11, 7, 9, 0), tick=False):
        rebuilt = services.calculate_monthly_performance(
            actor=ops["manager"], employee=ops["rahul_emp"], year=2026, month=10
        )
    assert rebuilt.weight_version == october_version
    assert _scores(rebuilt)["ACCURACY"].weight == Decimal("4.00")
    assert len(_scores(rebuilt)) == 6


def test_timeliness_not_applicable_blocks_finalization(ops, assign, ist):
    """With no SLA work Timeliness is not applicable (None). Under P4 the month cannot be
    finalized until the manager scores it explicitly (approved Option B; see the tests below)."""
    assign(ops["amit_emp"])
    with time_machine.travel(ist(2026, 11, 5, 9, 0), tick=False):
        performance = services.calculate_monthly_performance(
            actor=None, employee=ops["amit_emp"], year=2026, month=10
        )
    performance = _score_all(performance, ops["manager"])
    performance = services.submit_review(actor=ops["manager"], performance=performance,
                                         version=performance.version)
    with pytest.raises(FieldValidationError) as missing:
        services.finalize_performance(actor=ops["manager"], performance=performance,
                                      version=performance.version)
    assert "TIMELINESS" in str(missing.value.fields)


def test_audit_events(ops, calculated):
    performance = _submit_and_finalize(_score_all(calculated, ops["manager"]), ops["manager"])
    rows = AuditLog.objects.filter(entity_type="monthly_performance", entity_id=str(performance.pk))
    actions = list(rows.order_by("id").values_list("action", flat=True))
    assert actions == ["performance.calculated"] + ["performance.kpi_score_updated"] * 5 + [
        "performance.submitted", "performance.finalized",
    ]
    calc = rows.get(action="performance.calculated")
    assert calc.new_value["completed_tasks"] == 1 and calc.new_value["timeliness"] == "100.00"
    update = rows.get(action="performance.kpi_score_updated", context__kpi="ACCURACY")
    assert update.old_value["score"] is None and update.new_value["score"] == "92.00"
    assert update.context["kpi"] == "ACCURACY"
    final = rows.get(action="performance.finalized")
    assert final.new_value["overall_score"] == "94.60"
    assert final.new_value["kpi_scores"]["ACCURACY"] == {"score": "92.00", "weight": "3.00"}
    assert final.actor_user == ops["manager"]


def test_management_command_reports_skips(ops, assign, ist):
    assign(ops["rahul_emp"])
    out = StringIO()
    with time_machine.travel(ist(2026, 11, 5, 9, 0), tick=False):
        call_command("calculate_performance", "--year", "2026", "--month", "10", stdout=out)
    text = out.getvalue()
    assert "1 calculated" in text and "Skipped (no KPI assignment)" in text
    assert ops["amit_emp"].full_name in text
    performance = MonthlyPerformance.objects.get(employee=ops["rahul_emp"])
    assert performance.calculated_by is None  # system run
    one = StringIO()
    call_command("calculate_performance", "--year", "2026", "--month", "10",
                 "--employee", str(ops["rahul_emp"].pk), stdout=one)
    assert "1 calculated" in one.getvalue()
    assert str(performance).endswith("CALCULATED")


# --- approved Option B: Timeliness not applicable --------------------------------------------


@pytest.fixture
def not_applicable(ops, assign, ist):
    """Amit's October without any SLA work: Timeliness is not applicable."""
    assign(ops["amit_emp"])
    with time_machine.travel(ist(2026, 11, 5, 9, 0), tick=False):
        performance = services.calculate_monthly_performance(
            actor=ops["manager"], employee=ops["amit_emp"], year=2026, month=10
        )
    timeliness = _scores(performance)["TIMELINESS"]
    assert (timeliness.score, timeliness.source) == (None, "SYSTEM")
    return performance


def test_timeliness_not_applicable_can_be_scored_by_the_manager(ops, not_applicable):
    performance = _score_all(not_applicable, ops["manager"],
                             {**MANAGER_SCORES, "TIMELINESS": "85"})
    timeliness = _scores(performance)["TIMELINESS"]
    assert (timeliness.score, timeliness.source) == (Decimal("85.00"), "MANAGER")
    override = AuditLog.objects.get(action="performance.kpi_score_updated",
                                    context__kpi="TIMELINESS")
    assert override.context["system_not_applicable_override"] is True
    assert override.old_value["source"] == "SYSTEM" and override.new_value["source"] == "MANAGER"
    accuracy = AuditLog.objects.get(action="performance.kpi_score_updated", context__kpi="ACCURACY")
    assert accuracy.context["system_not_applicable_override"] is False
    final = _submit_and_finalize(performance, ops["manager"])
    # 92x3 + 85x2 + 90x1.5 + 94x1.5 + 96x1 + 98x1 = 916 / 10; the weights are not renormalised
    assert final.status == "FINALIZED" and final.overall_score == Decimal("91.60")


def test_override_stays_while_not_applicable_and_yields_once_measurable(ops, not_applicable,
                                                                        work, sla_24h, ist):
    _score_all(not_applicable, ops["manager"], {"TIMELINESS": "85"})
    with time_machine.travel(ist(2026, 11, 6, 9, 0), tick=False):
        still = services.calculate_monthly_performance(
            actor=ops["manager"], employee=ops["amit_emp"], year=2026, month=10
        )
    assert (_scores(still)["TIMELINESS"].score, _scores(still)["TIMELINESS"].source) == (
        Decimal("85.00"), "MANAGER",
    )
    task = work.raise_task(ops["amit_emp"], 2026, 10, 10, 10, 0)
    work.complete(task, ops["amit"], 2026, 10, 10, 11, 0)  # on time: Timeliness is measurable
    with time_machine.travel(ist(2026, 11, 7, 9, 0), tick=False):
        measured = services.calculate_monthly_performance(
            actor=ops["manager"], employee=ops["amit_emp"], year=2026, month=10
        )
    timeliness = _scores(measured)["TIMELINESS"]
    assert (timeliness.score, timeliness.source) == (Decimal("100.00"), "SYSTEM")
    assert timeliness.manager_remark == "" and timeliness.reviewed_by is None
    with pytest.raises(FieldValidationError):  # measurable again: the system owns it
        services.update_kpi_score(actor=ops["manager"], performance=measured,
                                  version=measured.version, kpi_code="TIMELINESS", score="70")
