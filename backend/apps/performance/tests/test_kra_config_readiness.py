"""Phase 7.5A: GET /performance/plans/{id}/readiness/ - a read-only answer to "would Admin's
activation succeed right now?". Its problems must always be exactly what activation refuses
with; reading it must change, lock and audit nothing."""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth.models import Group
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts import roles
from apps.accounts.tests.factories import UserFactory
from apps.audit.models import AuditLog
from apps.performance import config_services as svc
from apps.performance.models import KPIComponent, KPIWeightVersion

from . import test_config_v1 as v1

# Shared fixtures: Thursday 8 Oct 2026 IST (autouse) and an HR user.
frozen_today = v1.frozen_today
hr = v1.hr

pytestmark = pytest.mark.django_db
PLANS = "/api/v1/performance/plans/"


def _readiness(client, plan):
    response = client.get(f"{PLANS}{plan.pk}/readiness/")
    assert response.status_code == 200, response.content
    return response.json()


def _activation_refusal(client, plan) -> list[str]:
    response = client.post(f"{PLANS}{plan.pk}/activate/")
    assert response.status_code == 400, response.content
    return response.json()["fields"]["activation"]


@pytest.fixture
def admin(client_for, admin_user):
    return client_for(admin_user)


@pytest.fixture
def hr_client(client_for, hr):
    return client_for(hr)


def test_the_seeded_draft_lists_exactly_what_activation_refuses(admin, hr_client):
    plan = v1._kra_plan()
    body = _readiness(hr_client, plan)
    assert (body["plan_id"], body["status"], body["calculation_model"], body["ready"]) == (
        plan.pk, "DRAFT", "KRA_POINTS", False,
    )
    assert body["checked_on"] == "2026-10-08"
    assert body["problems"] and body["problems"] == _activation_refusal(admin, plan)
    assert _readiness(admin, plan) == body  # HR and Admin see the same answer
    assert KPIWeightVersion.objects.get(pk=plan.pk).status == "DRAFT"


def _past_start(hr, plan):
    svc.update_plan_version(actor=hr, version=plan, effective_from=date(2026, 10, 8))


def _nine_and_a_half_points(hr, plan):
    svc.update_plan_line(actor=hr, line=plan.weights.get(kpi__code="ACCURACY"),
                         weight=Decimal("2.50"))


def _line_without_component(hr, plan):
    svc.delete_component(actor=hr, component=KPIComponent.objects.filter(
        plan_line__weight_version=plan).first())


def _duplicate_priority(hr, plan):
    first, second = plan.deduction_rules.order_by("code")[:2]
    svc.update_deduction_rule(actor=hr, rule=second, priority=first.priority)


def _verification_without_a_verification_step(hr, plan):
    component = KPIComponent.objects.filter(plan_line__weight_version=plan,
                                            source_type="RESPONSIBILITY_TASKS").first()
    svc.update_component(actor=hr, component=component, verification_policy="REQUIRED")


def _retired_band_scheme(hr, plan):
    """Admin retires the plan's band scheme after it was activated (it must be ACTIVE)."""
    scheme = plan.band_scheme
    svc.retire_band_scheme(actor=_admin(), scheme=scheme, last_day=scheme.effective_from,
                           reason="Replaced")


def _admin():
    user = UserFactory()
    user.groups.set(Group.objects.filter(name=roles.ADMIN))
    return user


@pytest.mark.parametrize("break_it", [
    _past_start, _nine_and_a_half_points, _line_without_component, _duplicate_priority,
    _verification_without_a_verification_step, _retired_band_scheme,
])
def test_each_problem_is_reported_exactly_as_activation_reports_it(
    admin, hr_client, admin_user, hr, break_it
):
    v1._activate_seeded_rules(admin_user)
    plan = v1._kra_plan()
    v1._complete(plan, hr)
    break_it(hr, plan)
    body = _readiness(hr_client, plan)
    assert body["ready"] is False
    assert body["problems"] == _activation_refusal(admin, plan)


def test_an_overlapping_active_version_is_reported_like_activation(admin, admin_user, hr):
    active = v1._active_kra_plan(admin_user, hr)
    copy = svc.clone_plan_version(actor=hr, version=active)
    body = _readiness(admin, copy)
    assert "OPERATIONS_KRA v1 is ACTIVE for part of this period; retire it first." in (
        body["problems"])
    assert body["problems"] == _activation_refusal(admin, copy)


def test_a_complete_draft_is_ready_and_then_activates(admin, hr_client, admin_user, hr):
    v1._activate_seeded_rules(admin_user)
    plan = v1._kra_plan()
    v1._complete(plan, hr)
    assert _readiness(hr_client, plan)["problems"] == []
    assert _readiness(hr_client, plan)["ready"] is True
    activated = admin.post(f"{PLANS}{plan.pk}/activate/")
    assert activated.status_code == 200 and activated.json()["status"] == "ACTIVE"

    # an ACTIVE version: the same message activation gives for a second attempt
    again = admin.post(f"{PLANS}{plan.pk}/activate/")
    assert again.status_code == 409
    body = _readiness(admin, plan)
    assert (body["status"], body["ready"], body["problems"]) == (
        "ACTIVE", False, [again.json()["message"]],
    )


def test_a_legacy_version_gets_the_activation_refusal_message(admin):
    legacy = v1._legacy_v1()
    refused = admin.post(f"{PLANS}{legacy.pk}/activate/")
    assert refused.status_code == 409
    body = _readiness(admin, legacy)
    assert (body["calculation_model"], body["ready"], body["problems"]) == (
        "LEGACY_WEIGHTED", False, [refused.json()["message"]],
    )


def test_a_retired_version_is_not_ready(admin, admin_user, hr):
    active = v1._active_kra_plan(admin_user, hr)
    svc.retire_plan_version(actor=admin_user, version=active, last_day=date(2026, 11, 30),
                            reason="Replaced")
    body = _readiness(admin, active)
    assert (body["status"], body["ready"], body["problems"]) == (
        "RETIRED", False, ["Only a draft plan can be activated."],
    )


def test_reading_readiness_changes_locks_and_audits_nothing(hr_client):
    plan = v1._kra_plan()
    before_rows = list(KPIWeightVersion.objects.order_by("pk").values())
    before_audit = AuditLog.objects.count()
    with CaptureQueriesContext(connection) as queries:
        _readiness(hr_client, plan)
    assert not any("FOR UPDATE" in q["sql"].upper() for q in queries.captured_queries)
    assert list(KPIWeightVersion.objects.order_by("pk").values()) == before_rows
    assert AuditLog.objects.count() == before_audit


def test_who_may_read_it(client_for, make_user, api_client, hr_client, admin):
    plan = v1._kra_plan()
    url = f"{PLANS}{plan.pk}/readiness/"
    assert api_client.get(url).status_code == 401
    for role in (roles.EMPLOYEE, roles.OPERATIONS_MANAGER):
        assert client_for(make_user(role)).get(url).status_code == 403
    assert hr_client.get(url).status_code == 200
    assert admin.get(url).status_code == 200
    assert hr_client.get(f"{PLANS}999999/readiness/").status_code == 404
    assert hr_client.post(url).status_code == 405  # read-only: no write method exists
    assert admin.post(url).status_code == 403  # Admin cannot write configuration at all
