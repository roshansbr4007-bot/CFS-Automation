"""Phase 7.3 KRA review API: the HR review flow over HTTP, who may do what (HR, Admin,
Operations Manager, Employee) and what each response carries."""

from datetime import date

import pytest
import time_machine

from apps.accounts import roles
from apps.performance.models import DeductionRule, MonthlyComponentResult, MonthlyPerformance
from apps.recurring.models import RecurringSchedule, ScheduleOccurrence

from .test_kra_calculation import CLOSE, _category, _feed
from .test_kra_review import MANUAL, _activate_plan

pytestmark = pytest.mark.django_db
BASE = "/api/v1/performance"


@pytest.fixture
def hr(make_user):
    return make_user(roles.HR)


@pytest.fixture
def setup(admin_user, hr, ops, work, sla_24h, ist):
    plan = _activate_plan(admin_user, hr, ist, _category(_feed()))
    emp = ops["rahul_emp"]
    for day, title in ((2, "A"), (3, "B")):
        task = work.raise_task(emp, 2026, 11, day, 10, 0, title=title)
        work.complete(task, ops["rahul"], 2026, 11, day, 12, 0)
    return plan, emp


@pytest.fixture
def at_close(setup, ist):
    """Everything over HTTP happens after November has closed (logins included, so the
    session is not older than the travelled clock)."""
    with time_machine.travel(ist(*CLOSE), tick=False):
        yield setup


def _post(client, ist, url, body):
    return client.post(url, body, format="json")


def _component_id(month_id, code):
    return MonthlyComponentResult.objects.get(kpi_score__monthly_performance_id=month_id,
                                              kpi_score__kpi__code=code).component_id


def test_hr_reviews_a_month_end_to_end(at_close, hr, admin_user, client_for, ist):
    plan, emp = at_close
    client = client_for(hr)
    response = _post(client, ist, f"{BASE}/months/calculate/",
                     {"employee": emp.pk, "year": 2026, "month": 11})
    assert response.status_code == 200, response.content
    month = response.json()
    assert (month["status"], month["provisional"], month["final_total"]) == (
        "CALCULATED", False, "3.000000",
    )
    assert len(month["blockers"]) == 5
    blockers = client.get(f"{BASE}/months/{month['id']}/blockers/").json()
    assert {b["kind"] for b in blockers} == {"MANUAL_ENTRY_MISSING"}
    for code, value in MANUAL.items():
        url = f"{BASE}/months/{month['id']}/components/{_component_id(month['id'], code)}/"
        month = _post(client, ist, url + "manual-entry/",
                      {"version": month["version"], "achievement_pct": str(value),
                       "reason": "Monthly HR review"}).json()
    url = (f"{BASE}/months/{month['id']}/components/"
           f"{_component_id(month['id'], 'FINANCIAL_ACCURACY')}/mark-na/")
    month = _post(client, ist, url, {"version": month["version"], "reason": "None"}).json()
    assert (month["final_total"], month["band"], month["blockers"]) == (
        "7.800000", "Consistent Performer", [],
    )
    month = _post(client, ist, f"{BASE}/months/{month['id']}/submit/",
                  {"version": month["version"]}).json()
    assert month["status"] == "UNDER_REVIEW"
    accuracy = next(k for k in month["kpis"] if k["code"] == "ACCURACY")
    month = _post(client, ist, f"{BASE}/months/{month['id']}/kpis/{accuracy['kpi_id']}/adjust/",
                  {"version": month["version"], "points": "2.5", "reason": "Rework"}).json()
    rule = DeductionRule.objects.get(plan_version=plan, code="DATA_INCONSISTENCY")
    response = _post(client, ist, f"{BASE}/months/{month['id']}/deductions/",
                     {"version": month["version"], "rule": rule.pk, "percent": "20",
                      "evidence": "Audit sheet row 7", "reason": "Mismatch"})
    assert response.status_code == 201, response.content
    month = response.json()
    assert month["final_total"] == "5.840000"  # (7.8 - 0.5) x 80%
    application = month["deduction_applications"][0]
    assert (application["evidence"], application["reason"], application["active"]) == (
        "Audit sheet row 7", "Mismatch", True,
    )
    stale = _post(client, ist, f"{BASE}/months/{month['id']}/deductions/{application['id']}/"
                  "reverse/", {"version": month["version"] - 1, "reason": "x"})
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
    month = _post(client, ist, f"{BASE}/months/{month['id']}/deductions/{application['id']}/"
                  "reverse/", {"version": month["version"], "reason": "Wrong month"}).json()
    assert month["final_total"] == "7.300000"
    month = _post(client, ist, f"{BASE}/months/{month['id']}/finalize/",
                  {"version": month["version"]}).json()
    assert month["status"] == "FINALIZED"
    admin = client_for(admin_user)
    assert admin.get(f"{BASE}/months/{month['id']}/").status_code == 200  # read-only
    refused = _post(admin, ist, f"{BASE}/months/{month['id']}/reopen/",
                    {"version": month["version"], "reason": ""})
    assert refused.status_code == 400
    month = _post(admin, ist, f"{BASE}/months/{month['id']}/reopen/",
                  {"version": month["version"], "reason": "Late evidence"}).json()
    assert (month["status"], month["reopen_count"]) == ("UNDER_REVIEW", 1)
    month = _post(client, ist, f"{BASE}/months/{month['id']}/return/",
                  {"version": month["version"], "reason": "Recheck"}).json()
    assert (month["status"], month["final_total"]) == ("CALCULATED", "7.300000")
    annual = client.get(f"{BASE}/annual/", {"employee": emp.pk, "year": 2026}).json()
    assert (annual["applicable_months"], annual["annual_total"]) == (0, None)


def test_who_may_do_what(at_close, hr, admin_user, ops, client_for, ist):
    _, emp = at_close
    month = _post(client_for(hr), ist, f"{BASE}/months/calculate/",
                  {"employee": emp.pk, "year": 2026, "month": 11}).json()
    record = MonthlyPerformance.objects.get(pk=month["id"])
    body = {"version": record.version, "reason": "x"}
    manager = client_for(ops["manager"])
    employee = client_for(ops["rahul"])
    admin = client_for(admin_user)
    for client in (manager, employee):
        assert client.get(f"{BASE}/months/{record.pk}/").status_code == 403
        assert _post(client, ist, f"{BASE}/months/{record.pk}/submit/", body).status_code == 403
        assert client.get(f"{BASE}/annual/", {"employee": emp.pk, "year": 2026}
                          ).status_code == 403
    for url in ("submit/", "finalize/", "return/"):  # Admin: read and reopen only (E16)
        assert _post(admin, ist, f"{BASE}/months/{record.pk}/{url}", body).status_code == 403
    assert _post(admin, ist, f"{BASE}/months/calculate/",
                 {"employee": emp.pk, "year": 2026, "month": 11}).status_code == 403
    assert admin.get(f"{BASE}/months/{record.pk}/blockers/").status_code == 200
    reopen = _post(admin, ist, f"{BASE}/months/{record.pk}/reopen/", body)
    assert reopen.status_code == 409  # allowed to try; the month is not finalized
    assert MonthlyPerformance.objects.get(pk=record.pk).version == record.version


def test_leave_task_exceptions_and_annual_endpoints(setup, hr, ops, work, client_for, ist):
    _, emp = setup
    client = client_for(hr)
    leave = client.post(f"{BASE}/approved-leave/",
                        {"employee": emp.pk, "start_date": "2026-11-04",
                         "end_date": "2026-11-05", "reason": "Medical"}, format="json")
    assert leave.status_code == 201, leave.content
    assert client.get(f"{BASE}/approved-leave/", {"employee": emp.pk}).json()[0]["reason"] == (
        "Medical"
    )
    cancelled = client.post(f"{BASE}/approved-leave/{leave.json()['id']}/cancel/",
                            {"reason": "Mistake"}, format="json")
    assert cancelled.status_code == 200 and cancelled.json()["cancelled_at"]
    task = work.raise_task(emp, 2026, 11, 6, 10, 0, title="Other work")
    created = client.post(f"{BASE}/task-overrides/",
                          {"task": task.pk, "responsibility": _feed().pk, "action": "EXCLUDE",
                           "reason": "Not reconciliation work"}, format="json")
    assert created.status_code == 201, created.content
    assert len(client.get(f"{BASE}/task-overrides/", {"task": task.pk}).json()) == 1
    removed = client.post(f"{BASE}/task-overrides/{created.json()['id']}/remove/",
                          {"reason": "Undo"}, format="json")
    assert removed.status_code == 204
    assert client.get(f"{BASE}/approved-leave/", {"employee": "x"}).status_code == 400
    annual = client.get(f"{BASE}/annual/", {"employee": emp.pk, "year": 2026})
    assert annual.status_code == 200
    assert annual.json()["maximum_total"] == "120.00"
    assert client.get(f"{BASE}/annual/", {"year": 2026}).status_code == 400
    assert date.fromisoformat(leave.json()["start_date"]) == date(2026, 11, 4)


def test_gap_decision_endpoint(admin_user, hr, ops, own, client_for, ist):
    _activate_plan(admin_user, hr, ist, _category(_feed(), scope="SCHEDULED"))
    emp = ops["rahul_emp"]
    own(_feed(), emp, start=date(2026, 11, 1))
    gap = ScheduleOccurrence.objects.create(
        schedule=RecurringSchedule.objects.get(responsibility=_feed()),
        occurrence_date=date(2026, 11, 4), status="FAILED",
    )
    with time_machine.travel(ist(*CLOSE), tick=False):
        client = client_for(hr)
        month = client.post(f"{BASE}/months/calculate/",
                            {"employee": emp.pk, "year": 2026, "month": 11},
                            format="json").json()
        assert "UNDECIDED_GAP" in {b["kind"] for b in month["blockers"]}
        response = client.post(f"{BASE}/months/{month['id']}/gap-decisions/",
                               {"version": month["version"], "occurrence": gap.pk,
                                "decision": "EMPLOYEE_RESPONSIBLE", "reason": "Missed"},
                               format="json")
    assert response.status_code == 200, response.content
    assert "UNDECIDED_GAP" not in {b["kind"] for b in response.json()["blockers"]}


def test_a_legacy_employee_is_not_calculated_by_the_kra_endpoint(at_close, hr, ops, assign,
                                                                  client_for, ist):
    assign(ops["amit_emp"])
    response = _post(client_for(hr), ist, f"{BASE}/months/calculate/",
                     {"employee": ops["amit_emp"].pk, "year": 2026, "month": 11})
    assert response.status_code == 409 and response.json()["code"] == "not_kra_month"
    assert not MonthlyPerformance.objects.filter(employee=ops["amit_emp"]).exists()


# --- 7.3 decision 6 over HTTP -----------------------------------------------------------------

BAD_BODIES = [{}, {"reason": ""}, {"reason": "   "}, {"reason": None}]


@pytest.mark.parametrize("bad", BAD_BODIES)
def test_return_and_manual_entry_refuse_a_missing_or_blank_reason(at_close, hr, client_for,
                                                                  ist, bad):
    _, emp = at_close
    client = client_for(hr)
    month = client.post(f"{BASE}/months/calculate/",
                        {"employee": emp.pk, "year": 2026, "month": 11}, format="json").json()
    entry = (f"{BASE}/months/{month['id']}/components/"
             f"{_component_id(month['id'], 'COMPLIANCE')}/manual-entry/")
    refused = client.post(entry, {"version": month["version"], "achievement_pct": "90", **bad},
                          format="json")
    assert refused.status_code == 400 and set(refused.json()["fields"]) == {"reason"}
    month = client.post(f"{BASE}/months/{month['id']}/submit/", {"version": month["version"]},
                        format="json").json()
    refused = client.post(f"{BASE}/months/{month['id']}/return/",
                          {"version": month["version"], **bad}, format="json")
    assert refused.status_code == 400 and set(refused.json()["fields"]) == {"reason"}
    after = client.get(f"{BASE}/months/{month['id']}/").json()
    assert (after["status"], after["version"]) == ("UNDER_REVIEW", month["version"])
    compliance = next(k for k in after["kpis"] if k["code"] == "COMPLIANCE")
    assert compliance["components"][0]["entered_by"] is None


def test_return_and_manual_entry_accept_a_reason(at_close, hr, client_for, ist):
    _, emp = at_close
    client = client_for(hr)
    month = client.post(f"{BASE}/months/calculate/",
                        {"employee": emp.pk, "year": 2026, "month": 11}, format="json").json()
    entry = (f"{BASE}/months/{month['id']}/components/"
             f"{_component_id(month['id'], 'COMPLIANCE')}/manual-entry/")
    response = client.post(entry, {"version": month["version"], "achievement_pct": "90",
                                   "reason": "Audit file reviewed"}, format="json")
    assert response.status_code == 200, response.content
    month = client.post(f"{BASE}/months/{month['id']}/submit/",
                        {"version": response.json()["version"]}, format="json").json()
    response = client.post(f"{BASE}/months/{month['id']}/return/",
                           {"version": month["version"], "reason": "Recheck leave"},
                           format="json")
    assert response.status_code == 200 and response.json()["status"] == "CALCULATED"


def test_reopen_reverse_and_mark_na_keep_their_request_contract():
    from drf_spectacular.generators import SchemaGenerator

    schema = SchemaGenerator().get_schema(request=None, public=True)

    def required(suffix):
        path = next(p for p in schema["paths"] if p.endswith(suffix))
        ref = schema["paths"][path]["post"]["requestBody"]["content"]["application/json"]
        name = ref["schema"]["$ref"].rsplit("/", 1)[1]
        return sorted(schema["components"]["schemas"][name].get("required", []))

    for suffix in ("/reopen/", "/reverse/", "/mark-na/"):
        assert required(suffix) == ["version"]  # unchanged since 7.3
    assert required("/return/") == ["reason", "version"]
    assert required("/manual-entry/") == ["achievement_pct", "reason", "version"]
