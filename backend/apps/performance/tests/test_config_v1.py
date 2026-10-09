"""Phase 7.1 KRA configuration: seed, versioning, draft-only editing, activation validation,
legacy preservation, deductions, audit and API permissions. Nothing here calculates a month."""

import importlib
from datetime import date, datetime
from decimal import Decimal
from io import StringIO

import pytest
import time_machine
from django.core.management import call_command
from django.db import connection
from django.db.migrations.loader import MigrationLoader

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.core.errors import FieldValidationError
from apps.core.timeutils import IST
from apps.performance import config_services as svc
from apps.performance import services
from apps.performance.models import (
    KPI,
    AppendOnly,
    Band,
    BandScheme,
    ConfigurationLocked,
    DeductionRule,
    EmployeeKPIAssignment,
    KPIComponent,
    KPIWeight,
    KPIWeightVersion,
    ManualTaskOverride,
    MonthlyKPIScore,
    MonthlyPerformance,
    ScoreAdjustment,
    ScoringRule,
    ScoringRuleStep,
)
from apps.recurring.models import Responsibility
from apps.tasks import services as task_services

pytestmark = pytest.mark.django_db
PLANS = "/api/v1/performance/plans/"
RESPONSIBILITIES = ["FEED_UPLOAD", "MAIL_CHECKING", "SIP_STP_SWITCH_CHECKING",
                    "BROKERAGE_CALCULATION"]


@pytest.fixture(autouse=True)
def frozen_today():
    """Thursday 8 Oct 2026 (IST): the seeded drafts start on 1 Nov 2026, in the future."""
    with time_machine.travel(datetime(2026, 10, 8, 12, 0, tzinfo=IST), tick=False):
        yield


@pytest.fixture
def hr(make_user):
    return make_user(roles.HR)


def _kra_plan(version=1) -> KPIWeightVersion:
    return KPIWeightVersion.objects.get(configuration="OPERATIONS_KRA", version=version)


def _legacy_v1() -> KPIWeightVersion:
    return KPIWeightVersion.objects.get(configuration="OPERATIONS", version=1)


def _activate_seeded_rules(admin):
    svc.activate_scoring_rule(
        actor=admin, rule=ScoringRule.objects.get(code="KRA_BENCHMARK", version=1)
    )
    svc.activate_band_scheme(
        actor=admin, scheme=BandScheme.objects.get(code="KRA_BANDS", version=1)
    )


def _complete(plan, hr):
    """Fill in everything the seed leaves for HR: one component per line, the deduction
    scope / stacking / cap / priority / ceiling, and the stacking method."""
    for index, line in enumerate(plan.weights.order_by("position")):
        if index < len(RESPONSIBILITIES):
            svc.create_component(
                actor=hr, line=line, source_type="RESPONSIBILITY_TASKS",
                responsibility=Responsibility.objects.get(code=RESPONSIBILITIES[index]),
                task_scope="SCHEDULED", verification_policy="NOT_REQUIRED",
            )
        else:
            svc.create_component(actor=hr, line=line, source_type="MANUAL_ENTRY",
                                 label=f"HR assessment {index}")
    ceiling = Band.objects.get(scheme=plan.band_scheme, name="Needs Improvement")
    for priority, rule in enumerate(plan.deduction_rules.order_by("code"), start=1):
        changes = {"scope": "OVERALL", "stacking": "STACK", "uncapped": True,
                   "priority": priority}
        if rule.kind == "BAND_CEILING":
            changes["ceiling_band"] = ceiling
        svc.update_deduction_rule(actor=hr, rule=rule, **changes)
    svc.update_plan_version(actor=hr, version=plan, deduction_stacking_method="ADDITIVE")
    plan.refresh_from_db()  # the service saved a fresh, locked copy


def _active_kra_plan(admin, hr) -> KPIWeightVersion:
    _activate_seeded_rules(admin)
    plan = _kra_plan()
    _complete(plan, hr)
    return svc.activate_plan_version(actor=admin, version=plan)


def _problems(excinfo) -> str:
    return "\n".join(excinfo.value.fields["activation"])


# --- seed and legacy preservation -----------------------------------------------------------


def test_legacy_operations_v1_is_untouched():
    v1 = _legacy_v1()
    assert (v1.calculation_model, v1.status, v1.effective_to) == ("LEGACY_WEIGHTED", "ACTIVE", None)
    assert v1.weights.count() == 6
    assert not EmployeeKPIAssignment.objects.exists()
    names = dict(KPI.objects.values_list("code", "name"))
    assert names == {  # the KPI master is never renamed
        "ACCURACY": "Accuracy", "TIMELINESS": "Timeliness", "CLIENT_SERVICING": "Client Servicing",
        "FINANCIAL_ACCURACY": "Financial Accuracy", "DATA_SYSTEM": "Data/System",
        "COMPLIANCE": "Compliance",
    }
    assert not MonthlyPerformance.objects.exists()


def test_kra_v1_is_seeded_as_a_complete_looking_but_inactive_draft():
    plan = _kra_plan()
    assert (plan.calculation_model, plan.status) == ("KRA_POINTS", "DRAFT")
    assert plan.effective_from == date(2026, 11, 1)
    assert (plan.credit_on_time, plan.credit_late, plan.credit_overdue) == (
        Decimal("1.00"), Decimal("0.25"), Decimal("0.00"),
    )
    lines = [(w.kpi.code, w.label, w.weight, w.scoring_rule.code)
             for w in plan.weights.select_related("kpi", "scoring_rule").order_by("position")]
    assert lines == [
        ("ACCURACY", "Accuracy", Decimal("3.00"), "KRA_BENCHMARK"),
        ("TIMELINESS", "Timeliness & Task Discipline", Decimal("2.00"), "KRA_BENCHMARK"),
        ("CLIENT_SERVICING", "Client Servicing", Decimal("1.50"), "KRA_BENCHMARK"),
        ("FINANCIAL_ACCURACY", "Financial Accuracy", Decimal("1.50"), "KRA_BENCHMARK"),
        ("DATA_SYSTEM", "Data & System Management", Decimal("1.00"), "KRA_BENCHMARK"),
        ("COMPLIANCE", "Compliance & Documentation", Decimal("1.00"), "KRA_BENCHMARK"),
    ]
    assert sum(w for *_, w, _ in lines) == Decimal("10.00")
    assert not KPIComponent.objects.exists()  # HR maps responsibilities

    rule = ScoringRule.objects.get(code="KRA_BENCHMARK", version=1)
    assert rule.status == "DRAFT" and rule.below_min_score_pct == Decimal("0")
    assert [(s.min_achievement_pct, s.score_pct) for s in rule.steps.all()] == [
        (Decimal("100"), Decimal("100")), (Decimal("85"), Decimal("80")),
        (Decimal("70"), Decimal("60")), (Decimal("50"), Decimal("40")),
    ]
    scheme = plan.band_scheme
    assert (scheme.code, scheme.version, scheme.status) == ("KRA_BANDS", 1, "DRAFT")
    assert [(b.name, b.min_points) for b in scheme.bands.all()] == [
        ("High Performer", Decimal("9.00")), ("Consistent Performer", Decimal("7.50")),
        ("Needs Improvement", Decimal("6.00")), ("Performance Concern", Decimal("0.00")),
    ]


def test_only_the_kra_defined_deductions_are_seeded_and_left_incomplete():
    rules = {r.code: r for r in _kra_plan().deduction_rules.all()}
    assert {code: (r.kind, r.min_pct, r.max_pct) for code, r in rules.items()} == {
        "MISSED_RECONCILIATION": ("PERCENT_RANGE", Decimal("20"), Decimal("50")),
        "DELAYED_SYSTEM_UPDATE": ("PERCENT_RANGE", Decimal("10"), Decimal("30")),
        "SERVICE_DELAY_BEYOND_TAT": ("PERCENT_RANGE", Decimal("10"), Decimal("25")),
        "DATA_INCONSISTENCY": ("PERCENT_RANGE", Decimal("20"), Decimal("40")),
        "TRANSACTION_ERROR": ("BAND_CEILING", None, None),
        "REVENUE_LEAKAGE": ("BAND_CEILING", None, None),
    }
    for rule in rules.values():  # nothing invented: HR decides
        assert (rule.scope, rule.stacking, rule.cap_pct, rule.uncapped, rule.priority,
                rule.ceiling_band) == ("", "", None, False, None, None)
    assert _kra_plan().deduction_stacking_method == ""


def test_seed_is_idempotent():
    migration = importlib.import_module("apps.performance.migrations.0004_kra_configuration_seed")
    state = MigrationLoader(connection).project_state(
        ("performance", "0004_kra_configuration_seed")
    )

    def counts():
        return (KPIWeightVersion.objects.count(), KPIWeight.objects.count(),
                DeductionRule.objects.count(), ScoringRule.objects.count(),
                ScoringRuleStep.objects.count(), BandScheme.objects.count(), Band.objects.count())

    before = counts()
    migration.seed(state.apps, None)
    assert counts() == before


def test_legacy_engine_never_activates_or_scores_a_kra_plan(admin_user, hr, ops):
    plan = _active_kra_plan(admin_user, hr)
    draft = svc.clone_plan_version(actor=hr, version=plan)
    with pytest.raises(services.PerformanceError):
        services.activate_weight_version(actor=admin_user, version=draft)
    svc.create_override(actor=hr, employee=ops["rahul_emp"], version=plan,
                        effective_from=date(2026, 11, 1), reason="KRA pilot")
    with pytest.raises(services.CalculationModelNotSupported):
        services.calculate_monthly_performance(
            actor=admin_user, employee=ops["rahul_emp"], year=2026, month=11
        )
    out = StringIO()
    call_command("calculate_performance", "--year", "2026", "--month", "11",
                 "--employee", str(ops["rahul_emp"].pk), stdout=out)
    # Phase 7.2: the command now calculates KRA months; November has not started on 8 Oct.
    assert "Skipped (month has not started)" in out.getvalue()
    assert not MonthlyPerformance.objects.exists()


def test_kra_plans_never_join_a_legacy_family(hr):
    with pytest.raises(svc.ConfigConflict) as excinfo:
        svc.create_plan_version(actor=hr, configuration="operations", name="x",
                                effective_from=date(2026, 12, 1))
    assert excinfo.value.code == "legacy_configuration"
    with pytest.raises(svc.ConfigConflict):
        svc.update_plan_version(actor=hr, version=_legacy_v1(), name="Renamed")
    created = svc.create_plan_version(actor=hr, configuration="hr_kra", name="HR KRA",
                                      effective_from=date(2026, 12, 1))
    assert (created.configuration, created.version, created.status) == ("HR_KRA", 1, "DRAFT")
    assert (created.credit_on_time, created.credit_late, created.credit_overdue) == (
        Decimal("1.00"), Decimal("0.25"), Decimal("0.00"),
    )


def test_admin_retires_legacy_v1_with_a_reason_and_audit(admin_user):
    """Approved P13: an audited Admin action, not a migration."""
    with pytest.raises(FieldValidationError):
        svc.retire_plan_version(actor=admin_user, version=_legacy_v1(),
                                last_day=date(2026, 10, 31), reason="  ")
    retired = svc.retire_plan_version(actor=admin_user, version=_legacy_v1(),
                                      last_day=date(2026, 10, 31), reason="Replaced by KRA")
    assert (retired.status, retired.effective_to, retired.retire_reason) == (
        "RETIRED", date(2026, 10, 31), "Replaced by KRA",
    )
    assert retired.weights.count() == 6  # its lines never change
    row = AuditLog.objects.get(action="performance.config.plan_retired")
    assert row.actor_user == admin_user and row.context["calculation_model"] == "LEGACY_WEIGHTED"
    with pytest.raises(svc.ConfigConflict):
        svc.retire_plan_version(actor=admin_user, version=retired,
                                last_day=date(2026, 10, 30), reason="again")


# --- activation validation ------------------------------------------------------------------


def test_the_seeded_draft_cannot_be_activated_and_every_gap_is_listed(admin_user):
    with pytest.raises(FieldValidationError) as excinfo:
        svc.activate_plan_version(actor=admin_user, version=_kra_plan())
    problems = _problems(excinfo)
    for text in (
        "Band scheme KRA_BANDS v1 must be ACTIVE",
        "Accuracy: scoring rule KRA_BENCHMARK v1 must be ACTIVE",
        "Timeliness & Task Discipline: add at least one component.",
        "Deduction MISSED_RECONCILIATION: choose the scope.",
        "Deduction MISSED_RECONCILIATION: choose stacking or non-stacking.",
        "Deduction MISSED_RECONCILIATION: set the priority.",
        "Deduction MISSED_RECONCILIATION: set a cap or mark it uncapped.",
        "Deduction TRANSACTION_ERROR: choose the ceiling band.",
        "Deduction REVENUE_LEAKAGE: choose the ceiling band.",
        "deduction stacking method",
    ):
        assert text in problems, text
    assert _kra_plan().status == "DRAFT"  # never activated automatically


def test_a_complete_plan_activates_and_is_then_immutable(admin_user, hr):
    plan = _kra_plan()
    _activate_seeded_rules(admin_user)
    _complete(plan, hr)
    assert svc.plan_activation_problems(plan) == []
    with pytest.raises(svc.ConfigConflict):  # wrong role is the API's job; wrong state is ours
        svc.activate_plan_version(actor=admin_user, version=_legacy_v1())
    active = svc.activate_plan_version(actor=admin_user, version=plan)
    assert (active.status, active.activated_by) == ("ACTIVE", admin_user)

    line = active.weights.order_by("position").first()
    with pytest.raises(ConfigurationLocked):
        svc.update_plan_version(actor=hr, version=active, name="Changed")
    with pytest.raises(ConfigurationLocked):
        svc.update_plan_line(actor=hr, line=line, weight=Decimal("2.00"))
    with pytest.raises(ConfigurationLocked):
        svc.create_component(actor=hr, line=line, source_type="MANUAL_ENTRY", label="Extra")
    with pytest.raises(ConfigurationLocked):
        svc.delete_plan_version(actor=hr, version=active)
    # the model refuses too, whatever the caller
    with pytest.raises(ConfigurationLocked):
        line.save()
    with pytest.raises(ConfigurationLocked):
        line.components.first().save()
    with pytest.raises(ConfigurationLocked):
        active.deduction_rules.first().delete()
    with pytest.raises(ConfigurationLocked):
        KPIWeightVersion.objects.get(pk=active.pk).save()
    rule = ScoringRule.objects.get(code="KRA_BENCHMARK", version=1)
    with pytest.raises(ConfigurationLocked):
        rule.save()
    with pytest.raises(ConfigurationLocked):
        rule.steps.first().save()
    with pytest.raises(ConfigurationLocked):
        Band.objects.filter(scheme__code="KRA_BANDS").first().save()
    _legacy_v1().weights.first().save()  # legacy rows keep their original (service) rules

    actions = set(AuditLog.objects.filter(action__startswith="performance.config.")
                  .values_list("action", flat=True))
    assert {
        "performance.config.scoring_rule_activated", "performance.config.band_scheme_activated",
        "performance.config.component_created", "performance.config.deduction_rule_updated",
        "performance.config.plan_updated", "performance.config.plan_activated",
    } <= actions


def test_activation_needs_a_future_date_and_exactly_ten_points(admin_user, hr):
    _activate_seeded_rules(admin_user)
    plan = _kra_plan()
    _complete(plan, hr)
    accuracy = plan.weights.get(kpi__code="ACCURACY")
    svc.update_plan_line(actor=hr, line=accuracy, weight=Decimal("2.50"))
    svc.update_plan_version(actor=hr, version=plan, effective_from=date(2026, 10, 8))
    with pytest.raises(FieldValidationError) as excinfo:
        svc.activate_plan_version(actor=admin_user, version=plan)
    problems = _problems(excinfo)
    assert "The effective date must be in the future." in problems
    assert "Line weights must total exactly 10.00 (they total 9.50)." in problems


def test_clone_copies_everything_and_overlapping_active_versions_are_refused(admin_user, hr):
    active = _active_kra_plan(admin_user, hr)
    copy = svc.clone_plan_version(actor=hr, version=active)
    assert (copy.version, copy.status, copy.calculation_model) == (2, "DRAFT", "KRA_POINTS")
    assert copy.weights.count() == 6
    assert KPIComponent.objects.filter(plan_line__weight_version=copy).count() == 6
    def rules(version):
        return [(r.code, r.priority, r.ceiling_band_id)
                for r in version.deduction_rules.order_by("code")]

    assert rules(copy) == rules(active)
    with pytest.raises(FieldValidationError) as excinfo:
        svc.activate_plan_version(actor=admin_user, version=copy)
    assert "OPERATIONS_KRA v1 is ACTIVE for part of this period" in _problems(excinfo)

    svc.retire_plan_version(actor=admin_user, version=active, last_day=date(2026, 11, 30),
                            reason="December changes")
    svc.update_plan_version(actor=hr, version=copy, effective_from=date(2026, 12, 1))
    assert svc.activate_plan_version(actor=admin_user, version=copy).status == "ACTIVE"
    active.refresh_from_db()
    assert active.weights.get(kpi__code="ACCURACY").weight == Decimal("3.00")  # history kept


# --- components: automatic task identification is configuration ------------------------------


def test_component_rules(hr, admin_user):
    plan = _kra_plan()
    line = plan.weights.get(kpi__code="TIMELINESS")
    feed = Responsibility.objects.get(code="FEED_UPLOAD")
    with pytest.raises(FieldValidationError):  # scheduled-only does not match manual tasks
        svc.create_component(actor=hr, line=line, source_type="RESPONSIBILITY_TASKS",
                             responsibility=feed, task_scope="SCHEDULED", manual_match="CATEGORY")
    with pytest.raises(FieldValidationError):  # a manual entry needs a name
        svc.create_component(actor=hr, line=line, source_type="MANUAL_ENTRY")
    with pytest.raises(FieldValidationError):  # and has no responsibility
        svc.create_component(actor=hr, line=line, source_type="MANUAL_ENTRY", label="x",
                             responsibility=feed)
    untyped = Responsibility.objects.create(
        code="NO_TYPE", name="No task type", department=feed.department, category=feed.category
    )
    with pytest.raises(FieldValidationError):  # TASK_TYPE needs a task type to match
        svc.create_component(actor=hr, line=line, source_type="RESPONSIBILITY_TASKS",
                             responsibility=untyped, task_scope="MANUAL", manual_match="TASK_TYPE")
    component = svc.create_component(actor=hr, line=line, source_type="RESPONSIBILITY_TASKS",
                                     responsibility=feed, task_scope="BOTH")
    assert component.contribution_share == Decimal("1")  # equal by default
    with pytest.raises(svc.ConfigConflict):
        svc.create_component(actor=hr, line=line, source_type="RESPONSIBILITY_TASKS",
                             responsibility=feed)
    with pytest.raises(FieldValidationError) as excinfo:
        svc.activate_plan_version(actor=admin_user, version=plan)
    assert "Feed Upload: choose how manual tasks are matched." in _problems(excinfo)
    assert "Feed Upload: choose the verification policy." in _problems(excinfo)

    svc.update_component(actor=hr, component=component, manual_match="CATEGORY",
                         verification_policy="REQUIRED", contribution_share=Decimal("2"))
    svc.create_component(actor=hr, line=line, source_type="RESPONSIBILITY_TASKS",
                         responsibility=Responsibility.objects.get(code="MAIL_CHECKING"),
                         task_scope="MANUAL", manual_match="CATEGORY",
                         verification_policy="NOT_REQUIRED")
    with pytest.raises(FieldValidationError) as excinfo:
        svc.activate_plan_version(actor=admin_user, version=plan)
    assert "match manual tasks by the same category and department" in _problems(excinfo)
    actions = AuditLog.objects.filter(action__startswith="performance.config.component_")
    assert actions.count() == 3


def test_manual_task_override_is_an_exception_record_removed_with_its_task(
    admin_user, hr, ops, new_task
):
    task = new_task(ops["manager"], ops["rahul_emp"], title="Ad-hoc feed fix")
    feed = Responsibility.objects.get(code="FEED_UPLOAD")
    ManualTaskOverride.objects.create(task=task, responsibility=feed, action="INCLUDE",
                                      reason="Feed correction raised by hand", created_by=hr)
    task.refresh_from_db()
    task_services.delete_task(actor=admin_user, task=task, version=task.version)
    assert not ManualTaskOverride.objects.exists()


# --- deductions -------------------------------------------------------------------------------


def test_deduction_rules_are_configured_not_invented(hr, admin_user):
    plan = _kra_plan()
    other = svc.create_band_scheme(actor=hr, code="OTHER_BANDS", name="Other",
                                   effective_from=date(2026, 11, 1),
                                   bands=[{"name": "Any", "min_points": "0"}])
    bad = [
        {"code": "LOW_HIGH", "name": "x", "kind": "PERCENT_RANGE", "min_pct": "40",
         "max_pct": "20"},
        {"code": "NO_RANGE", "name": "x", "kind": "PERCENT_RANGE"},
        {"code": "CEILING_RANGE", "name": "x", "kind": "BAND_CEILING", "min_pct": "10",
         "max_pct": "20"},
        {"code": "FOREIGN_BAND", "name": "x", "kind": "BAND_CEILING",
         "ceiling_band": other.bands.get()},
        {"code": "CAP_AND_UNCAPPED", "name": "x", "kind": "PERCENT_RANGE", "min_pct": "1",
         "max_pct": "2", "cap_pct": "50", "uncapped": True},
        {"code": "UNKNOWN_SCOPE", "name": "x", "kind": "PERCENT_RANGE", "min_pct": "1",
         "max_pct": "2", "scope": "EVERYTHING"},
    ]
    for values in bad:
        with pytest.raises(FieldValidationError):
            svc.create_deduction_rule(actor=hr, version=plan, **values)
    with pytest.raises(svc.ConfigConflict):
        svc.create_deduction_rule(actor=hr, version=plan, code="MISSED_RECONCILIATION",
                                  name="dup", kind="PERCENT_RANGE", min_pct="1", max_pct="2")
    draft = svc.create_deduction_rule(actor=hr, version=plan, code="extra_rule", name="Extra",
                                      kind="PERCENT_RANGE", min_pct="5", max_pct="15")
    assert (draft.code, draft.scope, draft.priority) == ("EXTRA_RULE", "", None)  # incomplete

    _activate_seeded_rules(admin_user)
    _complete(plan, hr)
    draft.refresh_from_db()  # _complete() gave it a priority and marked it uncapped
    svc.update_deduction_rule(actor=hr, rule=draft, scope="KPI", stacking="NON_STACKING",
                              cap_pct="30", uncapped=False, priority=1)  # 1 is taken
    with pytest.raises(FieldValidationError) as excinfo:
        svc.activate_plan_version(actor=admin_user, version=plan)
    assert "Deduction EXTRA_RULE: priority 1 is also used by" in _problems(excinfo)
    svc.delete_deduction_rule(actor=hr, rule=draft)
    assert svc.activate_plan_version(actor=admin_user, version=plan).status == "ACTIVE"


# --- scoring rules and band schemes -------------------------------------------------------------


def test_scoring_rules_and_band_schemes_are_versioned(hr, admin_user):
    v2 = svc.create_scoring_rule(actor=hr, code="KRA_BENCHMARK", name="KRA benchmark 2027",
                                 effective_from=date(2027, 1, 1), below_min_score_pct="10",
                                 steps=[{"min_achievement_pct": "90", "score_pct": "100"}])
    assert (v2.version, v2.status, v2.below_min_score_pct) == (2, "DRAFT", Decimal("10"))
    svc.update_scoring_rule(actor=hr, rule=v2, steps=[], below_min_score_pct=None)
    with pytest.raises(FieldValidationError) as excinfo:
        svc.activate_scoring_rule(actor=admin_user, rule=v2)
    assert "Add the benchmark steps." in _problems(excinfo)
    assert "Set the score below the lowest step." in _problems(excinfo)
    with pytest.raises(FieldValidationError):  # duplicate minimum
        svc.update_scoring_rule(actor=hr, rule=v2, steps=[
            {"min_achievement_pct": "50", "score_pct": "40"},
            {"min_achievement_pct": "50", "score_pct": "60"},
        ])

    scheme = svc.create_band_scheme(actor=hr, code="KRA_BANDS", name="No floor",
                                    effective_from=date(2027, 1, 1),
                                    bands=[{"name": "Top", "min_points": "9"}])
    assert scheme.version == 2
    with pytest.raises(FieldValidationError) as excinfo:
        svc.activate_band_scheme(actor=admin_user, scheme=scheme)
    assert "One band must start at 0" in _problems(excinfo)

    active = _active_kra_plan(admin_user, hr)  # uses KRA_BENCHMARK v1 / KRA_BANDS v1
    with pytest.raises(svc.ConfigConflict):
        svc.retire_scoring_rule(actor=admin_user, rule=active.weights.first().scoring_rule,
                                last_day=date(2026, 12, 31), reason="in use")
    with pytest.raises(svc.ConfigConflict):
        svc.retire_band_scheme(actor=admin_user, scheme=active.band_scheme,
                               last_day=date(2026, 12, 31), reason="in use")
    copy = svc.clone_scoring_rule(actor=hr, rule=active.weights.first().scoring_rule)
    assert (copy.version, copy.steps.count()) == (3, 4)


# --- overrides and finalized history -----------------------------------------------------------


def test_overrides_need_a_reason_an_active_plan_and_never_touch_finalized_months(
    admin_user, hr, ops
):
    with pytest.raises(svc.ConfigConflict):  # a draft cannot be assigned
        svc.create_override(actor=hr, employee=ops["rahul_emp"], version=_kra_plan(),
                            effective_from=date(2026, 11, 1), reason="x")
    with pytest.raises(svc.ConfigConflict):  # legacy versions keep the legacy assignment path
        svc.create_override(actor=hr, employee=ops["rahul_emp"], version=_legacy_v1(),
                            effective_from=date(2026, 11, 1), reason="x")
    plan = _active_kra_plan(admin_user, hr)
    with pytest.raises(FieldValidationError):
        svc.create_override(actor=hr, employee=ops["rahul_emp"], version=plan,
                            effective_from=date(2026, 11, 1), reason=" ")
    MonthlyPerformance.objects.create(
        employee=ops["amit_emp"], year=2026, month=11, period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 30), status="FINALIZED",
    )
    with pytest.raises(svc.ConfigConflict):
        svc.create_override(actor=hr, employee=ops["amit_emp"], version=plan,
                            effective_from=date(2026, 11, 1), reason="Moves to KRA")
    row = svc.create_override(actor=hr, employee=ops["rahul_emp"], version=plan,
                              effective_from=date(2026, 11, 1), reason="Moves to KRA")
    assert row.reason == "Moves to KRA" and row.assigned_by == hr
    ended = svc.end_override(actor=hr, assignment=row, last_day=date(2026, 12, 31))
    assert ended.effective_to == date(2026, 12, 31)
    assert AuditLog.objects.filter(action__in=["performance.config.override_created",
                                               "performance.config.override_ended"]).count() == 2


# --- N/A and result structures (stored only; no calculation in 7.1) ---------------------------


def test_na_and_applicable_maximum_are_stored_without_rescaling(ops):
    record = MonthlyPerformance.objects.create(
        employee=ops["rahul_emp"], year=2026, month=11, period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 30), calculation_model="KRA_POINTS",
        max_points_applicable=Decimal("8.5"), final_total=Decimal("7.2"),
    )
    kpi = KPI.objects.get(code="CLIENT_SERVICING")
    score = MonthlyKPIScore.objects.create(
        monthly_performance=record, kpi=kpi, weight=Decimal("1.50"), source="SYSTEM",
        not_applicable=True, na_reason="No client-servicing work this month",
        auto_points=Decimal("0"), final_points=Decimal("0"),
    )
    record.refresh_from_db()
    score.refresh_from_db()
    assert (record.max_points_applicable, record.final_total) == (Decimal("8.5"), Decimal("7.2"))
    assert score.not_applicable and score.score is None  # legacy column untouched
    assert (record.overall_score, record.performance_band) == (None, "")


def test_adjustments_are_append_only(ops, hr):
    record = MonthlyPerformance.objects.create(
        employee=ops["rahul_emp"], year=2026, month=11, period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 30), calculation_model="KRA_POINTS",
    )
    score = MonthlyKPIScore.objects.create(
        monthly_performance=record, kpi=KPI.objects.get(code="ACCURACY"), weight=Decimal("3"),
        source="SYSTEM",
    )
    adjustment = ScoreAdjustment.objects.create(
        kpi_score=score, before_points=Decimal("2"), adjustment_points=Decimal("0.5"),
        after_points=Decimal("2.5"), reason="Evidence reviewed", actor=hr,
    )
    with pytest.raises(AppendOnly):
        adjustment.save()
    with pytest.raises(AppendOnly):
        adjustment.delete()


# --- API ----------------------------------------------------------------------------------------


def test_api_who_may_read_prepare_and_approve(client_for, make_user, api_client, ops):
    plan = _kra_plan()
    hr_client = client_for(make_user(roles.HR))
    admin = client_for(make_user(roles.ADMIN))
    for role in (roles.OPERATIONS_MANAGER, roles.EMPLOYEE):
        client = client_for(make_user(role))
        assert client.get(PLANS).status_code == 403
        assert client.get(f"{PLANS}{plan.pk}/").status_code == 403
    assert api_client.get(PLANS).status_code == 401

    listed = {(p["configuration"], p["version"]): p for p in hr_client.get(PLANS).json()}
    assert listed[("OPERATIONS", 1)]["calculation_model"] == "LEGACY_WEIGHTED"
    assert listed[("OPERATIONS_KRA", 1)]["status"] == "DRAFT"
    detail = admin.get(f"{PLANS}{plan.pk}/").json()
    assert [line["name"] for line in detail["lines"]][:2] == [
        "Accuracy", "Timeliness & Task Discipline",
    ]
    assert len(detail["deduction_rules"]) == 6

    assert admin.post(PLANS, {"configuration": "X_KRA", "name": "x",
                              "effective_from": "2026-12-01"}).status_code == 403
    created = hr_client.post(PLANS, {"configuration": "x_kra", "name": "X",
                                     "effective_from": "2026-12-01"})
    assert created.status_code == 201 and created.json()["status"] == "DRAFT"
    assert hr_client.post(f"{PLANS}{plan.pk}/activate/").status_code == 403
    refused = admin.post(f"{PLANS}{plan.pk}/activate/")
    assert refused.status_code == 400 and refused.json()["fields"]["activation"]
    line = plan.weights.get(kpi__code="TIMELINESS")
    component = hr_client.post(
        f"/api/v1/performance/plan-lines/{line.pk}/components/",
        {"source_type": "RESPONSIBILITY_TASKS",
         "responsibility": Responsibility.objects.get(code="FEED_UPLOAD").pk,
         "task_scope": "BOTH", "manual_match": "TASK_TYPE",
         "verification_policy": "REQUIRED"},
    )
    assert component.status_code == 201, component.content
    assert component.json()["manual_match"] == "TASK_TYPE"
    patched = hr_client.patch(f"{PLANS}{plan.pk}/", {"deduction_stacking_method": "SEQUENTIAL"})
    assert patched.status_code == 200
    assert patched.json()["deduction_stacking_method"] == "SEQUENTIAL"
    assert hr_client.patch(f"{PLANS}{plan.pk}/", {"deduction_stacking_method": "MAGIC"}) \
        .status_code == 400
    kpis = hr_client.get("/api/v1/performance/kpis/").json()
    assert {k["code"] for k in kpis} >= {"ACCURACY", "TIMELINESS"}
