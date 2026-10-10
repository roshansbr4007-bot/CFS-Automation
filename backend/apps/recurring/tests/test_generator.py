"""Automatic generation of responsibility work (Phase 5)."""

from datetime import date

import pytest
import time_machine
from django.conf import settings
from django.core.management import call_command
from django.db import IntegrityError, transaction

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.recurring import generator
from apps.recurring.models import RecurringSchedule, Responsibility, ScheduleOccurrence
from apps.recurring.services import assign_owner
from apps.recurring.tasks import generate_recurring_tasks
from apps.sla.models import TaskSla
from apps.sla.tasks import evaluate_sla_clocks
from apps.tasks.models import Task, TaskAssignment, TaskTemplate
from apps.tasks.services import AssignmentNotAllowed, create_scheduled_task

pytestmark = pytest.mark.django_db
TASKS = "/api/v1/tasks/"


def _run(ist, *at):
    with time_machine.travel(ist(*at), tick=False):
        return generator.generate_due_occurrences()


def _feed_task():
    return Task.objects.get(responsibility__code="FEED_UPLOAD")


@pytest.fixture
def owners(ops, resp, own):
    """Rahul owns the three daily responsibilities, Amit owns Brokerage Calculation."""
    for code in ("FEED_UPLOAD", "MAIL_CHECKING", "SIP_STP_SWITCH_CHECKING"):
        own(resp(code), ops["rahul_emp"])
    own(resp("BROKERAGE_CALCULATION"), ops["amit_emp"])


# --- fixed-time daily generation --------------------------------------------------------------


def test_daily_responsibilities_are_generated_at_10_for_the_current_owner(
    client_for, ops, owners, ist
):
    assert _run(ist, 2026, 10, 5, 9, 59)["generated"] == 0
    assert _run(ist, 2026, 10, 5, 10, 0)["generated"] == 3
    task = _feed_task()
    assert task.source == "SCHEDULED" and task.title == "Feed Upload — 05 Oct 2026"
    assert task.assigned_to == ops["rahul_emp"]
    assert task.created_by.email == generator.SCHEDULER_EMAIL  # the scheduler, not the manager
    assert task.assigned_by == task.created_by
    assert task.department.code == "OPS" and task.category.code == "OPERATIONS"
    assert task.occurrence_date == date(2026, 10, 5) and task.generated_at is not None
    assert task.schedule.responsibility == task.responsibility
    assert TaskAssignment.objects.filter(task=task).count() == 1
    received = client_for(ops["rahul"]).get(TASKS, {"view": "received", "source": "scheduled"})
    assert {t["title"] for t in received.json()["results"]} == {
        "Feed Upload — 05 Oct 2026",
        "Mail Checking — 05 Oct 2026",
        "SIP/STP/Switch Checking — 05 Oct 2026",
    }


@pytest.mark.parametrize(
    ("code", "due_hour"),
    [("FEED_UPLOAD", 12), ("MAIL_CHECKING", 12), ("SIP_STP_SWITCH_CHECKING", 13)],
)
def test_generated_tasks_get_the_existing_sla(owners, ist, code, due_hour):
    _run(ist, 2026, 10, 5, 10, 0)
    clock = TaskSla.objects.get(task__responsibility__code=code, kind="RESOLUTION")
    assert clock.start_at == ist(2026, 10, 5, 10, 0)
    assert clock.due_at == ist(2026, 10, 5, due_hour, 0)


def test_generation_does_not_need_any_login_and_exists_for_a_late_manager(
    client_for, ops, owners, ist
):
    _run(ist, 2026, 10, 5, 10, 0)  # nobody has signed in
    with time_machine.travel(ist(2026, 10, 5, 10, 15), tick=False):
        manager = client_for(ops["manager"])  # the manager signs in late
        titles = [t["title"] for t in manager.get(TASKS, {"source": "scheduled"}).json()["results"]]
    assert "Feed Upload — 05 Oct 2026" in titles


def test_no_generation_on_a_non_working_day(owners, ist):
    assert _run(ist, 2026, 10, 10, 11, 0)["generated"] == 0  # 2nd Saturday
    assert not Task.objects.filter(occurrence_date=date(2026, 10, 10)).exists()


# --- duplicates -------------------------------------------------------------------------------


def test_running_again_and_again_never_duplicates(owners, ist):
    first = _run(ist, 2026, 10, 5, 10, 0, 1)
    second = _run(ist, 2026, 10, 5, 10, 0, 30)
    third = _run(ist, 2026, 10, 5, 10, 1)
    assert first["generated"] == 3 and second["generated"] == third["generated"] == 0
    assert Task.objects.filter(source="SCHEDULED").count() == 3
    assert ScheduleOccurrence.objects.filter(occurrence_date=date(2026, 10, 5)).count() == 3


def test_both_database_barriers_refuse_a_second_occurrence(owners, ist):
    _run(ist, 2026, 10, 5, 10, 0)
    task = _feed_task()
    assert generator._claim(task.schedule, task.occurrence_date, "GENERATED") is None
    with pytest.raises(IntegrityError), transaction.atomic():
        Task.objects.create(
            title="dup", department=task.department, category=task.category,
            created_by=task.created_by, assigned_to=task.assigned_to,
            assigned_by=task.assigned_by, assigned_at=task.assigned_at, source="SCHEDULED",
            responsibility=task.responsibility, schedule=task.schedule,
            occurrence_date=task.occurrence_date, generated_at=task.generated_at,
        )


# --- recovery ---------------------------------------------------------------------------------


def test_a_late_run_still_generates_today_once_with_the_original_sla_start(owners, ist):
    assert _run(ist, 2026, 10, 5, 10, 30)["generated"] == 3
    clock = TaskSla.objects.get(task=_feed_task(), kind="RESOLUTION")
    assert clock.start_at == ist(2026, 10, 5, 10, 0)  # original trigger, not 10:30
    recovered = AuditLog.objects.filter(action="recurring.occurrence_recovered")
    assert recovered.count() == 3
    assert {r.context["delay_seconds"] for r in recovered} == {1800}


def test_fully_missed_daily_occurrences_are_recorded_not_generated(owners, ist):
    RecurringSchedule.objects.filter(frequency="DAILY").update(effective_from=date(2026, 10, 1))
    _run(ist, 2026, 10, 5, 10, 0)  # first run since Thursday 1 Oct
    feed = RecurringSchedule.objects.get(responsibility__code="FEED_UPLOAD")
    statuses = dict(feed.occurrences.values_list("occurrence_date", "status"))
    assert statuses == {
        date(2026, 10, 1): "MISSED",
        date(2026, 10, 2): "MISSED",
        date(2026, 10, 3): "MISSED",  # 1st Saturday is a working day
        date(2026, 10, 5): "GENERATED",  # Sunday 4 Oct is not a business day
    }
    assert Task.objects.filter(responsibility__code="FEED_UPLOAD").count() == 1
    assert AuditLog.objects.filter(action="recurring.occurrence_missed").count() == 9


def test_missed_monthly_occurrence_is_recovered_on_its_resolved_date(owners, ist):
    RecurringSchedule.objects.filter(frequency="MONTHLY").update(effective_from=date(2026, 9, 1))
    _run(ist, 2026, 10, 5, 10, 0)  # first run after the scheduler missed 20 Sep (a Sunday)
    task = Task.objects.get(responsibility__code="BROKERAGE_CALCULATION")
    assert task.occurrence_date == date(2026, 9, 21)  # R24: next working day
    assert task.title == "Brokerage Calculation — 21 Sep 2026"
    assert not TaskSla.objects.filter(task=task, kind="RESOLUTION").exists()  # no SLA (approved)
    assert AuditLog.objects.filter(
        action="recurring.occurrence_recovered", context__schedule_id=task.schedule_id
    ).exists()


def test_monthly_occurrence_on_its_day_and_not_before(owners, ist):
    assert _run(ist, 2026, 10, 19, 10, 0)["generated"] == 3  # only the three daily ones
    _run(ist, 2026, 10, 20, 9, 59)
    assert not Task.objects.filter(responsibility__code="BROKERAGE_CALCULATION").exists()
    _run(ist, 2026, 10, 20, 10, 0)
    task = Task.objects.get(responsibility__code="BROKERAGE_CALCULATION")
    assert task.occurrence_date == date(2026, 10, 20) and task.assigned_to.full_name


# --- ownership --------------------------------------------------------------------------------


def test_no_owner_is_skipped_audited_and_reported(ops, make_user, ist):
    admin = make_user(roles.ADMIN)
    hr = make_user(roles.HR)
    summary = _run(ist, 2026, 10, 5, 10, 0)
    assert summary["generated"] == 0 and summary["skipped"] == 3
    assert not Task.objects.filter(source="SCHEDULED").exists()
    feed = ScheduleOccurrence.objects.get(schedule__responsibility__code="FEED_UPLOAD")
    assert feed.status == "SKIPPED" and "No responsible employee" in feed.detail
    row = AuditLog.objects.get(action="recurring.occurrence_skipped", entity_id=str(feed.pk))
    assert row.actor_user.email == generator.SCHEDULER_EMAIL
    warnings = Notification.objects.filter(kind="SCHEDULE_WARNING")
    warned = set(warnings.values_list("recipient", flat=True))
    assert {admin.pk, ops["manager"].pk} <= warned
    assert hr.pk not in warned and ops["rahul"].pk not in warned


def test_inactive_owner_is_skipped_not_reassigned(ops, resp, own, ist):
    own(resp("FEED_UPLOAD"), ops["rahul_emp"])
    ops["rahul_emp"].is_active = False
    ops["rahul_emp"].save()
    _run(ist, 2026, 10, 5, 10, 0)
    feed = ScheduleOccurrence.objects.get(schedule__responsibility__code="FEED_UPLOAD")
    assert feed.status == "SKIPPED" and "inactive" in feed.detail


def test_owner_changes_apply_to_future_occurrences_only(admin_user, ops, resp, ist):
    feed = resp("FEED_UPLOAD")
    # Locked rule A: HR / Admin change owners (this test used the Operations Manager before).
    with time_machine.travel(ist(2026, 10, 1, 9, 0), tick=False):
        assign_owner(actor=admin_user, responsibility=feed, employee=ops["rahul_emp"],
                     effective_from=date(2026, 10, 1))
    _run(ist, 2026, 10, 5, 10, 0)
    with time_machine.travel(ist(2026, 10, 6, 9, 0), tick=False):
        assign_owner(actor=admin_user, responsibility=feed, employee=ops["amit_emp"],
                     effective_from=date(2026, 10, 10))
    _run(ist, 2026, 10, 12, 10, 0)
    by_day = dict(
        Task.objects.filter(responsibility=feed).values_list("occurrence_date", "assigned_to")
    )
    assert by_day == {
        date(2026, 10, 5): ops["rahul_emp"].pk,
        date(2026, 10, 12): ops["amit_emp"].pk,  # history kept: 5 Oct stays with Rahul
    }
    periods = list(
        feed.owners.order_by("id").values_list("employee", "effective_from", "effective_to")
    )
    assert periods == [
        (ops["rahul_emp"].pk, date(2026, 10, 1), date(2026, 10, 9)),
        (ops["amit_emp"].pk, date(2026, 10, 10), None),
    ]


# --- delete, reassignment, manual work --------------------------------------------------------


def test_a_deleted_scheduled_task_is_never_recreated(client_for, make_user, owners, ist):
    _run(ist, 2026, 10, 5, 10, 0)
    task = _feed_task()
    hr = client_for(make_user(roles.HR))
    assert hr.delete(f"{TASKS}{task.pk}/?version={task.version}").status_code == 204
    occurrence = ScheduleOccurrence.objects.get(
        schedule=task.schedule, occurrence_date=task.occurrence_date
    )
    assert occurrence.status == "GENERATED" and occurrence.task is None
    deleted = AuditLog.objects.get(action="task.deleted")
    assert deleted.old_value["source"] == "SCHEDULED"
    assert deleted.context["occurrences_unlinked"] == 1
    _run(ist, 2026, 10, 5, 11, 0)
    assert not Task.objects.filter(responsibility__code="FEED_UPLOAD").exists()


def test_reassigning_a_scheduled_task_keeps_its_source_and_links(client_for, ops, owners, ist):
    _run(ist, 2026, 10, 5, 10, 0)
    task = _feed_task()
    response = client_for(ops["manager"]).post(
        f"{TASKS}{task.pk}/reassign/", {"version": task.version, "assigned_to": ops["amit_emp"].pk}
    )
    body = response.json()
    assert response.status_code == 200 and body["source"] == "SCHEDULED"
    assert body["responsibility"]["code"] == "FEED_UPLOAD"
    assert body["occurrence_date"] == "2026-10-05" and body["schedule"]["id"] == task.schedule_id
    assert TaskAssignment.objects.filter(task=task).count() == 2


def test_a_manual_task_with_the_same_title_stays_manual(client_for, ops, owners, ist, category):
    _run(ist, 2026, 10, 5, 10, 0)
    body = client_for(ops["manager"]).post(TASKS, {
        "title": "Feed Upload — urgent special case", "assigned_to": ops["rahul_emp"].pk,
        "department": ops["rahul_emp"].department_id, "category": category.pk,
    }).json()
    assert body["source"] == "MANUAL" and body["responsibility"] is None
    rahul = client_for(ops["rahul"])
    manual = rahul.get(TASKS, {"view": "received", "source": "manual"}).json()["results"]
    scheduled = rahul.get(TASKS, {"view": "received", "source": "scheduled"}).json()["results"]
    assert [t["title"] for t in manual] == ["Feed Upload — urgent special case"]
    assert len(scheduled) == 3
    assert rahul.get(TASKS, {"source": "auto"}).status_code == 400


# --- failures, activity flags, Celery ---------------------------------------------------------


def test_a_failure_is_recorded_audited_and_not_retried_forever(owners, ist, monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(generator, "create_scheduled_task", broken)
    summary = _run(ist, 2026, 10, 5, 10, 0)
    assert summary["failed"] == 3 and not Task.objects.filter(source="SCHEDULED").exists()
    failed = ScheduleOccurrence.objects.filter(status="FAILED")
    assert failed.count() == 3 and "database unavailable" in failed.first().detail
    assert AuditLog.objects.filter(action="recurring.occurrence_failed").count() == 3
    monkeypatch.undo()
    assert _run(ist, 2026, 10, 5, 10, 5)["generated"] == 0


def test_inactive_responsibility_schedule_or_ended_schedule_generate_nothing(owners, ist):
    Responsibility.objects.filter(code="FEED_UPLOAD").update(is_active=False)
    RecurringSchedule.objects.filter(responsibility__code="MAIL_CHECKING").update(is_active=False)
    RecurringSchedule.objects.filter(responsibility__code="SIP_STP_SWITCH_CHECKING").update(
        effective_to=date(2026, 10, 5)
    )
    # Only SIP/STP is generated: still in effect on its last day.
    assert _run(ist, 2026, 10, 5, 10, 0)["generated"] == 1
    assert _run(ist, 2026, 10, 6, 10, 0)["generated"] == 0


def test_celery_tasks_and_beat_schedule(owners, ist):
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        assert generate_recurring_tasks()["generated"] == 3
        assert "thresholds" in evaluate_sla_clocks()
    jobs = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
    assert jobs == {"recurring.generate_recurring_tasks", "sla.evaluate_sla_clocks",
                    # Phase 7.3 (approved E17): the two KRA performance runs
                    "performance.daily_kra_calculation", "performance.kra_month_close"}
    assert settings.CELERY_TIMEZONE == "Asia/Kolkata"


def test_management_command(owners, ist, capsys):
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        call_command("generate_recurring_tasks")
    assert "3 generated" in capsys.readouterr().out


def test_an_inactive_task_type_fails_the_occurrence_visibly(owners, ist):
    TaskTemplate.objects.filter(code="FEED_UPLOAD").update(is_active=False)
    _run(ist, 2026, 10, 5, 10, 0)
    feed = ScheduleOccurrence.objects.get(schedule__responsibility__code="FEED_UPLOAD")
    assert feed.status == "FAILED" and "not active" in feed.detail


def test_scheduled_creation_refuses_an_inactive_assignee(ops, resp, ist):
    ops["amit_emp"].is_active = False
    ops["amit_emp"].save()
    schedule = RecurringSchedule.objects.get(responsibility=resp("FEED_UPLOAD"))
    with pytest.raises(AssignmentNotAllowed):
        create_scheduled_task(
            scheduler=generator.scheduler_user(), schedule=schedule,
            occurrence_date=date(2026, 10, 5), assignee=ops["amit_emp"],
            generated_at=ist(2026, 10, 5, 10, 0),
        )
