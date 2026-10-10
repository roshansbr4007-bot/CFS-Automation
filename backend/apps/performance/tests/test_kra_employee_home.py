"""Phase 7.4 Employee Home API: the caller's own KRA months, one month, and the annual figures.
Isolation (own linked employee only), the approved state mapping, employee-safe N/A labels and
the absence of every internal review detail."""

import json
from datetime import date
from decimal import Decimal

import pytest

from apps.accounts import roles
from apps.performance import employee_performance as mine
from apps.performance import review_services as review
from apps.performance.annual import annual_summary
from apps.performance.kra_engine import NA
from apps.performance.models import MonthlyPerformance

from . import test_kra_review as shared
from .test_kra_calculation import _category, _feed
from .test_kra_review import (
    _activate_plan,
    _calc,
    _deduct,
    _fresh,
    _kpi_id,
    _stored_month,
    _submitted,
)

# Shared pytest fixtures (the closed November month of test_kra_review and its HR user).
hr = shared.hr
month = shared.month

pytestmark = pytest.mark.django_db
D = Decimal
BASE = "/api/v1/performance/my"
INTERNAL_CODES = {value for name, value in vars(NA).items() if name.isupper()}
HIDDEN_KEYS = ("status", "reopen", "evidence", "reason", "applied_by", "actor", "entered_by",
               "adjustment", "deduction_points")


def _get(client, path, **params):
    return client.get(f"{BASE}/{path}", params)


def _assert_nothing_internal(body):
    text = json.dumps(body, default=str)
    for code in INTERNAL_CODES:
        assert f'"{code}"' not in text, code
    for key in HIDDEN_KEYS:
        assert f'"{key}' not in text, key


# --- approved state mapping (decisions 1, D, E) ------------------------------------------------


def test_what_the_employee_sees_in_each_state(month, hr, admin_user, ops, client_for, ist):
    _, emp, record = month
    client = client_for(ops["rahul"])

    def row():
        body = _get(client, "kra-months/").json()
        _assert_nothing_internal(body)
        (only,) = body["months"]
        return only

    def detail():
        response = _get(client, f"kra-months/{record.pk}/")
        assert response.status_code == 200
        _assert_nothing_internal(response.json())
        return response.json()

    pending = {"id": record.pk, "year": 2026, "month": 11, "state": "PENDING_REVIEW"}
    # closed but still CALCULATED: pending, no numbers
    assert row() == {**pending, "final_total": None, "max_points_applicable": None,
                     "band": None}
    assert detail() == pending
    record = _submitted(record, hr)  # UNDER_REVIEW
    assert detail() == pending
    record = review.finalize(actor=hr, performance=record, version=record.version)
    assert row() == {**pending, "state": "FINALIZED", "final_total": "7.800000",
                     "max_points_applicable": "8.500000", "band": "Consistent Performer"}
    body = detail()
    assert (body["state"], body["auto_total"], body["final_total"]) == (
        "FINALIZED", "7.800000", "7.800000",
    )
    assert len(body["kpis"]) == 6 and body["deductions"] == []
    record = review.reopen(actor=admin_user, performance=record, version=record.version,
                           reason="Late evidence")
    assert detail() == pending  # decision D: back to pending until finalized again
    review.return_for_recalculation(actor=hr, performance=record, version=record.version,
                                    reason="Recheck", now=ist(2026, 12, 9))
    assert detail() == pending  # CALCULATED again, month closed


def test_the_current_month_is_provisional_with_its_band(admin_user, hr, ops, work, sla_24h,
                                                        client_for, ist):
    _activate_plan(admin_user, hr, ist, _category(_feed()))
    emp = ops["rahul_emp"]
    task = work.raise_task(emp, 2026, 11, 2, 10, 0, title="A")
    work.complete(task, ops["rahul"], 2026, 11, 2, 12, 0)
    record = _calc(emp, ist, 2026, 11, 20, 9, 0)  # provisional
    body = _get(client_for(ops["rahul"]), f"kra-months/{record.pk}/").json()
    _assert_nothing_internal(body)
    assert (body["state"], body["final_total"], body["max_points_applicable"], body["band"]) == (
        "PROVISIONAL", "3.000000", "3.000000", "Performance Concern",  # decision E
    )
    accuracy = next(k for k in body["kpis"] if k["name"] == "Accuracy")
    assert accuracy["components"][0]["on_time_count"] == 1  # decision A: counts, no task rows
    assert '"tasks"' not in json.dumps(body) and '"task_credits"' not in json.dumps(body)
    manual = next(k for k in body["kpis"] if k["name"] == "Compliance & Documentation")
    assert (manual["not_applicable"], manual["na_label"]) == (True, "Not applicable this month")
    assert manual["components"][0]["na_label"] == "Awaiting HR assessment"


# --- decision 3: employee-safe N/A labels -----------------------------------------------------


def test_every_internal_na_code_maps_to_an_approved_label():
    approved = set(mine.NA_LABELS.values()) | {mine.GENERIC_NA_LABEL}
    for code in INTERNAL_CODES | {"", "SOMETHING_NEW", None}:
        label = mine.na_label(code)
        assert label in approved
        assert not any(c in label for c in INTERNAL_CODES)
    assert mine.na_label(NA.APPROVED_LEAVE) == mine.GENERIC_NA_LABEL  # task-level: generic
    assert mine.na_label(NA.NO_APPLICABLE_TASKS) == "No applicable tasks this month"


def test_a_finalized_month_shows_e19_deductions_and_hides_every_review_detail(
    month, hr, ops, client_for
):
    plan, _, record = month
    record = _submitted(record, hr)
    record = review.adjust_kpi(actor=hr, performance=record, version=record.version,
                               kpi_id=_kpi_id(record, "ACCURACY"), points=D("2.5"),
                               reason="SECRET ADJUSTMENT REASON")
    record = _deduct(record, hr, plan, "DELAYED_SYSTEM_UPDATE", D("10"),
                     kpi_id=_kpi_id(record, "ACCURACY"))
    record = review.finalize(actor=hr, performance=_fresh(record),
                             version=_fresh(record).version)
    response = _get(client_for(ops["rahul"]), f"kra-months/{record.pk}/")
    body = response.json()
    _assert_nothing_internal(body)
    text = json.dumps(body)
    for secret in ("SECRET ADJUSTMENT REASON", "Ticket 42", "found in review"):
        assert secret not in text
    assert body["deductions"] == [{"rule": "Delayed system update", "kpi": "Accuracy",
                                   "component": "", "points": "0.250000"}]
    accuracy = next(k for k in body["kpis"] if k["name"] == "Accuracy")
    assert (accuracy["auto_points"], accuracy["final_points"]) == ("3.000000", "2.250000")
    financial = next(k for k in body["kpis"] if k["name"] == "Financial Accuracy")
    assert financial["na_label"] == "Not applicable this month"  # HR's reason never shown


# --- decision 4 of the plan: personal endpoints never widen ---------------------------------


def test_personal_endpoints_need_an_own_employee_record(make_user, admin_user, client_for):
    unlinked_hr = client_for(make_user(roles.HR))
    for client in (unlinked_hr, client_for(admin_user)):
        for path, params in (("kra-months/", {}), ("kra-months/1/", {}),
                             ("annual/", {"year": 2026})):
            response = _get(client, path, **params)
            assert response.status_code == 404
            assert response.json()["code"] == "no_employee_record"


def test_personal_endpoints_only_ever_show_the_callers_own_months(month, ops, staff, client_for):
    _, rahul_emp, record = month
    hr_user, hr_emp = staff(roles.HR)
    hr_client = client_for(hr_user)
    body = _get(hr_client, "kra-months/", employee=rahul_emp.pk).json()
    assert body["employee"]["id"] == hr_emp.pk and body["months"] == []  # parameter ignored
    assert _get(hr_client, f"kra-months/{record.pk}/").status_code == 404
    assert _get(client_for(ops["amit"]), f"kra-months/{record.pk}/").status_code == 404
    manager = client_for(ops["manager"])  # P11: no review rights, but sees own (none here)
    assert _get(manager, "kra-months/").json()["months"] == []
    start, end = date(2026, 9, 1), date(2026, 9, 30)
    legacy = MonthlyPerformance.objects.create(employee=rahul_emp, year=2026, month=9,
                                               period_start=start, period_end=end)
    assert _get(client_for(ops["rahul"]), f"kra-months/{legacy.pk}/").status_code == 404
    assert [m["id"] for m in _get(client_for(ops["rahul"]), "kra-months/").json()["months"]] == [
        record.pk
    ]


def test_history_and_annual_count_the_same_finalized_months(ops, client_for):
    """Review finding: the history must never hide a month the annual figures count."""
    emp = ops["rahul_emp"]
    january = _stored_month(emp, 1, total="7")
    march = _stored_month(emp, 3, total="8")
    type(emp).objects.filter(pk=emp.pk).update(date_of_joining=date(2026, 2, 10))
    client = client_for(ops["rahul"])
    body = _get(client, "kra-months/").json()
    assert body["employee"]["date_of_joining"] == "2026-02-10"
    finalized = {m["id"] for m in body["months"] if m["state"] == "FINALIZED"}
    annual = _get(client, "annual/", year=2026).json()
    assert finalized == {m["id"] for m in annual["months"]} == {january.pk, march.pk}
    assert _get(client, f"kra-months/{january.pk}/").status_code == 200


# --- decision 1 of the plan: the approved annual figures ------------------------------------


def test_my_annual_is_the_approved_summary_without_internal_fields(ops, client_for):
    emp = ops["rahul_emp"]
    _stored_month(emp, 1, total="7.5")
    _stored_month(emp, 2, total="8.25")
    _stored_month(emp, 3, total="9", status="UNDER_REVIEW")
    _stored_month(emp, 4, total="0", maximum="0")
    _stored_month(emp, 5, model="LEGACY_WEIGHTED")
    client = client_for(ops["rahul"])
    body = _get(client, "annual/", year=2026).json()
    expected = annual_summary(emp, 2026)
    assert (body["applicable_months"], body["annual_total"], body["annual_average"],
            body["maximum_total"]) == (2, "15.750000", "7.875000", "120.00")
    assert [m["id"] for m in body["months"]] == [m["id"] for m in expected["months"]]
    assert body["excluded"] == [{"month": row["month"], "reason": row["reason"]}
                                for row in expected["excluded"]]
    assert "employee" not in body and "status" not in json.dumps(body)
    for params in ({}, {"year": "x"}, {"year": 1999}):
        assert _get(client, "annual/", **params).status_code == 400
    assert _get(client_for(ops["amit"]), "annual/", year=2026).json()["applicable_months"] == 0


def test_reading_my_performance_changes_nothing(month, ops, client_for):
    _, _, record = month
    before = list(MonthlyPerformance.objects.values_list("pk", "version", "status",
                                                          "final_total"))
    client = client_for(ops["rahul"])
    _get(client, "kra-months/")
    _get(client, f"kra-months/{record.pk}/")
    _get(client, "annual/", year=2026)
    assert list(MonthlyPerformance.objects.values_list("pk", "version", "status",
                                                        "final_total")) == before
    assert _fresh(record).version == record.version
