"""Scheduling fix (approved S1-S3, D7 A): a generated task's SLA clocks start at its SCHEDULED
time (occurrence date + the schedule's run time, IST), whenever it is generated and whoever
signs in when. Dependency task types keep the dependency engine's start; manual tasks are
unchanged. Every test drives the real generator, task services and SLA checker.

Seeded calendar: Sundays off; 1st and 3rd Saturdays working (Sat 10 Oct 2026 is off).
"""

from datetime import date, time, timedelta

import pytest

from apps.audit.models import AuditLog
from apps.org.services import record_daily_login
from apps.overdue.models import OverdueCase
from apps.recurring.models import ScheduleOccurrence
from apps.recurring.services import assign_owner
from apps.sla import services as sla_services
from apps.sla.models import SlaSetting, TaskSla
from apps.tasks import dependencies
from apps.tasks import services as task_services
from apps.tasks.models import Task, TaskDependency, TaskTemplate

from .scheduled_helpers import at, clock, duty, ist, only, run, task_of, task_type, tick

pytestmark = pytest.mark.django_db
TASKS = "/api/v1/tasks/"


def _login(user, employee, *parts):
    """What the two login signal handlers do, in a fixed order, at a fixed time."""
    with at(*parts):
        record_daily_login(user=user, at=ist(*parts))
        return sla_services.start_login_clocks(employee.pk, ist(*parts))


# --- 1. every non-dependency trigger starts at the scheduled time ------------------------------


@pytest.mark.parametrize("trigger", ["ASSIGNMENT", "LOGIN", "FIXED_TIME", "EVENT"])
def test_a_late_generated_task_starts_every_clock_at_the_scheduled_time(
    trigger, admin_user, ops
):
    kind = task_type(trigger, fixed_time=time(9, 0) if trigger == "FIXED_TIME" else None,
                     ack=True)
    schedule = only(duty(ops["rahul_emp"], admin_user, template=kind))
    run(2026, 10, 5, 12, 30)  # the scheduler was down from 10:00 to 12:30
    task = task_of(schedule)
    resolution, ack = clock(task), clock(task, "ACK")
    assert task.assigned_at == task.generated_at == ist(2026, 10, 5, 12, 30)
    assert (resolution.trigger, resolution.start_at, resolution.due_at) == (
        "FIXED_TIME", ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 12, 0),
    )
    assert (ack.trigger, ack.start_at, ack.due_at) == (
        "FIXED_TIME", ist(2026, 10, 5, 10, 0), ist(2026, 10, 5, 12, 0),
    )
    assert task.trigger_at is None  # no event time is invented


def test_an_on_time_generated_task_is_unchanged_by_the_fix(admin_user, ops):
    """The seeded daily types are already 'Fixed time 10:00': on-time generation is identical."""
    schedule = only(duty(ops["rahul_emp"], admin_user,
                         template=TaskTemplate.objects.get(code="FEED_UPLOAD")))
    run(2026, 10, 5, 10, 0)
    resolution = clock(task_of(schedule))
    assert (resolution.start_at, resolution.due_at) == (ist(2026, 10, 5, 10, 0),
                                                        ist(2026, 10, 5, 12, 0))


def test_a_task_without_a_task_type_runs_its_priority_sla_from_the_scheduled_time(
    admin_user, ops
):
    sla_services.create_rule(actor=admin_user, code="PRIO_LOW_4H", name="Low",
                             duration_minutes=240)
    sla_services.set_priority_rule(actor=admin_user, priority="LOW", rule_code="PRIO_LOW_4H")
    schedule = only(duty(ops["rahul_emp"], admin_user))  # ad-hoc: no task type, LOW priority
    run(2026, 10, 5, 10, 20)
    resolution = clock(task_of(schedule))
    assert (resolution.trigger, resolution.rule_snapshot["code"]) == ("FIXED_TIME", "PRIO_LOW_4H")
    assert (resolution.start_at, resolution.due_at) == (ist(2026, 10, 5, 10, 0),
                                                        ist(2026, 10, 5, 14, 0))


# --- 2. manual tasks keep their existing start --------------------------------------------------


def test_manual_tasks_keep_their_assignment_and_trigger_starts(ops, new_task):
    broker = TaskTemplate.objects.get(code="BROKER_MAPPING")  # ASSIGNMENT, 24 h, acknowledgment
    with at(2026, 10, 5, 11, 0):
        task = new_task(ops["manager"], ops["rahul_emp"], template=broker)
    for kind in ("RESOLUTION", "ACK"):
        assert (clock(task, kind).trigger, clock(task, kind).start_at) == (
            "ASSIGNMENT", ist(2026, 10, 5, 11, 0),
        )
    login_type = task_type("LOGIN")
    with at(2026, 10, 5, 9, 0):
        waiting = new_task(ops["manager"], ops["rahul_emp"], template=login_type)
    assert (clock(waiting).trigger, clock(waiting).start_at) == ("LOGIN", None)
    assert _login(ops["rahul"], ops["rahul_emp"], 2026, 10, 5, 9, 30) == 1
    assert clock(waiting).start_at == ist(2026, 10, 5, 9, 30)  # the login still starts it


def test_a_reassignment_restarts_acknowledgment_at_the_reassignment(admin_user, ops):
    schedule = only(duty(ops["rahul_emp"], admin_user, template=task_type("ASSIGNMENT", ack=True)))
    run(2026, 10, 5, 10, 0)
    task = task_of(schedule)
    with at(2026, 10, 5, 10, 30):
        task_services.reassign_task(actor=ops["manager"], task=task, version=task.version,
                                    assigned_to=ops["amit_emp"])
    ack = clock(task, "ACK")
    assert (ack.trigger, ack.start_at) == ("ASSIGNMENT", ist(2026, 10, 5, 10, 30))
    assert clock(task).start_at == ist(2026, 10, 5, 10, 0)  # resolution is never reset


# --- 3. login cannot start or move a scheduled clock --------------------------------------------


def test_a_login_before_or_after_the_scheduled_time_never_starts_or_moves_the_clock(
    admin_user, ops
):
    SlaSetting.objects.update_or_create(pk=1, defaults={"login_fallback_time": time(9, 0)})
    schedule = only(duty(ops["rahul_emp"], admin_user, template=task_type("LOGIN")))
    assert _login(ops["rahul"], ops["rahul_emp"], 2026, 10, 5, 9, 30) == 0  # nothing yet
    run(2026, 10, 5, 10, 0)
    task = task_of(schedule)
    assert (clock(task).start_at, clock(task).trigger) == (ist(2026, 10, 5, 10, 0), "FIXED_TIME")
    assert _login(ops["rahul"], ops["rahul_emp"], 2026, 10, 5, 11, 0) == 0
    tick(2026, 10, 5, 11, 1)  # also runs the login-fallback starter
    assert clock(task).start_at == ist(2026, 10, 5, 10, 0)
    assert clock(task).due_at == ist(2026, 10, 5, 12, 0)


# --- 4. fixed-time types follow the schedule's run time -----------------------------------------


@pytest.mark.parametrize("fixed", [time(9, 0), time(11, 0)])
def test_a_fixed_time_type_with_another_time_follows_the_schedule(fixed, admin_user, ops):
    schedule = only(duty(ops["rahul_emp"], admin_user, template=task_type("FIXED_TIME",
                                                                          fixed_time=fixed)))
    run(2026, 10, 5, 10, 0)
    assert clock(task_of(schedule)).start_at == ist(2026, 10, 5, 10, 0)  # the run time wins


# --- 5 / 6. event types -------------------------------------------------------------------------


def test_a_scheduled_event_task_runs_from_the_scheduled_time_and_is_monitored(
    admin_user, ops, admin_client
):
    event = task_type("EVENT", rule="SIP_FAILURE_24H")
    schedule = only(duty(ops["rahul_emp"], admin_user, template=event))
    run(2026, 10, 5, 10, 0)
    task = task_of(schedule)
    resolution = clock(task)
    assert (resolution.trigger, resolution.start_at, resolution.due_at) == (
        "FIXED_TIME", ist(2026, 10, 5, 10, 0), ist(2026, 10, 6, 10, 0),
    )
    with at(2026, 10, 5, 12, 0):
        body = admin_client.get(f"{TASKS}{task.pk}/").json()
    assert body["trigger_at"] is None
    assert body["sla"]["resolution"]["trigger"] == "FIXED_TIME"
    assert body["sla"]["resolution"]["waiting_for"] is None  # never "waiting for upstream"
    assert tick(2026, 10, 5, 22, 0)["thresholds"] == 1  # 50% after 12 of 24 hours
    tick(2026, 10, 6, 10, 0)
    resolution.refresh_from_db()
    assert resolution.overdue_at == ist(2026, 10, 6, 10, 0)
    assert OverdueCase.objects.filter(task=task).count() == 1
    generated = AuditLog.objects.get(action="recurring.task_generated")
    assert generated.context["template_trigger"] == "EVENT"  # the original setting is kept


def test_manual_event_tasks_are_unchanged(ops, new_task):
    sip = TaskTemplate.objects.get(code="SIP_FAILURE")
    with at(2026, 10, 5, 11, 0):
        task = new_task(ops["manager"], ops["rahul_emp"], template=sip,
                        trigger_at=ist(2026, 10, 5, 9, 15))
    resolution = clock(task)
    assert (resolution.trigger, resolution.start_at, resolution.due_at) == (
        "EVENT", ist(2026, 10, 5, 9, 15), ist(2026, 10, 6, 9, 15),
    )


def test_an_event_type_with_a_prerequisite_never_waits_or_links_as_a_dependent(
    admin_user, ops, new_task
):
    brokerage = TaskTemplate.objects.get(code="BROKERAGE_CALCULATION")
    event = task_type("EVENT", rule="SIP_FAILURE_24H", prerequisite_template=brokerage,
                      prerequisite_state="COMPLETED")
    with at(2026, 10, 5, 9, 0):
        new_task(ops["manager"], ops["rahul_emp"], template=brokerage)
    schedule = only(duty(ops["rahul_emp"], admin_user, template=event))
    run(2026, 10, 5, 10, 0)
    task = task_of(schedule)
    assert not dependencies.is_waiting(task)
    assert not TaskDependency.objects.filter(dependent=task).exists()
    assert clock(task).start_at == ist(2026, 10, 5, 10, 0)


# --- 7. dependency types keep the dependency engine's start ------------------------------------


def test_a_scheduled_dependency_task_still_waits_and_starts_from_its_prerequisite(
    admin_user, ops, new_task
):
    recon = TaskTemplate.objects.get(code="RECONCILIATION")  # DEPENDENCY on Brokerage, 24 h
    schedule = only(duty(ops["rahul_emp"], admin_user, template=recon))
    run(2026, 10, 5, 12, 0)  # late: still no backdating of a dependency clock
    task = task_of(schedule)
    waiting = clock(task)
    assert (waiting.trigger, waiting.start_at, waiting.due_at) == ("DEPENDENCY", None, None)
    assert dependencies.is_waiting(task)
    with at(2026, 10, 5, 14, 0):
        brokerage = new_task(ops["manager"], ops["rahul_emp"],
                             template=TaskTemplate.objects.get(code="BROKERAGE_CALCULATION"))
    assert TaskDependency.objects.get(dependent=task).prerequisite == brokerage
    with at(2026, 10, 5, 15, 0):
        brokerage.refresh_from_db()
        task_services.start_task(actor=ops["rahul"], task=brokerage, version=brokerage.version)
        brokerage.refresh_from_db()
        task_services.complete_task(actor=ops["rahul"], task=brokerage, version=brokerage.version)
    started = clock(task)
    assert (started.trigger, started.start_at) == ("DEPENDENCY", ist(2026, 10, 5, 15, 0))
    generated = AuditLog.objects.get(action="recurring.task_generated")
    assert generated.context["resolution_overdue_on_arrival"] is None  # it had not started


# --- HOLD after an overdue arrival --------------------------------------------------------------


def test_a_hold_after_an_overdue_arrival_shifts_the_deadline_and_keeps_the_facts(
    admin_user, ops
):
    schedule = only(duty(ops["rahul_emp"], admin_user,
                         template=TaskTemplate.objects.get(code="FEED_UPLOAD")))
    run(2026, 10, 5, 12, 30)  # 10:00-12:00 clock, already overdue on arrival
    task = task_of(schedule)
    tick(2026, 10, 5, 12, 31)
    assert clock(task).overdue_at == ist(2026, 10, 5, 12, 0)
    with at(2026, 10, 5, 12, 40):
        task.refresh_from_db()
        task_services.block_task(actor=ops["rahul"], task=task, version=task.version,
                                 reason="Waiting for the feed file")
    with at(2026, 10, 5, 13, 40):
        task.refresh_from_db()
        task_services.unblock_task(actor=ops["rahul"], task=task, version=task.version)
    resumed = clock(task)
    assert (resumed.start_at, resumed.due_at) == (ist(2026, 10, 5, 11, 0), ist(2026, 10, 5, 13, 0))
    assert resumed.overdue_at == ist(2026, 10, 5, 12, 0)  # a recorded threshold never moves
    assert OverdueCase.objects.filter(task=task).count() == 1
    generated = AuditLog.objects.get(action="recurring.task_generated")
    assert generated.context["resolution_overdue_on_arrival"] is True
    assert generated.context["scheduled_at"] == "2026-10-05T10:00:00+05:30"


# --- end-of-day deadlines -----------------------------------------------------------------------


def test_an_end_of_day_deadline_is_the_end_of_the_occurrence_date(admin_user, ops):
    SlaSetting.objects.update_or_create(pk=1, defaults={"company_work_end": time(18, 0)})
    mail = task_type("LOGIN", rule="MAIL_SAME_DAY")
    schedule = only(duty(ops["rahul_emp"], admin_user, template=mail, frequency="WEEKLY",
                         weekdays=[2], start=date(2026, 10, 1)))  # Wednesdays
    run(2026, 10, 8, 11, 0)  # Thursday: Wednesday 7 Oct is caught up a day late
    resolution = clock(task_of(schedule, date(2026, 10, 7)))
    assert (resolution.start_at, resolution.due_at) == (ist(2026, 10, 7, 10, 0),
                                                        ist(2026, 10, 7, 18, 0))
    assert sla_services.overdue_on_arrival(resolution) is True


def test_a_run_time_after_the_end_of_day_is_overdue_as_soon_as_it_starts(admin_user, ops):
    """Documented existing engine behaviour, not new: END_OF_DAY is the end of the start's date."""
    SlaSetting.objects.update_or_create(pk=1, defaults={"company_work_end": time(18, 0)})
    schedule = only(duty(ops["rahul_emp"], admin_user, run_time=time(19, 0),
                         template=task_type("ASSIGNMENT", rule="MAIL_SAME_DAY")))
    run(2026, 10, 5, 19, 0)
    resolution = clock(task_of(schedule))
    assert resolution.due_at == ist(2026, 10, 5, 18, 0) < resolution.start_at
    assert sla_services.overdue_on_arrival(resolution) is True


# --- 15. every generation path ------------------------------------------------------------------


def _start(schedule, day):
    return clock(task_of(schedule, day)).start_at


def test_weekly_catch_up_starts_on_its_own_date(admin_user, ops):
    schedule = only(duty(ops["rahul_emp"], admin_user, template=task_type("ASSIGNMENT"),
                         frequency="WEEKLY", weekdays=[2], start=date(2026, 10, 1)))
    run(2026, 10, 9, 11, 0)  # Friday: Wednesday 7 Oct is generated late (within the cycle)
    assert _start(schedule, date(2026, 10, 7)) == ist(2026, 10, 7, 10, 0)


def test_monthly_catch_up_starts_on_its_resolved_business_date(admin_user, ops):
    schedule = only(duty(ops["rahul_emp"], admin_user, template=task_type("ASSIGNMENT"),
                         frequency="MONTHLY", day_of_month=20, policy="NEXT_WORKING_DAY",
                         start=date(2026, 9, 1)))
    run(2026, 10, 5, 10, 30)  # 20 Sep 2026 was a Sunday -> Monday 21 Sep, caught up now
    assert _start(schedule, date(2026, 9, 21)) == ist(2026, 9, 21, 10, 0)


def test_a_one_off_date_generated_late_starts_on_its_date(admin_user, ops):
    schedule = only(duty(ops["rahul_emp"], admin_user, template=task_type("ASSIGNMENT"),
                         frequency="ONCE", run_date=date(2026, 10, 14), start=date(2026, 10, 14),
                         end=date(2026, 10, 14)))
    run(2026, 10, 15, 11, 0)
    assert _start(schedule, date(2026, 10, 14)) == ist(2026, 10, 14, 10, 0)


def test_a_same_day_owner_recovery_keeps_the_scheduled_start(admin_user, ops):
    schedule = only(duty(None, admin_user, template=task_type("ASSIGNMENT")))
    run(2026, 10, 5, 10, 0)
    occurrence = ScheduleOccurrence.objects.get(schedule=schedule)
    assert occurrence.status == "SKIPPED"
    with at(2026, 10, 5, 16, 0):
        assign_owner(actor=admin_user, responsibility=schedule.responsibility,
                     employee=ops["rahul_emp"], effective_from=date(2026, 10, 5))
    run(2026, 10, 5, 16, 50)
    occurrence.refresh_from_db()
    assert occurrence.status == "GENERATED" and occurrence.task.assigned_to == ops["rahul_emp"]
    assert clock(occurrence.task).start_at == ist(2026, 10, 5, 10, 0)
    entries = AuditLog.objects.filter(action="recurring.task_generated",
                                      entity_id=str(occurrence.pk))
    assert entries.count() == 1
    assert entries.get().context["resolution_overdue_on_arrival"] is True  # 2 h clock, 16:50


def test_earlier_daily_days_are_still_missed_not_backfilled(admin_user, ops):
    schedule = only(duty(ops["rahul_emp"], admin_user, template=task_type("ASSIGNMENT"),
                         start=date(2026, 10, 1)))
    run(2026, 10, 5, 10, 0)
    ledger = dict(schedule.occurrences.values_list("occurrence_date", "status"))
    assert ledger[date(2026, 10, 5)] == "GENERATED"
    assert {s for d, s in ledger.items() if d < date(2026, 10, 5)} == {"MISSED"}
    assert Task.objects.filter(schedule=schedule).count() == 1


# --- 16. FAILED stays FAILED --------------------------------------------------------------------


def test_a_failed_occurrence_stays_failed_and_creates_nothing(admin_user, ops):
    kind = task_type("ASSIGNMENT")
    schedule = only(duty(ops["rahul_emp"], admin_user, template=kind))
    TaskTemplate.objects.filter(pk=kind.pk).update(is_active=False)  # generation will fail
    run(2026, 10, 5, 10, 0)
    TaskTemplate.objects.filter(pk=kind.pk).update(is_active=True)
    run(2026, 10, 5, 10, 5)  # never retried
    occurrence = ScheduleOccurrence.objects.get(schedule=schedule)
    assert occurrence.status == "FAILED" and occurrence.task is None
    assert not Task.objects.filter(schedule=schedule).exists()
    assert not AuditLog.objects.filter(action="recurring.task_generated").exists()


def test_repeated_runs_create_one_task_and_one_generation_entry(admin_user, ops):
    schedule = only(duty(ops["rahul_emp"], admin_user, template=task_type("ASSIGNMENT")))
    for minute in (30, 31, 45):
        run(2026, 10, 5, 12, minute)
    assert Task.objects.filter(schedule=schedule).count() == 1
    assert AuditLog.objects.filter(action="recurring.task_generated").count() == 1


# --- 14. completion before the first monitor pass ------------------------------------------------


def test_completing_before_any_monitor_pass_fabricates_no_alert(admin_user, ops):
    schedule = only(duty(ops["rahul_emp"], admin_user, template=task_type("ASSIGNMENT")))
    run(2026, 10, 5, 12, 30)  # arrives overdue (10:00-12:00)
    task = task_of(schedule)
    with at(2026, 10, 5, 12, 35):
        task_services.start_task(actor=ops["rahul"], task=task, version=task.version)
        task.refresh_from_db()
        task_services.complete_task(actor=ops["rahul"], task=task, version=task.version)
    result = tick(2026, 10, 5, 12, 36)
    assert result["thresholds"] == 0
    resolution = clock(task)
    assert (resolution.warning_at, resolution.critical_at) == (None, None)
    assert resolution.outcome == "MISSED"  # completed after its scheduled deadline
    on_time = only(duty(ops["rahul_emp"], admin_user, template=task_type("ASSIGNMENT"),
                        start=date(2026, 10, 6)))
    run(2026, 10, 6, 10, 0)
    early = task_of(on_time)
    with at(2026, 10, 6, 10, 10):
        task_services.start_task(actor=ops["rahul"], task=early, version=early.version)
        early.refresh_from_db()
        task_services.complete_task(actor=ops["rahul"], task=early, version=early.version)
    assert tick(2026, 10, 6, 12, 30)["thresholds"] == 0
    entry = AuditLog.objects.get(action="recurring.task_generated",
                                 new_value__task_id=early.pk)
    assert entry.context["resolution_overdue_on_arrival"] is False  # never guessed as overdue


def test_clock_start_is_never_after_the_generation(admin_user, ops):
    """A generated occurrence is always due, so its clocks never start in the future."""
    schedule = only(duty(ops["rahul_emp"], admin_user, template=task_type("ASSIGNMENT")))
    run(2026, 10, 5, 10, 0)
    task = task_of(schedule)
    assert clock(task).start_at <= task.assigned_at
    assert clock(task).due_at - clock(task).start_at == timedelta(hours=2)
    assert TaskSla.objects.filter(task=task, start_at__gt=task.assigned_at).count() == 0
