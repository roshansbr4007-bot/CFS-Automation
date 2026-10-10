"""Phase 5.1: Daily Activities (employee) and Admin / Boss operations monitoring."""

from datetime import date

import pytest
import time_machine

from apps.accounts import roles
from apps.org.models import Department
from apps.recurring import generator
from apps.recurring.models import RecurringSchedule
from apps.sla import services as sla_services
from apps.sla.models import TaskSla
from apps.tasks.models import Task, TaskCategory, TaskTemplate

pytestmark = pytest.mark.django_db
TASKS = "/api/v1/tasks/"
MINE = f"{TASKS}daily-activities/"
SUMMARY = "/api/v1/operations/daily-summary/"


def _emp_url(employee, kind):
    return f"/api/v1/operations/employees/{employee.pk}/{kind}/"


@pytest.fixture
def owners(ops, resp, own):
    for code in ("FEED_UPLOAD", "MAIL_CHECKING", "SIP_STP_SWITCH_CHECKING"):
        own(resp(code), ops["rahul_emp"])
    own(resp("BROKERAGE_CALCULATION"), ops["amit_emp"])


def _generate(ist, *at):
    with time_machine.travel(ist(*at), tick=False):
        return generator.generate_due_occurrences()


def _act(client, task, name, **body):
    task.refresh_from_db()
    return client.post(f"{TASKS}{task.pk}/{name}/", {"version": task.version, **body})


def _complete(client_for, user, task, ist, *at):
    with time_machine.travel(ist(*at), tick=False):
        client = client_for(user)
        assert _act(client, task, "start").status_code == 200
        assert _act(client, task, "complete", work_response="Work done.").status_code == 200


def _mine(client_for, user, ist, *at, **params):
    with time_machine.travel(ist(*at), tick=False):
        return client_for(user).get(MINE, params).json()


def _by_title(activities):
    return {a["title"].split(" — ")[0]: a for a in activities}


# --- scheduler and login ----------------------------------------------------------------------


def test_login_never_creates_daily_activities(client_for, ops, owners, ist):
    before = Task.objects.count()
    body = _mine(client_for, ops["rahul"], ist, 2026, 10, 5, 10, 5)  # signs in, generator not run
    assert body["activities"] == [] and Task.objects.count() == before


def test_an_employee_signing_in_at_11_sees_the_10_oclock_activities_unchanged(
    client_for, ops, owners, ist
):
    assert _generate(ist, 2026, 10, 5, 10, 0)["generated"] == 3
    count = Task.objects.count()
    body = _mine(client_for, ops["rahul"], ist, 2026, 10, 5, 11, 0)  # first login at 11:00
    assert Task.objects.count() == count  # login fetched, created nothing
    feed = _by_title(body["activities"])["Feed Upload"]
    assert feed["scheduled_start"] == "2026-10-05T10:00:00+05:30"
    assert feed["deadline"] == "2026-10-05T12:00:00+05:30"  # not 13:00: login never resets
    assert feed["remaining_seconds"] == 3600
    assert feed["sla_state"] == "WARNING"  # 50% elapsed: the existing threshold
    assert feed["status"] == "PENDING" and feed["completion_result"] is None
    assert _by_title(body["activities"])["SIP/STP/Switch Checking"]["remaining_seconds"] == 7200
    later = _mine(client_for, ops["rahul"], ist, 2026, 10, 5, 11, 40)
    assert _by_title(later["activities"])["Feed Upload"]["deadline"] == feed["deadline"]


def test_countdown_and_overdue(client_for, ops, owners, ist):
    _generate(ist, 2026, 10, 5, 10, 0)
    early = _by_title(_mine(client_for, ops["rahul"], ist, 2026, 10, 5, 10, 50)["activities"])
    assert early["Feed Upload"]["sla_state"] == "ON_TRACK"
    assert early["Feed Upload"]["remaining_seconds"] == 70 * 60
    half = _by_title(_mine(client_for, ops["rahul"], ist, 2026, 10, 5, 11, 30)["activities"])
    assert half["Feed Upload"]["remaining_seconds"] == 1800
    assert half["Feed Upload"]["sla_state"] == "CRITICAL"
    late = _by_title(_mine(client_for, ops["rahul"], ist, 2026, 10, 5, 12, 0, 1)["activities"])
    assert late["Feed Upload"]["sla_state"] == "OVERDUE" and late["Feed Upload"]["is_overdue"]
    assert late["Feed Upload"]["remaining_seconds"] == -1


@pytest.mark.parametrize(("minute_after_11", "result"), [(35, "ON_TIME"), (80, "LATE")])
def test_completion_keeps_the_schedule_and_records_on_time_or_late(
    client_for, ops, owners, ist, minute_after_11, result
):
    _generate(ist, 2026, 10, 5, 10, 0)
    feed = Task.objects.get(responsibility__code="FEED_UPLOAD")
    hour, minute = 11 + minute_after_11 // 60, minute_after_11 % 60
    _complete(client_for, ops["rahul"], feed, ist, 2026, 10, 5, hour, minute)
    row = _by_title(_mine(client_for, ops["rahul"], ist, 2026, 10, 5, 13, 0)["activities"])
    feed_row = row["Feed Upload"]
    assert feed_row["status"] == "COMPLETED" and feed_row["completion_result"] == result
    assert feed_row["completed_at"] == f"2026-10-05T{hour:02d}:{minute:02d}:00+05:30"
    assert feed_row["scheduled_start"] == "2026-10-05T10:00:00+05:30"
    assert feed_row["deadline"] == "2026-10-05T12:00:00+05:30"
    assert feed_row["remaining_seconds"] is None and feed_row["is_overdue"] is False


def test_open_activities_carry_over_to_today_and_dates_can_be_chosen(client_for, ops, owners, ist):
    _generate(ist, 2026, 10, 5, 10, 0)
    tuesday = _mine(client_for, ops["rahul"], ist, 2026, 10, 6, 9, 0)  # before today's run
    assert {a["occurrence_date"] for a in tuesday["activities"]} == {"2026-10-05"}
    monday = _mine(client_for, ops["rahul"], ist, 2026, 10, 6, 9, 0, date="2026-10-05")
    assert len(monday["activities"]) == 3
    with time_machine.travel(ist(2026, 10, 6, 9, 0), tick=False):
        assert client_for(ops["rahul"]).get(MINE, {"date": "05-10-2026"}).status_code == 400


def test_an_activity_without_sla_shows_its_schedule_time_and_no_deadline(
    client_for, ops, owners, ist
):
    RecurringSchedule.objects.filter(frequency="MONTHLY").update(effective_from=date(2026, 10, 1))
    _generate(ist, 2026, 10, 20, 10, 0)
    body = _mine(client_for, ops["amit"], ist, 2026, 10, 20, 10, 30)
    brokerage = _by_title(body["activities"])["Brokerage Calculation"]
    assert brokerage["scheduled_start"] == "2026-10-20T10:00:00+05:30"
    assert brokerage["deadline"] is None and brokerage["remaining_seconds"] is None
    assert brokerage["sla_note"] == "No SLA configured"
    task = Task.objects.get(responsibility__code="BROKERAGE_CALCULATION")
    _complete(client_for, ops["amit"], task, ist, 2026, 10, 20, 11, 0)
    done = _by_title(_mine(client_for, ops["amit"], ist, 2026, 10, 20, 11, 5)["activities"])
    assert done["Brokerage Calculation"]["completion_result"] == "NO_DEADLINE"


def test_users_without_an_employee_record_have_no_daily_activities(client_for, make_user):
    assert client_for(make_user(roles.ADMIN)).get(MINE).json()["activities"] == []


# --- Admin / Boss monitoring ------------------------------------------------------------------


@pytest.fixture
def busy_monday(client_for, ops, owners, new_task, ist):
    """Rahul on Monday 5 Oct: Feed on time, Mail late, SIP open; one manual task done today,
    one manual Broker Mapping task (24h SLA) from Saturday still open and overdue."""
    _generate(ist, 2026, 10, 5, 10, 0)
    feed = Task.objects.get(responsibility__code="FEED_UPLOAD")
    mail = Task.objects.get(responsibility__code="MAIL_CHECKING")
    _complete(client_for, ops["rahul"], feed, ist, 2026, 10, 5, 11, 30)
    _complete(client_for, ops["rahul"], mail, ist, 2026, 10, 5, 12, 20)
    broker = TaskTemplate.objects.get(code="BROKER_MAPPING")
    with time_machine.travel(ist(2026, 10, 3, 10, 0), tick=False):
        overdue = new_task(ops["manager"], ops["rahul_emp"], title="Map RM codes", template=broker)
    with time_machine.travel(ist(2026, 10, 5, 9, 0), tick=False):
        done = new_task(ops["manager"], ops["rahul_emp"], title="Client KYC follow-up")
    with time_machine.travel(ist(2026, 10, 5, 12, 30), tick=False):
        client = client_for(ops["rahul"])
        _act(client, done, "start")
        _act(client, done, "complete", work_response="Work done.")
    return {"overdue": overdue, "done": done}


def _summary(client, ist, *at, **params):
    with time_machine.travel(ist(*at), tick=False):
        return client.get(SUMMARY, params)


def test_admin_summary_counts_daily_and_assigned_work_separately(
    admin_client, ops, busy_monday, ist
):
    body = _summary(admin_client, ist, 2026, 10, 5, 13, 30).json()
    assert body["date"] == "2026-10-05"
    rows = {r["employee"]["id"]: r for r in body["employees"]}
    rahul = rows[ops["rahul_emp"].pk]
    assert rahul["daily_activity"] == {
        "total": 3, "completed": 2, "pending": 1, "overdue": 1, "completed_late": 1,
    }  # SIP/STP (due 13:00) is overdue at 13:30; Mail was completed late
    assert rahul["assigned_tasks"] == {
        "total": 2, "completed": 1, "pending": 1, "overdue": 1, "completed_late": 0,
    }
    assert rows[ops["amit_emp"].pk]["daily_activity"]["total"] == 0  # no 20th yet


def test_admin_summary_filters_by_date_employee_and_department(
    admin_client, ops, busy_monday, staff, ist
):
    staff(roles.EMPLOYEE, department="RM")
    sunday = _summary(admin_client, ist, 2026, 10, 5, 13, 30, date="2026-10-04").json()
    rows = {r["employee"]["id"]: r for r in sunday["employees"]}
    rahul_sunday = rows[ops["rahul_emp"].pk]
    assert rahul_sunday["daily_activity"]["total"] == 0  # Sunday: nothing generated
    assert rahul_sunday["assigned_tasks"]["total"] == 1  # Saturday's task was open on Sunday
    only = _summary(admin_client, ist, 2026, 10, 5, 13, 30, employee=ops["rahul_emp"].pk).json()
    assert [r["employee"]["id"] for r in only["employees"]] == [ops["rahul_emp"].pk]
    ops_only = _summary(
        admin_client, ist, 2026, 10, 5, 13, 30, department=ops["rahul_emp"].department_id
    ).json()
    assert {r["employee"]["department"]["code"] for r in ops_only["employees"]} == {"OPS"}
    for bad in ({"date": "today"}, {"employee": "x"}, {"department": "ops"}):
        assert _summary(admin_client, ist, 2026, 10, 5, 13, 30, **bad).status_code == 400


def test_admin_employee_detail_with_filters(admin_client, ops, busy_monday, ist):
    daily_url = _emp_url(ops["rahul_emp"], "daily-activities")
    tasks_url = _emp_url(ops["rahul_emp"], "assigned-tasks")

    def titles(url, key, **params):
        return [row["title"].split(" — ")[0] for row in admin_client.get(url, params).json()[key]]

    with time_machine.travel(ist(2026, 10, 5, 13, 30), tick=False):
        daily = admin_client.get(daily_url).json()
        assert daily["counts"]["total"] == 3 and len(daily["activities"]) == 3
        results = {
            a["title"].split(" — ")[0]: a["completion_result"] for a in daily["activities"]
        }
        assert results == {
            "Feed Upload": "ON_TIME",
            "Mail Checking": "LATE",
            "SIP/STP/Switch Checking": None,
        }
        # status=overdue: still open and past the deadline.
        assert titles(daily_url, "activities", status="overdue") == ["SIP/STP/Switch Checking"]
        assert len(titles(daily_url, "activities", status="completed")) == 2
        assert titles(daily_url, "activities", status="pending") == ["SIP/STP/Switch Checking"]
        # sla_state=OVERDUE: the SLA state, which includes Mail Checking (completed late).
        assert sorted(titles(daily_url, "activities", sla_state="OVERDUE")) == [
            "Mail Checking",
            "SIP/STP/Switch Checking",
        ]
        assert set(titles(tasks_url, "tasks")) == {"Map RM codes", "Client KYC follow-up"}
        overdue = admin_client.get(tasks_url, {"status": "overdue"}).json()["tasks"]
        assert [t["title"] for t in overdue] == ["Map RM codes"]
        assert overdue[0]["assigned_by"]["id"] == ops["manager"].pk
        assert titles(tasks_url, "tasks", status="completed") == ["Client KYC follow-up"]
        assert titles(tasks_url, "tasks", status="pending") == ["Map RM codes"]
        assert admin_client.get(daily_url, {"status": "late"}).status_code == 400
        assert admin_client.get(daily_url, {"sla_state": "RED"}).status_code == 400
        missing = "/api/v1/operations/employees/999999/daily-activities/"
        assert admin_client.get(missing).status_code == 404


@pytest.mark.parametrize("role", [roles.EMPLOYEE, roles.HR, roles.OPERATIONS_MANAGER])
def test_only_admin_reaches_monitoring(api_client, client_for, make_user, ops, role):
    client = client_for(make_user(role))
    for url in (SUMMARY, _emp_url(ops["rahul_emp"], "daily-activities"),
                _emp_url(ops["rahul_emp"], "assigned-tasks")):
        assert client.get(url).status_code == 403
        assert api_client.get(url).status_code == 401


# --- Final business model checks (task SLA independence, Admin task rows) --------------------


def test_task_sla_comes_from_the_task_not_the_daily_schedule(
    client_for, ops, owners, new_task, ist
):
    """A manually raised task gets its deadline from its own task type and assignment time;
    the 10:00 daily schedule never sets or moves it (and it is not a daily activity)."""
    _generate(ist, 2026, 10, 5, 10, 0)
    broker = TaskTemplate.objects.get(code="BROKER_MAPPING")  # 24 h SLA from assignment
    with time_machine.travel(ist(2026, 10, 5, 15, 0), tick=False):
        typed = new_task(ops["manager"], ops["rahul_emp"], title="Map RM codes", template=broker)
        adhoc = new_task(ops["manager"], ops["rahul_emp"], title="Call client")
    with time_machine.travel(ist(2026, 10, 5, 15, 5), tick=False):
        client = client_for(ops["rahul"])
        typed_sla = client.get(f"{TASKS}{typed.pk}/").json()["sla"]["resolution"]
        adhoc_sla = client.get(f"{TASKS}{adhoc.pk}/").json()["sla"]
        daily = _by_title(client.get(MINE).json()["activities"])
    assert typed_sla["start_at"] == "2026-10-05T15:00:00+05:30"
    assert typed_sla["due_at"] == "2026-10-06T15:00:00+05:30"
    assert adhoc_sla["resolution"] is None and adhoc_sla["resolution_note"] == "No SLA configured"
    assert set(daily) == {"Feed Upload", "Mail Checking", "SIP/STP/Switch Checking"}
    assert daily["Feed Upload"]["deadline"] == "2026-10-05T12:00:00+05:30"  # unchanged


def test_admin_task_rows_show_who_raised_it_and_its_department_and_category(
    admin_client, ops, staff, new_task, ist
):
    hr, _ = staff(roles.HR, department="HR")
    with time_machine.travel(ist(2026, 10, 5, 9, 0), tick=False):
        task = new_task(
            hr,
            ops["amit_emp"],
            title="Collect KYC forms",
            department=Department.objects.get(code="HR"),
            category=TaskCategory.objects.get(code="COMPLIANCE"),
        )
    with time_machine.travel(ist(2026, 10, 5, 9, 30), tick=False):
        moved = admin_client.post(
            f"{TASKS}{task.pk}/reassign/",
            {"version": task.version, "assigned_to": ops["rahul_emp"].pk},
        )
        assert moved.status_code == 200
        rows = admin_client.get(_emp_url(ops["rahul_emp"], "assigned-tasks")).json()["tasks"]
    row = next(r for r in rows if r["title"] == "Collect KYC forms")
    assert row["raised_by"]["id"] == hr.pk  # raised by HR (another department)
    assert row["assigned_by"]["email"] == "admin@example.com"  # the current assignment
    assert row["department"]["code"] == "HR" and row["category"]["code"] == "COMPLIANCE"
    assert row["priority"] == "MEDIUM" and row["deadline"] is None


def test_daily_activity_sla_stays_schedule_based_even_with_a_priority_rule(
    admin_user, ops, owners, ist
):
    """A configured priority rule applies to manually raised tasks only: the generated daily
    activity keeps its schedule's task-type SLA (Feed Upload 10:00-12:00)."""
    sla_services.create_rule(
        actor=admin_user, code="PRIORITY_MEDIUM", name="Medium", duration_minutes=480
    )
    sla_services.set_priority_rule(
        actor=admin_user, priority="MEDIUM", rule_code="PRIORITY_MEDIUM"
    )
    _generate(ist, 2026, 10, 5, 10, 0)
    feed = Task.objects.get(responsibility__code="FEED_UPLOAD")
    assert feed.priority == "MEDIUM"
    clock = TaskSla.objects.get(task=feed, kind="RESOLUTION")
    assert clock.trigger == "FIXED_TIME" and clock.rule.code == "FEED_UPLOAD_2H"
    assert clock.start_at == ist(2026, 10, 5, 10, 0) and clock.due_at == ist(2026, 10, 5, 12, 0)
