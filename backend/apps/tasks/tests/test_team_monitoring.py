"""Operations Manager team monitoring: the existing monitoring data for the manager's OWN
department only, forced on the server. The Admin (organisation-wide) endpoints are unchanged."""

from datetime import date, datetime

import pytest
import time_machine

from apps.accounts import roles
from apps.core.timeutils import IST
from apps.org.models import Department
from apps.recurring import generator
from apps.recurring.models import RecurringSchedule, Responsibility, ResponsibilityOwner

pytestmark = pytest.mark.django_db
TEAM = "/api/v1/operations/team/daily-summary/"
ADMIN = "/api/v1/operations/daily-summary/"
DETAIL_KINDS = ("daily-activities", "assigned-tasks")


def _at(*parts):
    return time_machine.travel(datetime(*parts, tzinfo=IST), tick=False)


def _team_url(employee, kind):
    return f"/api/v1/operations/team/employees/{employee.pk}/{kind}/"


def _admin_url(employee, kind):
    return f"/api/v1/operations/employees/{employee.pk}/{kind}/"


def _get(client, url, **params):
    with _at(2026, 10, 5, 13, 30):
        return client.get(url, params)


@pytest.fixture
def monday(ops, staff, new_task, admin_user):
    """Monday 5 Oct 2026. OPS: Rahul owns Feed Upload (10:00, 2 h; still open at 13:30) and
    has one manual task. RM: its own manager and employee, with one manual task."""
    rm_manager, rm_manager_emp = staff(roles.OPERATIONS_MANAGER, department="RM")
    rm_user, rm_emp = staff(roles.EMPLOYEE, department="RM")
    RecurringSchedule.objects.filter(frequency="DAILY").update(effective_from=date(2026, 10, 5))
    ResponsibilityOwner.objects.create(
        responsibility=Responsibility.objects.get(code="FEED_UPLOAD"),
        employee=ops["rahul_emp"], effective_from=date(2026, 1, 1), assigned_by=admin_user,
    )
    with _at(2026, 10, 5, 9, 0):
        new_task(ops["manager"], ops["rahul_emp"], title="Client KYC follow-up")
        new_task(rm_manager, rm_emp, title="Portfolio review",
                 department=Department.objects.get(code="RM"))
    with _at(2026, 10, 5, 10, 0):
        generator.generate_due_occurrences()
    return {"rm_manager": rm_manager, "rm_manager_emp": rm_manager_emp,
            "rm_user": rm_user, "rm_emp": rm_emp}


def _rows(body):
    return {row["employee"]["id"]: row for row in body["employees"]}


def test_a_manager_sees_only_their_own_department(client_for, ops, monday):
    body = _get(client_for(ops["manager"]), TEAM).json()
    assert body["date"] == "2026-10-05"
    assert body["department"]["code"] == "OPS"
    rows = _rows(body)
    assert set(rows) == {ops["manager_emp"].pk, ops["rahul_emp"].pk, ops["amit_emp"].pk}
    assert rows[ops["rahul_emp"].pk]["daily_activity"] == {
        "total": 1, "completed": 0, "pending": 1, "overdue": 1, "completed_late": 0,
    }  # Feed Upload was due at 12:00
    assert rows[ops["rahul_emp"].pk]["assigned_tasks"] == {
        "total": 1, "completed": 0, "pending": 1, "overdue": 0, "completed_late": 0,
    }
    rm = _get(client_for(monday["rm_manager"]), TEAM).json()
    assert rm["department"]["code"] == "RM"
    assert set(_rows(rm)) == {monday["rm_manager_emp"].pk, monday["rm_emp"].pk}
    assert _rows(rm)[monday["rm_emp"].pk]["assigned_tasks"]["total"] == 1


def test_the_team_summary_is_exactly_the_admin_summary_of_that_department(
    admin_client, client_for, ops, monday
):
    team = _get(client_for(ops["manager"]), TEAM).json()
    admin = _get(admin_client, ADMIN, department=ops["manager_emp"].department_id).json()
    assert team["employees"] == admin["employees"]  # same monitoring data, nothing recalculated


def test_query_parameters_never_widen_the_scope(client_for, ops, monday):
    manager = client_for(ops["manager"])
    rm_department = monday["rm_emp"].department_id
    widened = _get(manager, TEAM, department=rm_department).json()  # ignored: forced to OPS
    assert widened["department"]["code"] == "OPS"
    assert monday["rm_emp"].pk not in _rows(widened)
    assert _get(manager, TEAM, employee=monday["rm_emp"].pk).json()["employees"] == []
    for kind in DETAIL_KINDS:  # another department's employee does not exist for this manager
        assert _get(manager, _team_url(monday["rm_emp"], kind)).status_code == 404
        assert _get(manager, f"/api/v1/operations/team/employees/999999/{kind}/").status_code == 404


def test_date_and_employee_filters(client_for, ops, monday):
    manager = client_for(ops["manager"])
    sunday = _get(manager, TEAM, date="2026-10-04").json()
    assert sunday["date"] == "2026-10-04"
    assert _rows(sunday)[ops["rahul_emp"].pk]["daily_activity"]["total"] == 0
    only = _get(manager, TEAM, employee=ops["rahul_emp"].pk).json()
    assert list(_rows(only)) == [ops["rahul_emp"].pk]
    for bad in ({"date": "today"}, {"employee": "x"}):
        assert _get(manager, TEAM, **bad).status_code == 400


def test_the_drill_down_is_department_scoped_and_matches_the_admin_view(
    admin_client, client_for, ops, monday
):
    manager = client_for(ops["manager"])
    daily = _get(manager, _team_url(ops["rahul_emp"], "daily-activities")).json()
    assert daily["counts"]["total"] == 1
    assert [a["title"].split(" — ")[0] for a in daily["activities"]] == ["Feed Upload"]
    tasks = _get(manager, _team_url(ops["rahul_emp"], "assigned-tasks")).json()
    assert [t["title"] for t in tasks["tasks"]] == ["Client KYC follow-up"]
    overdue = _get(manager, _team_url(ops["rahul_emp"], "daily-activities"), status="overdue")
    assert len(overdue.json()["activities"]) == 1
    assert _get(manager, _team_url(ops["rahul_emp"], "daily-activities"),
                sla_state="RED").status_code == 400
    for kind in DETAIL_KINDS:
        team = _get(manager, _team_url(ops["rahul_emp"], kind)).json()
        admin = _get(admin_client, _admin_url(ops["rahul_emp"], kind)).json()
        assert team == admin


def test_who_may_use_the_team_endpoints(api_client, client_for, make_user, staff, ops, monday):
    hr, _ = staff(roles.HR)
    inactive_manager, _ = staff(roles.OPERATIONS_MANAGER, employee_active=False)
    refused = {
        "employee": client_for(ops["rahul"]),
        "hr": client_for(hr),
        "manager without an employee record": client_for(make_user(roles.OPERATIONS_MANAGER)),
        "manager with an inactive employee record": client_for(inactive_manager),
        "admin (uses the organisation-wide endpoints)": client_for(make_user(roles.ADMIN)),
    }
    urls = [TEAM, *(_team_url(ops["rahul_emp"], kind) for kind in DETAIL_KINDS)]
    for who, client in refused.items():
        for url in urls:
            assert _get(client, url).status_code == 403, (who, url)
    for url in urls:
        assert _get(api_client, url).status_code == 401


def test_the_admin_endpoints_are_unchanged(admin_client, client_for, ops, monday):
    everyone = _rows(_get(admin_client, ADMIN).json())
    assert {ops["rahul_emp"].pk, monday["rm_emp"].pk} <= set(everyone)  # organisation-wide
    manager = client_for(ops["manager"])
    for url in (ADMIN, *(_admin_url(ops["rahul_emp"], kind) for kind in DETAIL_KINDS)):
        assert _get(manager, url).status_code == 403  # still Admin only
