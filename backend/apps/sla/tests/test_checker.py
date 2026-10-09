"""sla_tick: thresholds once per clock, notification rules, email delivery, idempotency."""

import pytest
import time_machine
from django.core import mail
from django.core.management import call_command

from apps.accounts import roles
from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.notifications import services as notification_services
from apps.notifications.models import Notification
from apps.org.tests.factories import EmployeeFactory
from apps.sla import services
from apps.sla.models import TaskSla

pytestmark = pytest.mark.django_db


@pytest.fixture
def feed_task(ops, new_task, template, ist):
    """Feed Upload for Rahul: 10:00 -> 12:00 IST (50% 11:00, 75% 11:30, 100% 12:00)."""
    with time_machine.travel(ist(2026, 10, 5, 9, 0), tick=False):
        return new_task(ops["manager"], ops["rahul_emp"], template=template("FEED_UPLOAD"))


@pytest.fixture
def escalation(make_user, ops):
    """Active HR, an inactive HR, an Admin (never assumed to be the Boss) and Rahul's
    reporting manager (the Operations Manager), who is the Boss recipient."""
    hr = make_user(roles.HR, email="hr@example.com")
    admin = make_user(roles.ADMIN, email="admin@example.com")
    make_user(roles.HR, email="former.hr@example.com", is_active=False)
    ops["rahul_emp"].reporting_manager = ops["manager_emp"]
    ops["rahul_emp"].save()
    return hr, admin


def fixed_boss(clock):
    """A replacement resolver used to prove the Boss mapping is configurable."""
    return User.objects.get(email="ceo@example.com"), None


def _tick(ist, *at):
    with time_machine.travel(ist(*at), tick=False):
        return services.evaluate_clocks()


def _rows(level):
    return Notification.objects.filter(kind=f"SLA_{level}")


def test_warning_notifies_employee_in_app_only(ops, feed_task, escalation, ist):
    assert _tick(ist, 2026, 10, 5, 10, 59)["thresholds"] == 0
    assert _tick(ist, 2026, 10, 5, 11, 0)["thresholds"] == 1
    rows = list(_rows("WARNING"))
    assert [r.recipient for r in rows] == [ops["rahul"]]
    assert rows[0].email_status == "NOT_REQUIRED" and mail.outbox == []
    clock = TaskSla.objects.get(task=feed_task)
    assert clock.state == "WARNING" and clock.warning_at == ist(2026, 10, 5, 11, 0)


def test_critical_notifies_employee_in_app_and_email(ops, feed_task, escalation, ist):
    _tick(ist, 2026, 10, 5, 11, 30)
    critical = _rows("CRITICAL").get()
    assert critical.recipient == ops["rahul"] and critical.email_status == "SENT"
    assert [m.to for m in mail.outbox] == [[ops["rahul"].email]]
    assert "Critical (75%)" in mail.outbox[0].subject


def test_overdue_notifies_employee_hr_and_boss_not_every_admin(ops, feed_task, escalation, ist):
    hr, admin = escalation
    _tick(ist, 2026, 10, 5, 12, 0)
    overdue = {r.recipient.email for r in _rows("OVERDUE")}
    # employee + active HR + Boss (reporting manager); inactive HR and plain Admin are not.
    assert overdue == {ops["rahul"].email, hr.email, ops["manager"].email}
    assert admin.email not in overdue
    assert not AuditLog.objects.filter(action="task.sla_escalation_recipient_missing").exists()
    overdue_mail = sorted(m.to[0] for m in mail.outbox if "Overdue" in m.subject)
    assert overdue_mail == sorted(overdue)
    clock = TaskSla.objects.get(task=feed_task)
    assert clock.state == "OVERDUE"
    feed_task.refresh_from_db()  # SLA Overdue is never a workflow status
    assert feed_task.status == "PENDING"


def test_crossing_every_threshold_at_once_records_each_once(ops, feed_task, escalation, ist):
    assert _tick(ist, 2026, 10, 5, 13, 0)["thresholds"] == 3
    rows = AuditLog.objects.filter(action="task.sla_threshold_reached")
    levels = sorted(rows.values_list("new_value__level", flat=True))
    assert levels == ["CRITICAL", "OVERDUE", "WARNING"]


def test_running_the_checker_again_never_duplicates(ops, feed_task, escalation, ist):
    _tick(ist, 2026, 10, 5, 12, 5)
    def counts():
        audit = AuditLog.objects.filter(action="task.sla_threshold_reached").count()
        return Notification.objects.count(), len(mail.outbox), audit

    before = counts()
    for minute in (6, 7, 30):
        _tick(ist, 2026, 10, 5, 12, minute)
    assert counts() == before


def test_notify_threshold_is_idempotent_by_dedup_key(ops, feed_task, ist):
    _tick(ist, 2026, 10, 5, 11, 0)
    clock = TaskSla.objects.get(task=feed_task)
    notification_services.notify_threshold(clock, "WARNING")
    assert _rows("WARNING").count() == 1


def test_stopped_clocks_are_ignored(client_for, ops, feed_task, ist):
    with time_machine.travel(ist(2026, 10, 5, 10, 30), tick=False):
        feed_task.refresh_from_db()
        client_for(ops["manager"]).post(
            f"/api/v1/tasks/{feed_task.pk}/cancel/", {"version": feed_task.version, "reason": "x"}
        )
    assert _tick(ist, 2026, 10, 5, 13, 0)["thresholds"] == 0
    assert not Notification.objects.exists()


def test_failed_email_is_retried_once_delivered(ops, feed_task, ist, monkeypatch):
    def broken(**kwargs):
        raise OSError("SMTP down")

    monkeypatch.setattr(notification_services, "send_mail", broken)
    _tick(ist, 2026, 10, 5, 11, 30)
    row = _rows("CRITICAL").get()
    assert row.email_status == "FAILED" and row.email_attempts == 1
    assert "SMTP down" in row.email_error
    monkeypatch.undo()
    assert _tick(ist, 2026, 10, 5, 11, 31)["emails_sent"] == 1
    row.refresh_from_db()
    assert row.email_status == "SENT" and row.email_error == "" and len(mail.outbox) == 1


def test_assignee_without_login_still_escalates(ops, staff, new_task, template, escalation, ist):
    unlinked = EmployeeFactory(full_name="No Login")
    with time_machine.travel(ist(2026, 10, 5, 9, 0), tick=False):
        task = new_task(ops["manager"], unlinked, template=template("FEED_UPLOAD"))
    _tick(ist, 2026, 10, 5, 12, 0)
    recipients = set(_rows("OVERDUE").filter(task=task).values_list("recipient__email", flat=True))
    assert recipients == {"hr@example.com"}  # no login for the assignee, no Boss configured
    assert not _rows("WARNING").filter(task=task).exists()
    gap = AuditLog.objects.get(action="task.sla_escalation_recipient_missing")
    assert gap.new_value["reason"] == "The assignee has no reporting manager set."


def test_sla_tick_command(feed_task, ist, monkeypatch, capsys):
    monkeypatch.setattr("apps.sla.management.commands.sla_tick.time.sleep", lambda s: None)
    with time_machine.travel(ist(2026, 10, 5, 11, 0), tick=False):
        call_command("sla_tick", "--once")
        call_command("sla_tick", "--loop", "60", "--max-runs", "2")
    output = capsys.readouterr().out
    assert output.count("SLA tick:") == 3
    assert "1 threshold(s)" in output


def test_reporting_manager_without_login_is_flagged_not_guessed(
    ops, staff, new_task, template, escalation, ist
):
    ops["rahul_emp"].reporting_manager = EmployeeFactory(full_name="Boss Without Login")
    ops["rahul_emp"].save()
    with time_machine.travel(ist(2026, 10, 5, 9, 0), tick=False):
        task = new_task(ops["manager"], ops["rahul_emp"], template=template("FEED_UPLOAD"))
    summary = _tick(ist, 2026, 10, 5, 12, 0)
    assert summary["escalation_gaps"] == 1
    emails = set(_rows("OVERDUE").filter(task=task).values_list("recipient__email", flat=True))
    assert emails == {ops["rahul"].email, "hr@example.com"}
    gap = AuditLog.objects.get(action="task.sla_escalation_recipient_missing")
    assert gap.new_value == {
        "clock": "RESOLUTION",
        "level": "OVERDUE",
        "reason": "The assignee's reporting manager has no active login.",
    }


def test_inactive_reporting_manager_is_flagged(ops, feed_task, escalation, ist):
    ops["manager_emp"].is_active = False
    ops["manager_emp"].save()
    assert _tick(ist, 2026, 10, 5, 12, 0)["escalation_gaps"] == 1


def test_boss_resolver_is_configurable(ops, feed_task, escalation, ist, make_user, settings):
    make_user(email="ceo@example.com")
    settings.SLA_BOSS_RESOLVER = "apps.sla.tests.test_checker.fixed_boss"
    _tick(ist, 2026, 10, 5, 12, 0)
    overdue = {r.recipient.email for r in _rows("OVERDUE")}
    assert "ceo@example.com" in overdue and ops["manager"].email not in overdue


def test_tick_command_warns_about_missing_boss(ops, new_task, template, ist, capsys):
    with time_machine.travel(ist(2026, 10, 5, 9, 0), tick=False):
        new_task(ops["manager"], ops["rahul_emp"], template=template("FEED_UPLOAD"))
    with time_machine.travel(ist(2026, 10, 5, 12, 0), tick=False):
        call_command("sla_tick", "--once")
    assert "had no Boss recipient" in capsys.readouterr().err
