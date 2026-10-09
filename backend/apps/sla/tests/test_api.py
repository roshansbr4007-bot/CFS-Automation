"""SLA in the task API, the creation preview, task types, rules, settings, notifications."""

from datetime import time, timedelta

import pytest
import time_machine

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.org.models import Department
from apps.sla import services
from apps.sla.models import SlaRule, SlaSetting, TaskSla
from apps.tasks.models import TaskTemplate

pytestmark = pytest.mark.django_db


def test_task_responses_carry_backend_calculated_sla(client_for, ops, new_task, template, ist):
    with time_machine.travel(ist(2026, 10, 5, 10, 30), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], template=template("BROKER_MAPPING"))
        client = client_for(ops["rahul"])
        detail = client.get(f"/api/v1/tasks/{task.pk}/").json()
        listed = client.get("/api/v1/tasks/").json()["results"][0]
    res = detail["sla"]["resolution"]
    assert res["rule_code"] == "BROKER_MAPPING_24H" and res["trigger"] == "ASSIGNMENT"
    assert res["state"] == "ON_TRACK" and res["elapsed_pct"] == 0.0
    assert res["due_at"] == "2026-10-06T10:30:00+05:30" and res["duration_minutes"] == 1440
    assert detail["sla"]["acknowledgment"]["rule_code"] == "ACK_2H"
    assert detail["template"]["code"] == "BROKER_MAPPING"
    assert listed["sla"]["resolution"]["due_at"] == res["due_at"]


def test_preview_calculates_without_saving(client_for, ops, template, ist):
    client = client_for(ops["manager"])
    with time_machine.travel(ist(2026, 10, 5, 11, 30), tick=False):
        body = client.post(
            "/api/v1/tasks/sla-preview/",
            {"template": template("FEED_UPLOAD").pk, "assigned_to": ops["rahul_emp"].pk},
        ).json()
    assert body["resolution"]["start_at"] == "2026-10-05T10:00:00+05:30"
    assert body["resolution"]["due_at"] == "2026-10-05T12:00:00+05:30"
    assert body["resolution"]["state"] == "CRITICAL"
    assert body["acknowledgment"] is None
    assert not TaskSla.objects.exists()


@pytest.mark.parametrize(
    ("payload", "note"),
    [
        ({}, "No SLA configured"),
        ({"template_code": "SIP_FAILURE"}, "Enter the event time to see the SLA."),
        ({"template_code": "EOD_TEST"}, "SLA inactive until company work_end is configured."),
        ({"template_code": "BROKERAGE_CALCULATION"}, "No SLA configured"),
    ],
)
def test_preview_explains_missing_sla(client_for, ops, template, payload, note):
    """Updated in Phase 5: the end-of-day case uses a test task type, because Mail Checking
    is now fixed-time; Brokerage Calculation has no resolution SLA (approved)."""
    TaskTemplate.objects.create(
        code="EOD_TEST",
        name="End of day test",
        department=Department.objects.get(code="OPS"),
        resolution_rule_code="MAIL_SAME_DAY",
    )
    data = {"assigned_to": ops["rahul_emp"].pk}
    if code := payload.get("template_code"):
        data["template"] = template(code).pk
    body = client_for(ops["manager"]).post("/api/v1/tasks/sla-preview/", data).json()
    assert body["resolution"] is None and body["resolution_note"] == note


def test_preview_with_ack_and_event_and_login_wait(client_for, ops, template, now):
    client = client_for(ops["manager"])
    event = (now - timedelta(hours=2)).isoformat()
    body = client.post(
        "/api/v1/tasks/sla-preview/",
        {
            "template": template("SIP_FAILURE").pk,
            "trigger_at": event,
            "acknowledgment_required": True,
        },
    ).json()
    assert body["resolution"]["rule_code"] == "SIP_FAILURE_24H"
    assert body["acknowledgment"]["rule_code"] == "ACK_2H"
    waiting = client.post(
        "/api/v1/tasks/sla-preview/",
        {"template": template("BIRTHDAY_WISHES").pk, "assigned_to": ops["amit_emp"].pk},
    ).json()
    assert waiting["resolution"]["state"] == "NOT_STARTED"
    assert "login" in waiting["resolution"]["waiting_for"]


def test_preview_needs_create_permission(client_for, make_user):
    """Updated for Phase 4: HR now holds tasks.create_task, so HR may preview; a signed-in user
    without any task-creating role is still refused."""
    url = "/api/v1/tasks/sla-preview/"
    assert client_for(make_user()).post(url, {}).status_code == 403
    assert client_for(make_user(roles.HR)).post(url, {}).status_code == 200


def test_task_types_for_the_form(client_for, make_user):
    body = client_for(make_user(roles.EMPLOYEE)).get("/api/v1/task-templates/").json()
    by_code = {t["code"]: t for t in body}
    assert set(by_code) == {
        "FEED_UPLOAD",
        "BIRTHDAY_WISHES",
        "SIP_STP_CHECK",
        "BROKER_MAPPING",
        "RECONCILIATION",
        "MAIL_CHECKING",
        "SIP_FAILURE",
        "BROKERAGE_CALCULATION",  # Phase 5
    }
    assert by_code["FEED_UPLOAD"]["fixed_time"] == "10:00:00"
    assert by_code["BROKER_MAPPING"]["acknowledgment_required"] is True
    # Phase 5 rules: Mail Checking and SIP/STP are fixed-time from 10:00.
    for code in ("MAIL_CHECKING", "SIP_STP_CHECK"):
        assert by_code[code]["trigger"] == "FIXED_TIME"
        assert by_code[code]["fixed_time"] == "10:00:00"
    assert by_code["MAIL_CHECKING"]["sla_rule_name"] == "Mail checking"
    assert by_code["MAIL_CHECKING"]["sla_note"] is None
    assert by_code["BROKERAGE_CALCULATION"]["sla_note"] == services.NO_SLA
    assert by_code["SIP_FAILURE"]["sla_rule_name"] == "SIP failure resolution"
    assert all(t["department"]["code"] == "OPS" and not t["verification_required"] for t in body)


def test_seeded_rules_are_listed(client_for, make_user):
    rules = client_for(make_user()).get("/api/v1/sla-rules/").json()
    minutes = {r["code"]: r["duration_minutes"] for r in rules}
    assert minutes == {
        "ACK_2H": 120,
        "FEED_UPLOAD_2H": 120,
        "BIRTHDAY_2H": 120,
        "SIP_STP_CHECK_3H": 180,
        "BROKER_MAPPING_24H": 1440,
        "RECON_24H": 1440,
        "MAIL_SAME_DAY": None,
        "SIP_FAILURE_24H": 1440,
        "MAIL_CHECKING_2H": 120,  # Phase 5: Mail Checking 10:00-12:00
    }
    assert all(r["clock"] == "CALENDAR" and r["version"] == 1 for r in rules)


def test_admin_supersedes_a_rule(admin_client, admin_user):
    rule = SlaRule.objects.get(code="FEED_UPLOAD_2H")
    url = f"/api/v1/sla-rules/{rule.pk}/supersede/"
    response = admin_client.post(url, {"duration_minutes": 90})
    assert response.status_code == 201 and response.json()["version"] == 2
    rule.refresh_from_db()
    assert rule.is_active is False
    row = AuditLog.objects.get(action="sla.rule_superseded")
    assert row.old_value["duration_minutes"] == 120 and row.new_value["duration_minutes"] == 90
    again = admin_client.post(url, {"duration_minutes": 60})
    assert again.status_code == 409 and again.json()["code"] == "rule_not_active"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"clock": "BUSINESS"}, "clock"),
        ({"warning_pct": 80}, "warning_pct"),
        ({"duration_minutes": None}, "duration_minutes"),
    ],
)
def test_supersede_validation(admin_client, body, field):
    rule = SlaRule.objects.get(code="BROKER_MAPPING_24H")
    response = admin_client.post(f"/api/v1/sla-rules/{rule.pk}/supersede/", body)
    assert response.status_code == 400 and field in response.json()["fields"]


@pytest.mark.parametrize("role", [roles.EMPLOYEE, roles.OPERATIONS_MANAGER, roles.HR])
def test_only_admin_changes_sla_configuration(client_for, make_user, role):
    client = client_for(make_user(role))
    rule = SlaRule.objects.get(code="FEED_UPLOAD_2H")
    assert client.post(f"/api/v1/sla-rules/{rule.pk}/supersede/", {}).status_code == 403
    assert client.patch("/api/v1/sla-settings/", {"company_work_end": "18:30"}).status_code == 403


def test_settings_start_empty_and_admin_sets_them(admin_client):
    assert admin_client.get("/api/v1/sla-settings/").json()["company_work_end"] is None
    response = admin_client.patch(
        "/api/v1/sla-settings/", {"company_work_end": "18:30", "login_fallback_time": "10:15"}
    )
    assert response.json()["company_work_end"] == "18:30:00"
    row = AuditLog.objects.get(action="sla.settings_updated")
    assert row.old_value == {"company_work_end": None, "login_fallback_time": None}
    assert row.new_value == {"company_work_end": "18:30", "login_fallback_time": "10:15"}
    admin_client.patch("/api/v1/sla-settings/", {"company_work_end": "18:30"})
    assert AuditLog.objects.filter(action="sla.settings_updated").count() == 1
    assert SlaSetting.load().login_fallback_time == time(10, 15)


# --- notifications ------------------------------------------------------------------------------


@pytest.fixture
def inbox(ops, new_task, template, ist):
    with time_machine.travel(ist(2026, 10, 5, 9, 0), tick=False):
        new_task(ops["manager"], ops["rahul_emp"], template=template("FEED_UPLOAD"))
    with time_machine.travel(ist(2026, 10, 5, 11, 30), tick=False):
        services.evaluate_clocks()
    return Notification.objects.filter(recipient=ops["rahul"]).order_by("id")


def test_my_notifications_and_unread_count(client_for, ops, inbox):
    client = client_for(ops["rahul"])
    body = client.get("/api/v1/notifications/").json()
    assert [n["kind"] for n in body["results"]] == ["SLA_CRITICAL", "SLA_WARNING"]
    assert body["results"][0]["task_reference"].startswith("T-")
    assert client.get("/api/v1/notifications/unread-count/").json() == {"unread": 2}
    assert client_for(ops["amit"]).get("/api/v1/notifications/").json()["count"] == 0


def test_mark_read_and_read_all(client_for, ops, inbox):
    client = client_for(ops["rahul"])
    first = inbox.first()
    assert client.post(f"/api/v1/notifications/{first.pk}/read/").json()["read_at"]
    assert client.get("/api/v1/notifications/", {"unread": "true"}).json()["count"] == 1
    other = client_for(ops["amit"]).post(f"/api/v1/notifications/{first.pk}/read/")
    assert other.status_code == 404
    assert client.post("/api/v1/notifications/read-all/").json() == {"marked": 1}
    assert client.get("/api/v1/notifications/unread-count/").json() == {"unread": 0}
