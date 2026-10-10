"""Approved S4-S7, Q1, Q4: arrival facts of generated tasks.

Recorded once in the task's own recurring.task_generated audit entry; read back (in two queries
whatever the number of tasks) through the task's occurrence, and only from an entry that names
the task. Shown read-only on the daily-activity responses and the task detail, next to the
unchanged `scheduled_start`. Tasks without recorded facts get null facts, never guesses.
"""

from datetime import date, datetime, time

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.audit.models import AuditLog
from apps.audit.services import record
from apps.recurring.generator import scheduler_user
from apps.recurring.models import RecurringSchedule, ScheduleOccurrence
from apps.tasks import monitoring
from apps.tasks import services as task_services
from apps.tasks.models import TaskTemplate

from .scheduled_helpers import at, clock, duty, ist, only, run, task_of, task_type

pytestmark = pytest.mark.django_db
TASKS = "/api/v1/tasks/"
MINE = "/api/v1/tasks/daily-activities/"
OPS_EMP = "/api/v1/operations/employees/{pk}/daily-activities/"
TEAM_EMP = "/api/v1/operations/team/employees/{pk}/daily-activities/"
CC_EMP = "/api/v1/command-center/employees/{pk}/"
FACTS = ("scheduled_at", "sla_start_at", "arrived_overdue", "ack_arrived_overdue")


def _broker():
    return TaskTemplate.objects.get(code="BROKER_MAPPING")  # resolution 24 h, acknowledgment 2 h


def _entry(task):
    return AuditLog.objects.get(action="recurring.task_generated", new_value__task_id=task.pk)


def _detail(client, task, *now):
    with at(*now):
        return client.get(f"{TASKS}{task.pk}/").json()


def _facts(task):
    return monitoring.arrival_facts([task])[task.pk]


# --- recording ----------------------------------------------------------------------------------


def test_facts_are_recorded_once_in_the_generation_entry(admin_user, ops):
    schedule = only(duty(ops["rahul_emp"], admin_user, template=_broker()))
    run(2026, 10, 5, 12, 30)
    task = task_of(schedule)
    context = _entry(task).context
    assert context["scheduled_at"] == "2026-10-05T10:00:00+05:30"
    assert datetime.fromisoformat(context["arrived_at"]) == ist(2026, 10, 5, 12, 30)
    assert context["resolution_overdue_on_arrival"] is False  # 24 h clock
    assert context["ack_overdue_on_arrival"] is True  # 2 h clock: 10:00-12:00
    assert context["template_trigger"] == "ASSIGNMENT"
    assert (context["recovered"], context["delay_seconds"]) == (True, 9000)  # existing keys kept


def test_a_task_without_a_task_type_records_a_null_trigger(admin_user, ops):
    schedule = only(duty(ops["rahul_emp"], admin_user))  # ad-hoc, no SLA configured
    run(2026, 10, 5, 10, 0)
    context = _entry(task_of(schedule)).context
    assert context["template_trigger"] is None
    assert context["resolution_overdue_on_arrival"] is None  # no clock at all
    assert context["ack_overdue_on_arrival"] is None


# --- Q1: the badge is about resolution only; acknowledgment is separate -------------------------


def test_resolution_and_acknowledgment_facts_are_separate(admin_user, ops, admin_client):
    late_ack = only(duty(ops["rahul_emp"], admin_user, template=_broker()))
    run(2026, 10, 5, 12, 30)
    body = _detail(admin_client, task_of(late_ack), 2026, 10, 5, 12, 40)
    assert (body["arrived_overdue"], body["ack_arrived_overdue"]) == (False, True)
    assert body["scheduled_at"] == "2026-10-05T10:00:00+05:30"

    both = only(duty(ops["rahul_emp"], admin_user, template=task_type("ASSIGNMENT", ack=True),
                     start=date(2026, 10, 6)))
    run(2026, 10, 6, 12, 30)
    body = _detail(admin_client, task_of(both), 2026, 10, 6, 12, 40)
    assert (body["arrived_overdue"], body["ack_arrived_overdue"]) == (True, True)

    on_time = only(duty(ops["rahul_emp"], admin_user, template=task_type("ASSIGNMENT"),
                        start=date(2026, 10, 7)))
    run(2026, 10, 7, 10, 0)
    body = _detail(admin_client, task_of(on_time), 2026, 10, 7, 10, 5)
    assert (body["arrived_overdue"], body["ack_arrived_overdue"]) == (False, None)


def test_a_manual_task_has_no_arrival_facts(ops, new_task, admin_client):
    with at(2026, 10, 5, 9, 0):
        task = new_task(ops["manager"], ops["rahul_emp"], template=_broker())
    body = _detail(admin_client, task, 2026, 10, 5, 9, 5)
    assert {k: body[k] for k in ("scheduled_at", "arrived_overdue", "ack_arrived_overdue")} == {
        "scheduled_at": None, "arrived_overdue": None, "ack_arrived_overdue": None,
    }


# --- 13: facts never change afterwards ----------------------------------------------------------


def test_hold_resume_reassignment_and_completion_never_change_the_facts(admin_user, ops):
    schedule = only(duty(ops["rahul_emp"], admin_user,
                         template=TaskTemplate.objects.get(code="FEED_UPLOAD")))
    run(2026, 10, 5, 12, 30)
    task = task_of(schedule)
    before = _facts(task)
    assert before["arrived_overdue"] is True
    with at(2026, 10, 5, 12, 40):
        task_services.block_task(actor=ops["rahul"], task=task, version=task.version,
                                 reason="Waiting")
    with at(2026, 10, 5, 14, 0):
        task.refresh_from_db()
        task_services.unblock_task(actor=ops["rahul"], task=task, version=task.version)
    with at(2026, 10, 5, 14, 5):
        task.refresh_from_db()
        task_services.reassign_task(actor=ops["manager"], task=task, version=task.version,
                                    assigned_to=ops["amit_emp"])
    with at(2026, 10, 5, 14, 10):
        task.refresh_from_db()
        task_services.start_task(actor=ops["amit"], task=task, version=task.version)
        task.refresh_from_db()
        task_services.complete_task(
            actor=ops["amit"], task=task, version=task.version, work_response="Work done."
        )
    task.refresh_from_db()
    assert _facts(task) == before
    assert clock(task).start_at == ist(2026, 10, 5, 11, 20)  # the hold moved the SLA start ...
    assert before["scheduled_at"] == ist(2026, 10, 5, 10, 0)  # ... never the scheduled time


def test_a_later_run_time_change_never_moves_the_recorded_scheduled_time(admin_user, ops):
    schedule = only(duty(ops["rahul_emp"], admin_user, template=task_type("ASSIGNMENT")))
    run(2026, 10, 5, 10, 0)
    RecurringSchedule.objects.filter(pk=schedule.pk).update(run_time=time(11, 0))
    task = task_of(schedule)
    task.refresh_from_db()
    assert _facts(task)["scheduled_at"] == ist(2026, 10, 5, 10, 0)


# --- Q4 / 17: missing, foreign and duplicate entries ---------------------------------------------


def _pre_fix_task(admin_user, ops, *, entry_extra=None, with_entry=True):
    """A generated task as the code BEFORE this fix left it: same task, occurrence and entry,
    but no facts in the entry (or no entry at all)."""
    schedule = only(duty(ops["rahul_emp"], admin_user, template=task_type("ASSIGNMENT")))
    with at(2026, 10, 5, 12, 0):
        task = task_services.create_scheduled_task(
            scheduler=scheduler_user(), schedule=schedule, occurrence_date=date(2026, 10, 5),
            assignee=ops["rahul_emp"], generated_at=ist(2026, 10, 5, 12, 0),
        )
        occurrence = ScheduleOccurrence.objects.create(
            schedule=schedule, occurrence_date=date(2026, 10, 5), status="GENERATED",
            assignee=ops["rahul_emp"], generated_at=ist(2026, 10, 5, 12, 0), task=task,
        )
        if with_entry:
            record(action="recurring.task_generated", entity_type="schedule_occurrence",
                   entity_id=occurrence.pk, new={"status": "GENERATED", "task_id": task.pk},
                   use_request_user=False, extra={"recovered": True, "delay_seconds": 7200,
                                                  **(entry_extra or {})})
    return schedule, occurrence, task


@pytest.mark.parametrize("with_entry", [True, False])
def test_a_task_generated_before_the_fix_gets_null_facts(with_entry, admin_user, ops,
                                                         admin_client):
    schedule, _, task = _pre_fix_task(admin_user, ops, with_entry=with_entry)
    RecurringSchedule.objects.filter(pk=schedule.pk).update(run_time=time(9, 30))
    body = _detail(admin_client, task, 2026, 10, 5, 12, 5)
    assert body["scheduled_at"] == "2026-10-05T09:30:00+05:30"  # best effort: current run time
    assert (body["arrived_overdue"], body["ack_arrived_overdue"]) == (None, None)
    with at(2026, 10, 5, 12, 5):
        row = admin_client.get(OPS_EMP.format(pk=ops["rahul_emp"].pk)).json()["activities"][0]
    assert (row["arrived_overdue"], row["ack_arrived_overdue"]) == (None, None)


def test_an_entry_naming_another_task_is_ignored(admin_user, ops):
    _, occurrence, task = _pre_fix_task(admin_user, ops, with_entry=False)
    record(action="recurring.task_generated", entity_type="schedule_occurrence",
           entity_id=occurrence.pk, new={"status": "GENERATED", "task_id": task.pk + 999},
           use_request_user=False, extra={"scheduled_at": "2026-10-05T08:00:00+05:30",
                                          "resolution_overdue_on_arrival": True,
                                          "ack_overdue_on_arrival": True})
    assert _facts(task)["arrived_overdue"] is None


def test_with_two_entries_the_first_one_wins(admin_user, ops):
    first = {"scheduled_at": "2026-10-05T10:00:00+05:30", "resolution_overdue_on_arrival": True,
             "ack_overdue_on_arrival": None}
    _, occurrence, task = _pre_fix_task(admin_user, ops, entry_extra=first)
    record(action="recurring.task_generated", entity_type="schedule_occurrence",
           entity_id=occurrence.pk, new={"status": "GENERATED", "task_id": task.pk},
           use_request_user=False, extra={"scheduled_at": "2026-10-05T11:00:00+05:30",
                                          "resolution_overdue_on_arrival": False,
                                          "ack_overdue_on_arrival": False})
    assert _facts(task) == {"scheduled_at": ist(2026, 10, 5, 10, 0), "arrived_overdue": True,
                            "ack_arrived_overdue": None}


def test_each_task_gets_the_facts_of_its_own_occurrence(admin_user, ops):
    on_time = duty(ops["rahul_emp"], admin_user, template=task_type("ASSIGNMENT"))
    weekly = duty(ops["rahul_emp"], admin_user, template=task_type("ASSIGNMENT"),
                  frequency="WEEKLY", weekdays=[0, 2], start=date(2026, 10, 5))
    RecurringSchedule.objects.exclude(pk__in=[on_time.pk, weekly.pk]).update(is_active=False)
    run(2026, 10, 5, 10, 0)  # Monday: both on time
    run(2026, 10, 9, 11, 0)  # Friday: daily Friday late; weekly Wednesday caught up
    tasks = [task_of(on_time, date(2026, 10, 5)), task_of(on_time, date(2026, 10, 9)),
             task_of(weekly, date(2026, 10, 5)), task_of(weekly, date(2026, 10, 7))]
    facts = monitoring.arrival_facts(tasks)
    assert [(facts[t.pk]["scheduled_at"], facts[t.pk]["arrived_overdue"]) for t in tasks] == [
        (ist(2026, 10, 5, 10, 0), False), (ist(2026, 10, 9, 10, 0), False),
        (ist(2026, 10, 5, 10, 0), False), (ist(2026, 10, 7, 10, 0), True),
    ]


def test_a_deleted_task_leaves_the_others_untouched(admin_user, ops):
    first = duty(ops["rahul_emp"], admin_user, template=task_type("ASSIGNMENT"))
    second = duty(ops["rahul_emp"], admin_user, template=task_type("ASSIGNMENT"))
    RecurringSchedule.objects.exclude(pk__in=[first.pk, second.pk]).update(is_active=False)
    run(2026, 10, 5, 12, 30)
    gone, kept = task_of(first), task_of(second)
    with at(2026, 10, 5, 12, 40):
        task_services.delete_task(actor=admin_user, task=gone, version=gone.version)
    assert ScheduleOccurrence.objects.get(schedule=first).task is None
    assert _facts(kept)["arrived_overdue"] is True


def test_the_lookup_takes_two_queries_whatever_the_number_of_tasks(admin_user, ops):
    schedules = [duty(ops["rahul_emp"], admin_user, template=task_type("ASSIGNMENT"))
                 for _ in range(5)]
    RecurringSchedule.objects.exclude(pk__in=[s.pk for s in schedules]).update(is_active=False)
    run(2026, 10, 5, 10, 0)
    tasks = [task_of(s) for s in schedules]

    def count(chosen):
        with CaptureQueriesContext(connection) as captured:
            monitoring.arrival_facts(chosen)
        return len(captured)

    assert count(tasks[:1]) == count(tasks) == 2
    assert count([]) == 0


# --- 19: API compatibility on every daily-activity response ------------------------------------


def test_every_daily_activity_response_carries_the_new_fields_and_keeps_scheduled_start(
    admin_user, ops, admin_client, client_for
):
    recon = only(duty(ops["rahul_emp"], admin_user,
                      template=TaskTemplate.objects.get(code="RECONCILIATION")))
    run(2026, 10, 5, 12, 30)  # waits on its dependency: no SLA start yet
    with at(2026, 10, 5, 12, 40):
        mine = client_for(ops["rahul"]).get(MINE).json()["activities"]
        team = client_for(ops["manager"]).get(TEAM_EMP.format(pk=ops["rahul_emp"].pk)).json()
        everyone = admin_client.get(OPS_EMP.format(pk=ops["rahul_emp"].pk)).json()
        command = admin_client.get(CC_EMP.format(pk=ops["rahul_emp"].pk)).json()
    rows = [mine[0], team["activities"][0], everyone["activities"][0],
            command["daily_activities"][0]]
    for row in rows:
        assert row["task_id"] == task_of(recon).pk
        assert row["scheduled_start"] == "2026-10-05T10:00:00+05:30"  # unchanged meaning
        assert {k: row[k] for k in FACTS} == {
            "scheduled_at": "2026-10-05T10:00:00+05:30", "sla_start_at": None,
            "arrived_overdue": None, "ack_arrived_overdue": None,
        }


def test_after_a_hold_sla_start_and_scheduled_time_differ(admin_user, ops, admin_client):
    schedule = only(duty(ops["rahul_emp"], admin_user,
                         template=TaskTemplate.objects.get(code="FEED_UPLOAD")))
    run(2026, 10, 5, 10, 0)
    task = task_of(schedule)
    with at(2026, 10, 5, 10, 30):
        task_services.block_task(actor=ops["rahul"], task=task, version=task.version,
                                 reason="Waiting")
    with at(2026, 10, 5, 11, 0):
        task.refresh_from_db()
        task_services.unblock_task(actor=ops["rahul"], task=task, version=task.version)
    with at(2026, 10, 5, 11, 5):
        row = admin_client.get(OPS_EMP.format(pk=ops["rahul_emp"].pk)).json()["activities"][0]
    assert row["scheduled_at"] == "2026-10-05T10:00:00+05:30"
    assert row["sla_start_at"] == row["scheduled_start"] == "2026-10-05T10:30:00+05:30"
    assert row["arrived_overdue"] is False
