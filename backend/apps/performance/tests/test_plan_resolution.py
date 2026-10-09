"""Phase 7.1 plan resolution: HR override -> department + system role (auth Group) default ->
no plan. Several login roles with several defaults are ambiguous and need an HR override."""

from datetime import date, datetime

import pytest
import time_machine
from django.contrib.auth.models import Group

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.core.errors import FieldValidationError
from apps.core.timeutils import IST
from apps.org.models import Department
from apps.org.tests.factories import EmployeeFactory
from apps.performance import config_services as svc
from apps.performance.models import MonthlyPerformance

from .test_config_v1 import _active_kra_plan

pytestmark = pytest.mark.django_db
RESOLUTION = "/api/v1/performance/plan-resolution/"


@pytest.fixture(autouse=True)
def frozen_today():
    with time_machine.travel(datetime(2026, 10, 8, 12, 0, tzinfo=IST), tick=False):
        yield


@pytest.fixture
def hr(make_user):
    return make_user(roles.HR)


@pytest.fixture
def plan(admin_user, hr):
    return _active_kra_plan(admin_user, hr)  # OPERATIONS_KRA v1, ACTIVE from 1 Nov 2026


def _ops():
    return Department.objects.get(code="OPS")


def _default(hr, role, start=date(2026, 10, 1), configuration="OPERATIONS_KRA"):
    return svc.create_plan_default(actor=hr, department=_ops(), role=role,
                                   configuration=configuration, effective_from=start)


def test_the_department_and_system_role_default_applies(hr, plan, ops):
    default = _default(hr, roles.EMPLOYEE)
    result = svc.resolve_plan(ops["rahul_emp"], date(2026, 11, 15))
    assert (result.state, result.source, result.version, result.default) == (
        "RESOLVED", "DEFAULT", plan, default,
    )
    assert result.roles == [roles.EMPLOYEE]
    before = svc.resolve_plan(ops["rahul_emp"], date(2026, 10, 15))  # default, no version yet
    assert (before.state, before.source) == ("NO_PLAN", "DEFAULT")
    assert "No ACTIVE version of OPERATIONS_KRA" in before.reason
    manager = svc.resolve_plan(ops["manager_emp"], date(2026, 11, 15))  # no OM default
    assert manager.state == "NO_PLAN" and manager.source is None


def test_an_hr_override_wins_over_the_default(hr, plan, ops):
    _default(hr, roles.EMPLOYEE)
    override = svc.create_override(actor=hr, employee=ops["manager_emp"], version=plan,
                                   effective_from=date(2026, 11, 1), reason="Shared targets")
    result = svc.resolve_plan(ops["manager_emp"], date(2026, 11, 2))
    assert (result.state, result.source, result.override) == ("RESOLVED", "OVERRIDE", override)


def test_no_login_means_no_system_role(hr, plan):
    _default(hr, roles.EMPLOYEE)
    loginless = EmployeeFactory(user=None, department=_ops())
    result = svc.resolve_plan(loginless, date(2026, 11, 15))
    assert result.state == "NO_PLAN" and "no login" in result.reason


def test_several_roles_with_several_defaults_are_ambiguous(hr, plan, staff):
    user, employee = staff(roles.EMPLOYEE)
    user.groups.add(Group.objects.get(name=roles.HR))  # also HR (no precedence is invented)
    employee_default = _default(hr, roles.EMPLOYEE)
    one = svc.resolve_plan(employee, date(2026, 11, 15))
    assert one.state == "RESOLVED" and one.roles == [roles.EMPLOYEE, roles.HR]
    hr_default = _default(hr, roles.HR)
    two = svc.resolve_plan(employee, date(2026, 11, 15))
    assert two.state == "AMBIGUOUS_ROLE" and two.version is None
    assert {d.pk for d in two.matching_defaults} == {employee_default.pk, hr_default.pk}


def test_plan_default_rules(hr, plan, ops):
    with pytest.raises(FieldValidationError):  # only the four system roles
        _default(hr, "Senior Executive")
    with pytest.raises(FieldValidationError):  # a legacy family is never a default
        _default(hr, roles.EMPLOYEE, configuration="OPERATIONS")
    with pytest.raises(FieldValidationError):
        _default(hr, roles.EMPLOYEE, configuration="NO_SUCH_PLAN")
    first = _default(hr, roles.EMPLOYEE)
    with pytest.raises(svc.ConfigConflict):  # periods never overlap
        _default(hr, roles.EMPLOYEE, start=date(2026, 12, 1))
    ended = svc.end_plan_default(actor=hr, default=first, last_day=date(2026, 11, 30))
    assert ended.effective_to == date(2026, 11, 30) and ended.ended_by == hr
    MonthlyPerformance.objects.create(  # a finalized KRA month for OPS / Employee in December
        employee=ops["rahul_emp"], year=2026, month=12, period_start=date(2026, 12, 1),
        period_end=date(2026, 12, 31), status="FINALIZED", calculation_model="KRA_POINTS",
        department=_ops(), role_name=roles.EMPLOYEE,
    )
    with pytest.raises(svc.ConfigConflict):
        _default(hr, roles.EMPLOYEE, start=date(2026, 12, 1))
    assert set(AuditLog.objects.filter(action__startswith="performance.config.default_")
               .values_list("action", flat=True)) == {
        "performance.config.default_created", "performance.config.default_ended",
    }


def test_resolution_api(client_for, make_user, hr, plan, ops):
    _default(hr, roles.EMPLOYEE)
    params = {"employee": ops["rahul_emp"].pk, "date": "2026-11-15"}
    for role in (roles.HR, roles.ADMIN):
        body = client_for(make_user(role)).get(RESOLUTION, params).json()
        assert (body["state"], body["source"], body["plan_version"]["id"]) == (
            "RESOLVED", "DEFAULT", plan.pk,
        )
        assert body["roles"] == [roles.EMPLOYEE] and body["override_id"] is None
    for role in (roles.OPERATIONS_MANAGER, roles.EMPLOYEE):
        assert client_for(make_user(role)).get(RESOLUTION, params).status_code == 403
    assert client_for(hr).get(RESOLUTION, {"employee": ops["rahul_emp"].pk}).status_code == 400

    defaults = client_for(hr).get("/api/v1/performance/plan-defaults/").json()
    assert [(d["role"], d["configuration"]) for d in defaults] == [
        (roles.EMPLOYEE, "OPERATIONS_KRA"),
    ]
    created = client_for(hr).post("/api/v1/performance/assignments/", {
        "employee": ops["amit_emp"].pk, "plan_version": plan.pk,
        "effective_from": "2026-11-01", "reason": "Pilot",
    })
    assert created.status_code == 201 and created.json()["reason"] == "Pilot"
    missing_reason = client_for(hr).post("/api/v1/performance/assignments/", {
        "employee": ops["manager_emp"].pk, "plan_version": plan.pk,
        "effective_from": "2026-11-01", "reason": "",
    })
    assert missing_reason.status_code == 400
