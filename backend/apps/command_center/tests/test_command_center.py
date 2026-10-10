"""Phase 6A: Admin (Boss) Command Center - permissions, aggregation, scheduler heartbeat, health."""

import pytest
import time_machine

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.audit.services import record
from apps.command_center import services
from apps.command_center.heartbeat import GENERATOR_JOB, SLA_JOB
from apps.command_center.models import SchedulerHeartbeat
from apps.recurring import generator
from apps.recurring.tasks import generate_recurring_tasks
from apps.sla.tasks import evaluate_sla_clocks
from apps.tasks.models import Task, TaskTemplate

pytestmark = pytest.mark.django_db
SUMMARY = "/api/v1/command-center/summary/"
HEALTH = "/api/v1/command-center/health/"
TASKS = "/api/v1/tasks/"


@pytest.fixture
def probes(monkeypatch):
    """Live infrastructure is never touched in tests: Redis and Celery probes are replaced."""
    state = {"redis": True, "workers": [{"worker@host": {"ok": "pong"}}], "pinged": 0}

    def redis_ping(url):
        if isinstance(state["redis"], Exception):
            raise state["redis"]
        return state["redis"]

    def celery_ping():
        state["pinged"] += 1
        if isinstance(state["workers"], Exception):
            raise state["workers"]
        return state["workers"]

    monkeypatch.setattr(services, "redis_ping", redis_ping)
    monkeypatch.setattr(services, "celery_ping", celery_ping)
    return state


def _checks(body):
    return {c["name"]: c["status"] for c in body["checks"]}


# --- permissions ------------------------------------------------------------------------------


@pytest.mark.parametrize("url", [SUMMARY, HEALTH])
def test_admin_only(api_client, client_for, make_user, admin_client, probes, url):
    assert api_client.get(url).status_code == 401
    for role in (roles.EMPLOYEE, roles.HR, roles.OPERATIONS_MANAGER):
        assert client_for(make_user(role)).get(url).status_code == 403
    assert admin_client.get(url).status_code == 200


# --- aggregation (reuses the existing monitoring rows and SLA engine) ------------------------


def _act(client, task, name, **body):
    task.refresh_from_db()
    return client.post(f"{TASKS}{task.pk}/{name}/", {"version": task.version, **body})


def _complete(client_for, user, task, ist, *at):
    with time_machine.travel(ist(*at), tick=False):
        client = client_for(user)
        assert _act(client, task, "start").status_code == 200
        assert _act(client, task, "complete", work_response="Work done.").status_code == 200


def test_summary_aggregates_today_from_existing_definitions(
    admin_client, client_for, ops, resp, own, new_task, ist
):
    for code in ("FEED_UPLOAD", "MAIL_CHECKING", "SIP_STP_SWITCH_CHECKING"):
        own(resp(code), ops["rahul_emp"])
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        generator.generate_due_occurrences()
    _complete(client_for, ops["rahul"], Task.objects.get(responsibility__code="FEED_UPLOAD"),
              ist, 2026, 10, 5, 11, 30)
    _complete(client_for, ops["rahul"], Task.objects.get(responsibility__code="MAIL_CHECKING"),
              ist, 2026, 10, 5, 12, 20)  # after its 12:00 deadline
    broker = TaskTemplate.objects.get(code="BROKER_MAPPING")  # 24 h from assignment
    with time_machine.travel(ist(2026, 10, 3, 10, 0), tick=False):
        new_task(ops["manager"], ops["rahul_emp"], title="Map RM codes", template=broker)
    with time_machine.travel(ist(2026, 10, 5, 9, 0), tick=False):
        done = new_task(ops["manager"], ops["rahul_emp"], title="Client KYC follow-up")
    _complete(client_for, ops["rahul"], done, ist, 2026, 10, 5, 12, 30)

    with time_machine.travel(ist(2026, 10, 5, 13, 30), tick=False):
        body = admin_client.get(SUMMARY).json()
    assert body["date"] == "2026-10-05"
    assert body["operations"]["daily_activity"] == {
        "total": 3, "completed": 2, "pending": 1, "overdue": 1, "completed_late": 1,
    }
    assert body["operations"]["assigned_tasks"] == {
        "total": 2, "completed": 1, "pending": 1, "overdue": 1, "completed_late": 0,
    }
    assert body["sla"]["daily_activity"] == {
        "not_started": 0, "on_track": 0, "warning": 0, "critical": 0, "overdue": 1,
        "completed_on_time": 1, "completed_late": 1,
    }
    assert body["sla"]["assigned_tasks"]["overdue"] == 1  # Broker Mapping, due 4 Oct
    assert body["todays_occurrences"] == {"generated": 3, "skipped": 0, "missed": 0, "failed": 0}
    rahul = next(e for e in body["employees"] if e["employee"]["id"] == ops["rahul_emp"].pk)
    assert rahul["employee"]["department"]["code"] == "OPS"
    assert rahul["daily_activity"]["overdue"] == 1 and rahul["assigned_tasks"]["total"] == 2


def test_recent_events_are_the_latest_20_and_metadata_only(admin_client, admin_user):
    for i in range(25):
        record(action="test.event", entity_type="thing", entity_id=i, actor=admin_user,
               old={"secret": "old"}, new={"secret": "new"}, extra={"note": "private"})
    events = admin_client.get(SUMMARY).json()["recent_events"]
    assert len(events) == 20
    assert set(events[0]) == {"id", "action", "entity_type", "entity_id", "actor", "occurred_at"}
    assert events[0]["entity_id"] == "24" and events[0]["actor"]["id"] == admin_user.pk
    assert "secret" not in str(events) and "private" not in str(events)
    assert AuditLog.objects.filter(action="test.event").count() == 25


# --- scheduler heartbeat ----------------------------------------------------------------------


def _scheduler(client):
    return {j["job"]: j for j in client.get(SUMMARY).json()["scheduler"]}


def test_scheduler_never_run_then_healthy_then_stale(admin_client, ops, resp, own, ist):
    jobs = _scheduler(admin_client)
    assert {jobs[GENERATOR_JOB]["status"], jobs[SLA_JOB]["status"]} == {"NEVER_RUN"}
    own(resp("FEED_UPLOAD"), ops["rahul_emp"])
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        result = generate_recurring_tasks()  # the real Celery task body
        evaluate_sla_clocks()
    assert result["generated"] == 1  # business result returned unchanged
    with time_machine.travel(ist(2026, 10, 5, 10, 1), tick=False):
        jobs = _scheduler(admin_client)
    assert jobs[GENERATOR_JOB]["status"] == "HEALTHY"
    assert jobs[GENERATOR_JOB]["last_summary"]["generated"] == 1
    assert jobs[SLA_JOB]["status"] == "HEALTHY"
    with time_machine.travel(ist(2026, 10, 5, 10, 10), tick=False):
        stale = _scheduler(admin_client)[GENERATOR_JOB]
    assert stale["status"] == "UNKNOWN" and "minutes ago" in stale["detail"]


def test_a_failed_run_is_reported_and_the_error_still_raised(admin_client, monkeypatch):
    def broken():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("apps.recurring.tasks.generate_due_occurrences", broken)
    with pytest.raises(RuntimeError):
        generate_recurring_tasks()
    job = _scheduler(admin_client)[GENERATOR_JOB]
    assert job["status"] == "FAILED" and "database unavailable" in job["detail"]


def test_a_heartbeat_write_failure_never_breaks_the_job(monkeypatch, ops, resp, own, ist):
    def boom(*args, **kwargs):
        raise RuntimeError("heartbeat table unavailable")

    monkeypatch.setattr(SchedulerHeartbeat.objects, "update_or_create", boom)
    own(resp("FEED_UPLOAD"), ops["rahul_emp"])
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        assert generate_recurring_tasks()["generated"] == 1
    assert Task.objects.filter(responsibility__code="FEED_UPLOAD").count() == 1
    assert not SchedulerHeartbeat.objects.exists()
    assert str(SchedulerHeartbeat(job="x")) == "x: never run"


# --- live health ------------------------------------------------------------------------------


def test_health_all_reachable(admin_client, probes):
    body = admin_client.get(HEALTH).json()
    assert body["overall"] == "HEALTHY"
    assert _checks(body) == {
        "api": "HEALTHY", "database": "HEALTHY", "redis": "HEALTHY", "celery_workers": "HEALTHY",
    }


def test_redis_down_fails_and_workers_are_not_pinged(admin_client, probes):
    probes["redis"] = ConnectionError("refused")
    body = admin_client.get(HEALTH).json()
    assert _checks(body)["redis"] == "FAILED"
    assert _checks(body)["celery_workers"] == "UNKNOWN" and probes["pinged"] == 0
    assert body["overall"] == "FAILED"


def test_no_worker_reply_fails_and_a_ping_error_is_unknown(admin_client, probes):
    probes["workers"] = []
    assert _checks(admin_client.get(HEALTH).json())["celery_workers"] == "FAILED"
    probes["workers"] = TimeoutError("broker busy")
    body = admin_client.get(HEALTH).json()
    assert _checks(body)["celery_workers"] == "UNKNOWN" and body["overall"] == "UNKNOWN"


def test_non_redis_broker_and_database_failure(admin_client, probes, settings, monkeypatch):
    settings.CELERY_BROKER_URL = "memory://"
    assert _checks(admin_client.get(HEALTH).json())["redis"] == "UNKNOWN"

    class BrokenConnection:
        def cursor(self):
            raise OSError("db down")

    monkeypatch.setattr(services, "connection", BrokenConnection())
    assert services._database()[0] == "FAILED"
