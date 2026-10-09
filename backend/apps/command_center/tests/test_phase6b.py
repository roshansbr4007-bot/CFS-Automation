"""Phase 6B: read-only operational monitoring in the Command Center.

One Monday at 12:15 IST. Rahul owns the three daily responsibilities (Feed Upload done on time,
Mail Checking overdue, SIP/STP in progress and CRITICAL) and has an overdue manual Broker Mapping.
Amit has a blocked ad-hoc task and a manual Broker Mapping in WARNING. An RM employee has an
on-track manual task. The Operations Manager has no work."""

import pytest
import time_machine
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts import roles
from apps.command_center import services
from apps.core.timeutils import to_ist
from apps.org.models import Employee
from apps.org.tests.factories import EmployeeFactory
from apps.recurring import generator
from apps.tasks.models import Task, TaskTemplate

pytestmark = pytest.mark.django_db
BASE = "/api/v1/command-center/"
TASKS = "/api/v1/tasks/"
NOW = (2026, 10, 5, 12, 15)


def _act(client, task, name, **body):
    task.refresh_from_db()
    return client.post(f"{TASKS}{task.pk}/{name}/", {"version": task.version, **body})


@pytest.fixture
def monday(client_for, admin_user, ops, staff, resp, own, new_task, ist):
    for code in ("FEED_UPLOAD", "MAIL_CHECKING", "SIP_STP_SWITCH_CHECKING"):
        own(resp(code), ops["rahul_emp"])
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        generator.generate_due_occurrences()
    broker = TaskTemplate.objects.get(code="BROKER_MAPPING")  # 24 h from assignment
    with time_machine.travel(ist(2026, 10, 3, 10, 0), tick=False):
        overdue = new_task(ops["manager"], ops["rahul_emp"], title="Map RM codes", template=broker)
    with time_machine.travel(ist(2026, 10, 4, 23, 0), tick=False):
        warning = new_task(ops["manager"], ops["amit_emp"], title="Map new branch", template=broker)
    _, rm_emp = staff(roles.EMPLOYEE, department="RM")
    with time_machine.travel(ist(2026, 10, 5, 9, 0), tick=False):
        blocked = new_task(ops["manager"], ops["amit_emp"], title="Call client")
    with time_machine.travel(ist(2026, 10, 5, 12, 0), tick=False):
        on_track = new_task(admin_user, rm_emp, title="Prepare RM sheet", template=broker)
    with time_machine.travel(ist(2026, 10, 5, 9, 30), tick=False):
        amit = client_for(ops["amit"])
        assert _act(amit, blocked, "block", reason="Waiting for KYC").status_code == 200
    with time_machine.travel(ist(2026, 10, 5, 11, 0), tick=False):
        rahul = client_for(ops["rahul"])
        feed = Task.objects.get(responsibility__code="FEED_UPLOAD")
        sip = Task.objects.get(responsibility__code="SIP_STP_SWITCH_CHECKING")
        for task, action in ((feed, "start"), (feed, "complete"), (sip, "start")):
            assert _act(rahul, task, action).status_code == 200
    return {"rm_emp": rm_emp, "overdue": overdue, "warning": warning, "blocked": blocked,
            "on_track": on_track}


def _get(client, ist, path, **params):
    with time_machine.travel(ist(*NOW), tick=False):
        return client.get(f"{BASE}{path}", params)


# --- authorization ----------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["summary/", "sla-attention/", "employees/{emp}/"])
def test_admin_only(api_client, client_for, make_user, admin_client, ops, path):
    url = f"{BASE}{path.format(emp=ops['rahul_emp'].pk)}"
    assert api_client.get(url).status_code == 401
    for role in (roles.EMPLOYEE, roles.HR, roles.OPERATIONS_MANAGER):
        assert client_for(make_user(role)).get(url).status_code == 403
    assert admin_client.get(url).status_code == 200


# --- summary ----------------------------------------------------------------------------------


def test_overview_counts_keep_scheduled_and_manual_apart(admin_client, ops, monday, ist):
    body = _get(admin_client, ist, "summary/").json()
    overview = body["overview"]
    assert overview["daily_activities"] == {
        "scheduled": 3, "completed": 1, "pending": 1, "in_progress": 1, "blocked": 0, "overdue": 1,
    }
    assert overview["assigned_tasks"] == {
        "total": 4, "active": 4, "completed": 0, "pending": 3, "in_progress": 0, "blocked": 1,
        "overdue": 1,
    }
    assert overview["sla"] == {"on_track": 1, "warning": 1, "critical": 1, "overdue": 2}
    assert overview["employees"] == {
        "total_active": Employee.objects.filter(is_active=True).count(), "with_work": 3,
    }
    attention = {e["employee"]["id"]: e["attention"] for e in body["employees"]}
    assert attention[ops["rahul_emp"].pk] == ["OVERDUE", "CRITICAL"]
    assert attention[ops["amit_emp"].pk] == ["WARNING", "BLOCKED"]
    assert attention[monday["rm_emp"].pk] == ["ON_TRACK"]
    assert attention[ops["manager_emp"].pk] == ["NO_ACTIVE_WORK"]
    # Phase 6A fields are still there and unchanged in meaning.
    assert body["operations"]["daily_activity"]["total"] == 3
    assert body["operations"]["assigned_tasks"]["total"] == 4


@pytest.mark.parametrize(
    ("params", "daily", "assigned"),
    [
        ({"source": "SCHEDULED"}, 3, 0),
        ({"source": "MANUAL"}, 0, 4),
        ({"status": "BLOCKED"}, 0, 1),
        ({"status": "COMPLETED"}, 1, 0),
        ({"sla_state": "OVERDUE"}, 1, 1),
        ({"date": "2026-10-04"}, 0, 2),  # Sunday: nothing scheduled; 2 tasks were open then
    ],
)
def test_row_filters(admin_client, monday, ist, params, daily, assigned):
    overview = _get(admin_client, ist, "summary/", **params).json()["overview"]
    assert overview["daily_activities"]["scheduled"] == daily
    assert overview["assigned_tasks"]["total"] == assigned


def test_department_and_employee_filters(admin_client, ops, monday, ist):
    rm = _get(admin_client, ist, "summary/", department=monday["rm_emp"].department_id).json()
    assert [e["employee"]["id"] for e in rm["employees"]] == [monday["rm_emp"].pk]
    assert rm["overview"]["assigned_tasks"]["total"] == 1
    assert rm["overview"]["daily_activities"]["scheduled"] == 0
    amit = _get(admin_client, ist, "summary/", employee=ops["amit_emp"].pk).json()
    assert [e["employee"]["id"] for e in amit["employees"]] == [ops["amit_emp"].pk]
    assert amit["overview"]["assigned_tasks"]["total"] == 2


def test_empty_result(admin_client, monday, ist):
    nobody = EmployeeFactory(is_active=False)
    body = _get(admin_client, ist, "summary/", employee=nobody.pk).json()
    assert body["employees"] == []
    assert body["overview"]["employees"] == {"total_active": 0, "with_work": 0}
    assert body["overview"]["sla"] == {"on_track": 0, "warning": 0, "critical": 0, "overdue": 0}


@pytest.mark.parametrize(
    "params",
    [{"source": "BOTH"}, {"status": "DONE"}, {"sla_state": "RED"}, {"date": "today"},
     {"department": "ops"}, {"employee": "x"}],
)
def test_invalid_filters_are_rejected(admin_client, params):
    assert admin_client.get(f"{BASE}summary/", params).status_code == 400


# --- employee drill-down ----------------------------------------------------------------------


def test_drill_down_shows_each_employees_own_work_by_source(admin_client, ops, monday, ist):
    rahul = _get(admin_client, ist, f"employees/{ops['rahul_emp'].pk}/").json()
    assert rahul["attention"] == ["OVERDUE", "CRITICAL"]
    assert {a["source"] for a in rahul["daily_activities"]} == {"SCHEDULED"}
    assert {a["title"].split(" — ")[0] for a in rahul["daily_activities"]} == {
        "Feed Upload", "Mail Checking", "SIP/STP/Switch Checking",
    }
    manual = [(t["title"], t["source"]) for t in rahul["assigned_tasks"]]
    assert manual == [("Map RM codes", "MANUAL")]
    amit = _get(admin_client, ist, f"employees/{ops['amit_emp'].pk}/").json()
    assert amit["daily_activities"] == []
    assert {t["title"] for t in amit["assigned_tasks"]} == {"Map new branch", "Call client"}
    warning = next(t for t in amit["assigned_tasks"] if t["title"] == "Map new branch")
    assert warning["sla_state"] == "WARNING" and warning["remaining_seconds"] > 0
    assert warning["department"]["code"] == "OPS" and warning["category"]["code"] == "OPERATIONS"
    done = _get(admin_client, ist, f"employees/{ops['rahul_emp'].pk}/", status="COMPLETED").json()
    assert [a["completion_result"] for a in done["daily_activities"]] == ["ON_TIME"]
    assert done["assigned_tasks"] == []
    assert _get(admin_client, ist, "employees/999999/").status_code == 404


# --- SLA attention ----------------------------------------------------------------------------


def test_sla_attention_groups_by_existing_state(admin_client, ops, monday, ist):
    body = _get(admin_client, ist, "sla-attention/").json()
    titles = {group: [i["title"].split(" — ")[0] for i in body[group]]
              for group in ("critical", "warning", "overdue", "on_track", "not_started")}
    assert titles == {
        "critical": ["SIP/STP/Switch Checking"],
        "warning": ["Map new branch"],
        "overdue": ["Map RM codes", "Mail Checking"],  # earliest deadline first
        "on_track": [],  # on-track work is not attention unless requested
        "not_started": [],
    }
    sip = body["critical"][0]
    assert sip["source"] == "SCHEDULED" and sip["employee"]["id"] == ops["rahul_emp"].pk
    assert sip["remaining_seconds"] == 45 * 60 and sip["status"] == "IN_PROGRESS"
    assert body["overdue"][0]["source"] == "MANUAL"


def test_on_track_items_only_when_requested(admin_client, monday, ist):
    body = _get(admin_client, ist, "sla-attention/", sla_state="ON_TRACK").json()
    assert [i["title"] for i in body["on_track"]] == ["Prepare RM sheet"]
    assert body["critical"] == body["warning"] == body["overdue"] == []


def test_sla_attention_filters(admin_client, ops, monday, ist):
    manual = _get(admin_client, ist, "sla-attention/", source="MANUAL").json()
    assert manual["critical"] == [] and [i["title"] for i in manual["overdue"]] == ["Map RM codes"]
    amit = _get(admin_client, ist, "sla-attention/", employee=ops["amit_emp"].pk).json()
    assert amit["overdue"] == [] and [i["title"] for i in amit["warning"]] == ["Map new branch"]


# --- read-only and query count ----------------------------------------------------------------


def test_monitoring_changes_nothing(admin_client, ops, monday, ist):
    before = list(Task.objects.order_by("id").values_list("id", "status", "version"))
    for path in ("summary/", "sla-attention/", f"employees/{ops['rahul_emp'].pk}/"):
        _get(admin_client, ist, path)
    assert list(Task.objects.order_by("id").values_list("id", "status", "version")) == before
    assert admin_client.post(f"{BASE}summary/").status_code == 405


def test_query_count_does_not_grow_with_employees(admin_user, new_task, ist):
    broker = TaskTemplate.objects.get(code="BROKER_MAPPING")

    def add_employee_with_work():
        with time_machine.travel(ist(2026, 10, 5, 9, 0), tick=False):
            new_task(admin_user, EmployeeFactory(), title="Work", template=broker)

    def queries():
        with time_machine.travel(ist(*NOW), tick=False):
            filters = services.Filters(day=to_ist(timezone.now()).date())
            with CaptureQueriesContext(connection) as captured:
                services.summary(filters=filters)
                services.sla_attention(filters)
        return len(captured)

    add_employee_with_work()
    small = queries()
    for _ in range(8):
        add_employee_with_work()
    assert queries() == small
