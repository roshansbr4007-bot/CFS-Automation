"""KPI master, versioned weights (total 10.0), effective periods and employee assignment."""

from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.audit.models import AuditLog
from apps.core.errors import FieldValidationError
from apps.performance import services
from apps.performance.models import (
    KPI,
    EmployeeKPIAssignment,
    KPIWeight,
    KPIWeightVersion,
    MonthlyKPIScore,
    MonthlyPerformance,
)

pytestmark = pytest.mark.django_db
OPS_WEIGHTS = {"ACCURACY": "3.0", "TIMELINESS": "2.0", "CLIENT_SERVICING": "1.5",
               "FINANCIAL_ACCURACY": "1.5", "DATA_SYSTEM": "1.0", "COMPLIANCE": "1.0"}


def test_kpi_creation_seeds_the_approved_operations_kpis(ops_version):
    sources = dict(KPI.objects.values_list("code", "score_source"))
    assert sources == {
        "ACCURACY": "MANAGER", "TIMELINESS": "SYSTEM", "CLIENT_SERVICING": "MANAGER",
        "FINANCIAL_ACCURACY": "MANAGER", "DATA_SYSTEM": "MANAGER", "COMPLIANCE": "MANAGER",
    }
    weights = {w.kpi.code: w.weight for w in ops_version.weights.select_related("kpi")}
    expected = {code: Decimal(v).quantize(Decimal("0.01")) for code, v in OPS_WEIGHTS.items()}
    assert weights == expected
    assert sum(weights.values()) == Decimal("10.00")
    assert ops_version.status == "ACTIVE" and ops_version.effective_to is None
    assert not EmployeeKPIAssignment.objects.exists()  # approved P6: assigned to nobody
    assert str(KPI.objects.get(code="ACCURACY")) == "Accuracy"
    assert str(ops_version) == "OPERATIONS v1"


def test_kpi_validation_at_the_database(ops, ops_version):
    accuracy = KPI.objects.get(code="ACCURACY")
    with pytest.raises(IntegrityError), transaction.atomic():
        KPIWeight.objects.create(weight_version=ops_version, kpi=accuracy, weight=Decimal("-1"))
    performance = MonthlyPerformance.objects.create(
        employee=ops["rahul_emp"], year=2026, month=10,
        period_start=date(2026, 10, 1), period_end=date(2026, 10, 31),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        MonthlyKPIScore.objects.create(
            monthly_performance=performance, kpi=accuracy, weight=Decimal("3"),
            score=Decimal("100.01"), source="MANAGER",
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        MonthlyPerformance.objects.create(
            employee=ops["amit_emp"], year=2026, month=13,
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        )


def test_weight_validation_in_the_service(admin_user):
    with pytest.raises(FieldValidationError):
        services.create_weight_version(actor=admin_user, configuration="HR", name="HR",
                                       effective_from=date(2026, 1, 1),
                                       weights={"ACCURACY": "-0.5"})
    with pytest.raises(FieldValidationError):
        services.create_weight_version(actor=admin_user, configuration="HR", name="HR",
                                       effective_from=date(2026, 1, 1), weights={"NOPE": "1"})
    with pytest.raises(FieldValidationError):
        services.create_weight_version(actor=admin_user, configuration="", name="x",
                                       effective_from=date(2026, 1, 1), weights={})
    with pytest.raises(FieldValidationError):
        services.create_weight_version(actor=admin_user, configuration="HR", name="HR",
                                       effective_from=date(2026, 2, 1),
                                       effective_to=date(2026, 1, 1), weights={})


def test_weight_total_must_be_exactly_10_to_activate(admin_user):
    draft = services.create_weight_version(
        actor=admin_user, configuration="HR", name="HR", effective_from=date(2026, 1, 1),
        weights={**OPS_WEIGHTS, "ACCURACY": "2.5"},  # total 9.5
    )
    with pytest.raises(FieldValidationError):
        services.activate_weight_version(actor=admin_user, version=draft)
    draft.refresh_from_db()
    assert draft.status == "DRAFT"
    good = services.create_weight_version(actor=admin_user, configuration="HR", name="HR",
                                          effective_from=date(2026, 1, 1), weights=OPS_WEIGHTS)
    assert good.version == 2  # numbering continues per configuration
    assert services.activate_weight_version(actor=admin_user, version=good).status == "ACTIVE"
    with pytest.raises(services.PerformanceError):
        services.activate_weight_version(actor=admin_user, version=good)  # not a draft any more
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {"performance.weight_version_created", "performance.weight_version_activated"} <= actions


def test_kpi_versioning_and_effective_periods(admin_user, ops_version):
    v2 = services.create_weight_version(
        actor=admin_user, configuration="OPERATIONS", name="Operations (Nov)",
        effective_from=date(2099, 11, 1), weights={**OPS_WEIGHTS, "ACCURACY": "2.5",
                                                   "TIMELINESS": "2.5"},
    )
    with pytest.raises(services.PerformanceError):  # v1 is open-ended: periods would overlap
        services.activate_weight_version(actor=admin_user, version=v2)
    with pytest.raises(FieldValidationError):
        services.end_weight_version(actor=admin_user, version=ops_version,
                                    last_day=ops_version.effective_from.replace(year=2000))
    services.end_weight_version(actor=admin_user, version=ops_version, last_day=date(2099, 10, 31))
    assert services.activate_weight_version(actor=admin_user, version=v2).status == "ACTIVE"
    with pytest.raises(services.PerformanceError):
        services.end_weight_version(actor=admin_user, version=ops_version,
                                    last_day=date(2099, 10, 30))
    ops_version.refresh_from_db()
    assert {w.kpi.code: w.weight for w in ops_version.weights.select_related("kpi")}["ACCURACY"] \
        == Decimal("3.00")  # the old version's weights never change
    assert KPIWeightVersion.objects.filter(configuration="OPERATIONS").count() == 2


def test_employee_assignment_periods(admin_user, ops, ops_version, assign):
    first = assign(ops["rahul_emp"], start=date(2026, 1, 1), end=date(2026, 9, 30))
    with pytest.raises(services.PerformanceError):
        assign(ops["rahul_emp"], start=date(2026, 9, 1))  # overlaps
    second = assign(ops["rahul_emp"], start=date(2026, 10, 1))
    assert services.assignment_for(ops["rahul_emp"], date(2026, 9, 30)) == first
    assert services.assignment_for(ops["rahul_emp"], date(2026, 10, 31)) == second
    assert services.assignment_for(ops["amit_emp"], date(2026, 10, 31)) is None
    services.end_kpi_assignment(actor=admin_user, assignment=second, last_day=date(2026, 12, 31))
    assert services.assignment_for(ops["rahul_emp"], date(2027, 1, 31)) is None
    with pytest.raises(services.PerformanceError):
        services.end_kpi_assignment(actor=admin_user, assignment=second, last_day=date(2027, 1, 1))
    draft = services.create_weight_version(actor=admin_user, configuration="OPS2", name="x",
                                           effective_from=date(2026, 1, 1), weights=OPS_WEIGHTS)
    with pytest.raises(services.PerformanceError):  # only active versions can be assigned
        assign(ops["amit_emp"], version=draft)
    with pytest.raises(FieldValidationError):
        assign(ops["amit_emp"], start=date(2026, 5, 1), end=date(2026, 4, 1))
    third = assign(ops["amit_emp"], start=date(2026, 6, 1))
    with pytest.raises(FieldValidationError):
        services.end_kpi_assignment(actor=admin_user, assignment=third, last_day=date(2026, 5, 1))
    assert str(first).startswith(f"{ops['rahul_emp'].pk} -> OPERATIONS v1")
    assert AuditLog.objects.filter(action="performance.kpi_assigned").count() == 3


# --- Stage A corrections ----------------------------------------------------------------------


def test_seed_is_deterministic(ops_version):
    assert ops_version.effective_from == date(2026, 1, 1)  # not the day migrations ran


def test_a_version_must_contain_every_active_kpi_exactly_once(admin_user):
    partial = services.create_weight_version(
        actor=admin_user, configuration="HR", name="HR", effective_from=date(2026, 1, 1),
        weights={"ACCURACY": "10"},  # totals 10.0 but leaves out five KPIs
    )
    with pytest.raises(FieldValidationError) as missing:
        services.activate_weight_version(actor=admin_user, version=partial)
    assert "missing" in str(missing.value.fields) and "TIMELINESS" in str(missing.value.fields)
    partial.refresh_from_db()
    assert partial.status == "DRAFT"
    KPI.objects.create(code="LEGACY", name="Legacy", score_source="MANAGER", is_active=False)
    with_inactive = services.create_weight_version(actor=admin_user, configuration="HR",
                                                   name="HR", effective_from=date(2026, 1, 1),
                                                   weights={**OPS_WEIGHTS, "LEGACY": "0"})
    with pytest.raises(FieldValidationError) as inactive:
        services.activate_weight_version(actor=admin_user, version=with_inactive)
    assert "inactive KPI: LEGACY" in str(inactive.value.fields)
    complete = services.create_weight_version(actor=admin_user, configuration="HR", name="HR",
                                              effective_from=date(2026, 1, 1), weights=OPS_WEIGHTS)
    # the inactive LEGACY KPI is not required; the six active ones are
    assert services.activate_weight_version(actor=admin_user, version=complete).status == "ACTIVE"


def test_assignment_must_lie_inside_the_version_period(admin_user, ops, assign):
    spring = services.create_weight_version(
        actor=admin_user, configuration="HR", name="HR spring", effective_from=date(2026, 3, 1),
        effective_to=date(2026, 6, 30), weights=OPS_WEIGHTS,
    )
    services.activate_weight_version(actor=admin_user, version=spring)
    amit = ops["amit_emp"]
    with pytest.raises(FieldValidationError):  # starts before the version
        assign(amit, version=spring, start=date(2026, 2, 1), end=date(2026, 4, 30))
    with pytest.raises(FieldValidationError):  # open-ended, but the version ends on 30 June
        assign(amit, version=spring, start=date(2026, 3, 1))
    with pytest.raises(FieldValidationError):  # ends after the version
        assign(amit, version=spring, start=date(2026, 3, 1), end=date(2026, 7, 31))
    contained = assign(amit, version=spring, start=date(2026, 3, 1), end=date(2026, 6, 30))
    assert contained.weight_version == spring
    with pytest.raises(FieldValidationError):  # before the open-ended v1 starts (1 Jan 2026)
        assign(ops["rahul_emp"], start=date(2025, 12, 1))
    assert assign(ops["rahul_emp"]).effective_to is None  # open-ended inside open-ended


def test_a_version_cannot_end_before_its_assignments(admin_user, ops, ops_version, assign):
    assignment = assign(ops["rahul_emp"])  # open-ended
    with pytest.raises(services.PerformanceError):
        services.end_weight_version(actor=admin_user, version=ops_version,
                                    last_day=date(2026, 10, 31))
    services.end_kpi_assignment(actor=admin_user, assignment=assignment,
                                last_day=date(2026, 10, 31))
    with pytest.raises(services.PerformanceError):
        services.end_weight_version(actor=admin_user, version=ops_version,
                                    last_day=date(2026, 10, 30))
    ended = services.end_weight_version(actor=admin_user, version=ops_version,
                                        last_day=date(2026, 10, 31))
    assert ended.effective_to == date(2026, 10, 31)


# --- KPI deactivation rule --------------------------------------------------------------------


def test_kpi_used_by_an_active_version_cannot_be_deactivated(admin_user, ops_version):
    accuracy = KPI.objects.get(code="ACCURACY")
    before = {w.kpi.code: w.weight for w in ops_version.weights.select_related("kpi")}
    with pytest.raises(FieldValidationError) as refused:
        services.set_kpi_active(actor=admin_user, kpi=accuracy, is_active=False)
    assert "is_active" in refused.value.fields
    accuracy.is_active = False
    with pytest.raises(FieldValidationError):  # a direct model save is refused too
        accuracy.save()
    assert KPI.objects.get(code="ACCURACY").is_active is True
    ops_version.refresh_from_db()
    assert ops_version.status == "ACTIVE"
    after = {w.kpi.code: w.weight for w in ops_version.weights.select_related("kpi")}
    assert after == before and sum(after.values()) == Decimal("10.00")  # nothing renormalised
    assert not AuditLog.objects.filter(action="performance.kpi_activation_changed").exists()


def test_kpi_not_used_by_an_active_version_can_be_deactivated(admin_user):
    legacy = KPI.objects.create(code="LEGACY", name="Legacy", score_source="MANAGER")
    services.create_weight_version(  # a DRAFT version does not lock the KPI
        actor=admin_user, configuration="HR", name="HR", effective_from=date(2026, 1, 1),
        weights={**OPS_WEIGHTS, "LEGACY": "0"},
    )
    assert services.set_kpi_active(actor=admin_user, kpi=legacy, is_active=False).is_active is False
    row = AuditLog.objects.get(action="performance.kpi_activation_changed")
    assert row.old_value == {"is_active": True} and row.new_value == {"is_active": False}
    assert row.context["code"] == "LEGACY"
    assert services.set_kpi_active(actor=admin_user, kpi=legacy, is_active=True).is_active is True
    assert services.set_kpi_active(actor=admin_user, kpi=legacy, is_active=True).is_active is True
    assert AuditLog.objects.filter(action="performance.kpi_activation_changed").count() == 2
