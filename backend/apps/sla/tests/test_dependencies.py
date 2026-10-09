"""Task Dependency Engine (apps.tasks.dependencies): links from task-type configuration, satisfied
once, dependent SLA started when every prerequisite is satisfied (approved D1-D7).

Manual tasks here run WITHOUT a priority SLA mapping (the "unmapped priority" case), so a
Reconciliation task waits on its DEPENDENCY trigger; with a mapping the priority SLA wins (D7).
"""

import importlib
from datetime import datetime, timedelta
from io import StringIO

import pytest
import time_machine
from django.apps import apps as live_apps
from django.core.management import call_command
from django.db import IntegrityError, transaction

from apps.audit.models import AuditLog
from apps.core.timeutils import IST
from apps.overdue.models import OverdueCase
from apps.sla import services
from apps.sla.models import SlaRule, TaskSla
from apps.tasks import dependencies
from apps.tasks import services as task_services
from apps.tasks.models import Task, TaskDependency, TaskTemplate

pytestmark = pytest.mark.django_db
TASKS = "/api/v1/tasks/"
WAITING = "Not started — waiting for upstream task"
PRIORITY_HOURS = {"URGENT": 8, "HIGH": 24, "MEDIUM": 48, "LOW": 72}


def _at(*parts):
    return time_machine.travel(datetime(*parts, tzinfo=IST), tick=False)


def _clock(task):
    return TaskSla.objects.get(task=task, kind="RESOLUTION", is_current=True)


def _links(**filters):
    return list(TaskDependency.objects.filter(**filters).order_by("id"))


def _events(task):
    return list(
        AuditLog.objects.filter(
            entity_type="task", entity_id=str(task.pk), action__startswith="task.dependency_"
        )
        .order_by("id")
        .values_list("action", flat=True)
    )


def _start(task, user):
    task.refresh_from_db()
    return task_services.start_task(actor=user, task=task, version=task.version)


def _complete(task, user):
    task.refresh_from_db()
    if task.status == "PENDING":
        _start(task, user)
        task.refresh_from_db()
    return task_services.complete_task(actor=user, task=task, version=task.version)


def _verify(task, user):
    task.refresh_from_db()
    return task_services.verify_task(actor=user, task=task, version=task.version)


def _reject(task, user):
    task.refresh_from_db()
    return task_services.reject_verification(
        actor=user, task=task, version=task.version, reason="Figures differ", remarks="Redo"
    )


@pytest.fixture
def raise_task(ops, new_task, template):
    """raise_task(code, *parts, **fields): a manual task of a task type, raised by the Operations
    Manager for Rahul at the given IST time."""

    def _raise(code, *parts, **fields):
        with _at(*parts):
            return new_task(ops["manager"], ops["rahul_emp"], template=template(code), **fields)

    return _raise


@pytest.fixture
def chain(dept):
    """chain(state, verification=False, department="OPS"): a prerequisite task type and a
    DEPENDENCY-triggered dependent type (24 h rule) configured to wait for it."""

    def _chain(state, verification=False, department="OPS", rule="RECON_24H"):
        home = dept(department)
        suffix = f"{department}_{state}_{int(verification)}"
        prerequisite = TaskTemplate.objects.create(
            code=f"PREP_{suffix}", name=f"Prep {suffix}", department=home,
            trigger="ASSIGNMENT", verification_required=verification,
        )
        dependent = TaskTemplate.objects.create(
            code=f"AFTER_{suffix}", name=f"After {suffix}", department=home,
            resolution_rule_code=rule, trigger="DEPENDENCY",
            prerequisite_template=prerequisite, prerequisite_state=state,
        )
        return prerequisite, dependent

    return _chain


# --- 1-3. the model and the configuration ------------------------------------------------------


def test_a_dependency_link_is_stored_with_its_state_and_author(raise_task, ops):
    brokerage = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 0)
    recon = raise_task("RECONCILIATION", 2026, 10, 5, 9, 30)
    link = TaskDependency.objects.get(prerequisite=brokerage, dependent=recon)
    assert (link.required_state, link.satisfied_at, link.created_by) == (
        "COMPLETED", None, ops["manager"]
    )
    assert str(link) == f"{brokerage.pk} -> {recon.pk} (COMPLETED)"
    assert list(recon.prerequisite_links.all()) == [link]
    assert list(brokerage.dependent_links.all()) == [link]


def test_a_duplicate_pair_and_a_self_dependency_are_refused(raise_task, ops):
    brokerage = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 0)
    recon = raise_task("RECONCILIATION", 2026, 10, 5, 9, 30)
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskDependency.objects.create(prerequisite=brokerage, dependent=recon,
                                      required_state="COMPLETED")
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskDependency.objects.create(prerequisite=recon, dependent=recon,
                                      required_state="COMPLETED")
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskDependency.objects.create(prerequisite=recon, dependent=brokerage,
                                      required_state="DONE")
    assert TaskDependency.objects.count() == 1


def test_task_type_configuration_constraints(template):
    feed = template("FEED_UPLOAD")
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskTemplate.objects.filter(pk=feed.pk).update(prerequisite_template=feed,
                                                        prerequisite_state="COMPLETED")
    with pytest.raises(IntegrityError), transaction.atomic():  # a state needs a prerequisite
        TaskTemplate.objects.filter(pk=feed.pk).update(prerequisite_state="COMPLETED")
    with pytest.raises(IntegrityError), transaction.atomic():  # a prerequisite needs a state
        TaskTemplate.objects.filter(pk=feed.pk).update(
            prerequisite_template=template("BROKERAGE_CALCULATION")
        )


def test_reconciliation_is_configured_to_wait_for_brokerage_calculation(template):
    recon = template("RECONCILIATION")
    assert recon.prerequisite_template == template("BROKERAGE_CALCULATION")
    assert (recon.prerequisite_state, recon.trigger, recon.resolution_rule_code) == (
        "COMPLETED", "DEPENDENCY", "RECON_24H"
    )
    others = TaskTemplate.objects.exclude(code="RECONCILIATION")
    assert not others.filter(prerequisite_template__isnull=False).exists()


# --- 4-10. linking by configuration ----------------------------------------------------------


@pytest.mark.parametrize("dependent_first", [False, True])
def test_either_creation_order_links_the_pair(raise_task, dependent_first):
    if dependent_first:
        recon = raise_task("RECONCILIATION", 2026, 10, 5, 9, 0)
        brokerage = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 30)
    else:
        brokerage = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 0)
        recon = raise_task("RECONCILIATION", 2026, 10, 5, 9, 30)
    assert [(link.prerequisite, link.dependent) for link in _links()] == [(brokerage, recon)]
    assert _events(recon) == ["task.dependency_linked"]
    assert _clock(recon).start_at is None  # linked, still waiting


def test_only_the_same_business_date_matches(raise_task, ops):
    brokerage = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 23, 50)  # IST date 5 Oct
    next_day = raise_task("RECONCILIATION", 2026, 10, 6, 0, 10)  # IST date 6 Oct
    same_day = raise_task("RECONCILIATION", 2026, 10, 5, 23, 55)
    assert [(link.prerequisite, link.dependent) for link in _links()] == [(brokerage, same_day)]
    assert dependencies.business_date(next_day).isoformat() == "2026-10-06"
    with _at(2026, 10, 6, 1, 0):
        _complete(brokerage, ops["rahul"])
    assert _clock(next_day).start_at is None and _clock(same_day).start_at is not None


def test_another_department_or_task_type_never_matches(raise_task, dept):
    raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 0, department=dept("RM"))
    raise_task("FEED_UPLOAD", 2026, 10, 5, 9, 5)
    recon = raise_task("RECONCILIATION", 2026, 10, 5, 9, 30)
    assert _links() == [] and _events(recon) == []
    assert _clock(recon).start_at is None


# --- 11-16. satisfying and starting ------------------------------------------------------------


def test_a_completed_prerequisite_starts_the_dependent_sla(raise_task, ops, ist):
    brokerage = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 0)
    recon = raise_task("RECONCILIATION", 2026, 10, 5, 9, 30)
    with _at(2026, 10, 5, 12, 0):
        _complete(brokerage, ops["rahul"])
    link = TaskDependency.objects.get()
    assert link.satisfied_at == ist(2026, 10, 5, 12, 0)
    clock = _clock(recon)
    assert (clock.trigger, clock.rule_snapshot["code"]) == ("DEPENDENCY", "RECON_24H")
    assert clock.start_at == ist(2026, 10, 5, 12, 0)  # later than its 9:30 assignment (D3)
    assert clock.due_at == ist(2026, 10, 6, 12, 0)  # 24 h, from the existing rule
    assert _events(recon) == [
        "task.dependency_linked", "task.dependency_satisfied", "task.dependency_sla_started"
    ]
    recon.refresh_from_db()
    assert recon.status == "PENDING"  # the dependent's workflow is untouched


def test_a_prerequisite_finished_before_the_dependent_starts_it_at_its_assignment(
    raise_task, ops, ist
):
    brokerage = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 0)
    with _at(2026, 10, 5, 9, 30):
        _complete(brokerage, ops["rahul"])
    recon = raise_task("RECONCILIATION", 2026, 10, 5, 11, 0)
    assert TaskDependency.objects.get().satisfied_at == ist(2026, 10, 5, 9, 30)
    assert _clock(recon).start_at == ist(2026, 10, 5, 11, 0)  # max(9:30, 11:00) (D3)


def test_a_verified_prerequisite_needs_verification_not_completion(raise_task, chain, ops, ist):
    prep, after = chain("VERIFIED", verification=True)
    prerequisite = raise_task(prep.code, 2026, 10, 5, 9, 0)
    dependent = raise_task(after.code, 2026, 10, 5, 9, 30)
    with _at(2026, 10, 5, 11, 0):
        _complete(prerequisite, ops["rahul"])
    assert TaskDependency.objects.get().satisfied_at is None  # COMPLETED is not VERIFIED (D5)
    assert _clock(dependent).start_at is None
    with _at(2026, 10, 5, 12, 0):
        _verify(prerequisite, ops["manager"])
    assert TaskDependency.objects.get().satisfied_at == ist(2026, 10, 5, 12, 0)
    assert _clock(dependent).start_at == ist(2026, 10, 5, 12, 0)
    late = raise_task(after.code, 2026, 10, 5, 13, 0)  # linked to an already verified task
    assert _clock(late).start_at == ist(2026, 10, 5, 13, 0)


def test_a_completed_requirement_is_not_met_by_a_rejected_completion(raise_task, chain, ops):
    prep, after = chain("COMPLETED", verification=True)
    prerequisite = raise_task(prep.code, 2026, 10, 5, 9, 0)
    with _at(2026, 10, 5, 10, 0):
        _complete(prerequisite, ops["rahul"])
    with _at(2026, 10, 5, 10, 30):
        _reject(prerequisite, ops["manager"])
    dependent = raise_task(after.code, 2026, 10, 5, 11, 0)
    assert TaskDependency.objects.get().satisfied_at is None
    assert _clock(dependent).start_at is None


def test_a_pending_prerequisite_keeps_the_dependent_waiting(client_for, raise_task, ops):
    brokerage = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 0)
    recon = raise_task("RECONCILIATION", 2026, 10, 5, 9, 30)
    with _at(2026, 10, 5, 10, 0):
        _start(brokerage, ops["rahul"])  # in progress is not completed
        services.evaluate_clocks()
    sla = client_for(ops["manager"]).get(f"{TASKS}{recon.pk}/").json()["sla"]["resolution"]
    assert (sla["state"], sla["waiting_for"]) == ("NOT_STARTED", WAITING)
    assert TaskDependency.objects.get().satisfied_at is None


def test_a_dependent_starts_only_when_every_prerequisite_is_satisfied(raise_task, ops, ist):
    first = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 0)
    second = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 5)
    recon = raise_task("RECONCILIATION", 2026, 10, 5, 9, 30)
    assert len(_links(dependent=recon)) == 2
    with _at(2026, 10, 5, 11, 0):
        _complete(first, ops["rahul"])
    assert _clock(recon).start_at is None
    with _at(2026, 10, 5, 12, 0):
        _complete(second, ops["rahul"])
    assert _clock(recon).start_at == ist(2026, 10, 5, 12, 0)


def test_one_prerequisite_starts_all_of_its_dependents(raise_task, ops, ist):
    recon_a = raise_task("RECONCILIATION", 2026, 10, 5, 9, 0)
    recon_b = raise_task("RECONCILIATION", 2026, 10, 5, 9, 5)
    brokerage = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 30)
    assert len(_links(prerequisite=brokerage)) == 2
    with _at(2026, 10, 5, 12, 0):
        _complete(brokerage, ops["rahul"])
    assert _clock(recon_a).start_at == _clock(recon_b).start_at == ist(2026, 10, 5, 12, 0)


# --- 17-21. idempotency, rejection, cancellation, deletion -------------------------------------


def test_calling_the_engine_again_changes_nothing(raise_task, ops, ist):
    brokerage = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 0)
    recon = raise_task("RECONCILIATION", 2026, 10, 5, 9, 30)
    with _at(2026, 10, 5, 12, 0):
        _complete(brokerage, ops["rahul"])
    before = (list(TaskDependency.objects.values()), list(TaskSla.objects.values()),
              AuditLog.objects.count())
    brokerage.refresh_from_db()
    recon.refresh_from_db()
    later = ist(2026, 10, 5, 15, 0)
    assert dependencies.on_prerequisite_reached(brokerage, "COMPLETED", later, ops["rahul"]) == 0
    assert dependencies.link_new_task(recon, ops["manager"]) == 0
    assert dependencies.link_new_task(brokerage, ops["manager"]) == 0
    assert services.start_dependency_clock(recon, later) is None
    after = (list(TaskDependency.objects.values()), list(TaskSla.objects.values()),
             AuditLog.objects.count())
    assert after == before


def test_relinking_a_still_waiting_dependent_changes_nothing(raise_task, ops, ist):
    first = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 0)
    raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 5)
    recon = raise_task("RECONCILIATION", 2026, 10, 5, 9, 30)
    with _at(2026, 10, 5, 11, 0):
        _complete(first, ops["rahul"])
    before = (list(TaskDependency.objects.values()), _events(recon))
    recon.refresh_from_db()
    assert dependencies.is_waiting(recon)
    assert dependencies.link_new_task(recon, ops["manager"]) == 0  # the pairs exist already
    assert (list(TaskDependency.objects.values()), _events(recon)) == before
    assert _clock(recon).start_at is None


def test_complete_reject_complete_never_resets_the_dependency(raise_task, chain, ops, ist):
    prep, after = chain("COMPLETED", verification=True)
    prerequisite = raise_task(prep.code, 2026, 10, 5, 9, 0)
    dependent = raise_task(after.code, 2026, 10, 5, 9, 30)
    with _at(2026, 10, 5, 11, 0):
        _complete(prerequisite, ops["rahul"])
    started = _clock(dependent)
    with _at(2026, 10, 5, 11, 30):
        _reject(prerequisite, ops["manager"])  # no rollback (D6)
    with _at(2026, 10, 5, 12, 0):
        _complete(prerequisite, ops["rahul"])
    assert TaskDependency.objects.get().satisfied_at == ist(2026, 10, 5, 11, 0)
    clock = _clock(dependent)
    assert (clock.pk, clock.start_at, clock.due_at) == (
        started.pk, ist(2026, 10, 5, 11, 0), ist(2026, 10, 6, 11, 0)
    )
    assert _events(dependent).count("task.dependency_sla_started") == 1


def test_a_started_dependent_is_never_restarted_or_relinked(raise_task, ops, ist):
    brokerage = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 0)
    recon = raise_task("RECONCILIATION", 2026, 10, 5, 9, 30)
    with _at(2026, 10, 5, 12, 0):
        _complete(brokerage, ops["rahul"])
    late = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 13, 0)
    with _at(2026, 10, 5, 14, 0):
        _complete(late, ops["rahul"])
    assert _links(prerequisite=late) == []
    assert _clock(recon).start_at == ist(2026, 10, 5, 12, 0)


def test_a_cancelled_prerequisite_keeps_the_dependent_waiting(raise_task, ops):
    brokerage = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 0)
    recon = raise_task("RECONCILIATION", 2026, 10, 5, 9, 30)
    with _at(2026, 10, 5, 10, 0):
        brokerage.refresh_from_db()
        task_services.cancel_task(actor=ops["manager"], task=brokerage,
                                  version=brokerage.version, reason="Raised twice")
        services.evaluate_clocks()
    assert TaskDependency.objects.get().satisfied_at is None  # D4: no automatic start
    assert _clock(recon).start_at is None
    later = raise_task("RECONCILIATION", 2026, 10, 5, 11, 0)
    assert _links(dependent=later) == []  # a cancelled task is never linked


def test_deleting_a_prerequisite_removes_its_links_and_the_dependent_keeps_waiting(
    raise_task, admin_user
):
    brokerage = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 0)
    recon = raise_task("RECONCILIATION", 2026, 10, 5, 9, 30)
    brokerage.refresh_from_db()
    task_services.delete_task(actor=admin_user, task=brokerage, version=brokerage.version)
    assert not Task.objects.filter(pk=brokerage.pk).exists()
    assert _links() == [] and _clock(recon).start_at is None
    deleted = AuditLog.objects.get(action="task.deleted", entity_id=str(brokerage.pk))
    assert deleted.context["dependencies_unlinked"] == 1
    assert set(deleted.context["deleted_records"]) == {
        "assignments", "verifications", "comments", "attachments", "sla_clocks"
    }
    recon.refresh_from_db()
    task_services.delete_task(actor=admin_user, task=recon, version=recon.version)
    assert AuditLog.objects.get(
        action="task.deleted", entity_id=str(recon.pk)
    ).context["dependencies_unlinked"] == 0


def test_deleting_a_dependent_removes_its_link(raise_task, admin_user, ops):
    brokerage = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 0)
    recon = raise_task("RECONCILIATION", 2026, 10, 5, 9, 30)
    recon.refresh_from_db()
    task_services.delete_task(actor=admin_user, task=recon, version=recon.version)
    assert _links() == []
    with _at(2026, 10, 5, 12, 0):
        _complete(brokerage, ops["rahul"])  # nothing left to start
    assert TaskSla.objects.filter(task_id=recon.pk).count() == 0


def test_a_reverse_link_is_never_made(raise_task, dept):
    ops_dept = dept("OPS")
    first = TaskTemplate.objects.create(code="LOOP_A", name="Loop A", department=ops_dept,
                                        resolution_rule_code="RECON_24H", trigger="DEPENDENCY")
    second = TaskTemplate.objects.create(code="LOOP_B", name="Loop B", department=ops_dept,
                                         resolution_rule_code="RECON_24H", trigger="DEPENDENCY",
                                         prerequisite_template=first,
                                         prerequisite_state="COMPLETED")
    TaskTemplate.objects.filter(pk=first.pk).update(prerequisite_template=second,
                                                     prerequisite_state="COMPLETED")
    a = raise_task("LOOP_A", 2026, 10, 5, 9, 0)
    b = raise_task("LOOP_B", 2026, 10, 5, 9, 30)
    assert [(link.prerequisite, link.dependent) for link in _links()] == [(a, b)]


# --- 22, 25, 26. everything that is not a dependency stays exactly as it was -------------------


@pytest.mark.parametrize("priority", list(PRIORITY_HOURS))
def test_a_manual_reconciliation_keeps_its_priority_sla(raise_task, ops, ist, priority):
    call_command("configure_priority_sla", stdout=StringIO())
    brokerage = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 0)
    recon = raise_task("RECONCILIATION", 2026, 10, 5, 9, 30, priority=priority)
    clock = _clock(recon)
    assert (clock.trigger, clock.rule_snapshot["code"].startswith("PRIORITY_")) == (
        "ASSIGNMENT", True
    )
    assert clock.start_at == ist(2026, 10, 5, 9, 30)
    assert clock.due_at - clock.start_at == timedelta(hours=PRIORITY_HOURS[priority])
    assert _links() == []  # D7: the priority SLA is authoritative; nothing is linked
    before = list(TaskSla.objects.filter(task=recon).values())
    with _at(2026, 10, 5, 12, 0):
        _complete(brokerage, ops["rahul"])
    assert list(TaskSla.objects.filter(task=recon).values()) == before


def test_other_triggers_are_unchanged(raise_task, ops, ist):
    sip_failure = raise_task("SIP_FAILURE", 2026, 10, 5, 10, 0,
                             trigger_at=ist(2026, 10, 5, 9, 15))
    broker = raise_task("BROKER_MAPPING", 2026, 10, 5, 10, 0)
    feed = raise_task("FEED_UPLOAD", 2026, 10, 5, 11, 0)
    birthday = raise_task("BIRTHDAY_WISHES", 2026, 10, 5, 11, 0)
    brokerage = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 11, 30)
    with _at(2026, 10, 5, 12, 0):
        _complete(brokerage, ops["rahul"])
    assert (_clock(broker).trigger, _clock(broker).start_at) == (
        "ASSIGNMENT", ist(2026, 10, 5, 10, 0)
    )
    assert (_clock(sip_failure).trigger, _clock(sip_failure).start_at) == (
        "EVENT", ist(2026, 10, 5, 9, 15)
    )
    assert (_clock(feed).trigger, _clock(feed).start_at) == (
        "FIXED_TIME", ist(2026, 10, 5, 10, 0)
    )
    assert (_clock(birthday).trigger, _clock(birthday).start_at) == ("LOGIN", None)
    assert _links() == []
    assert not AuditLog.objects.filter(action__startswith="task.dependency_").exists()


def test_an_unlinked_dependency_task_still_never_starts_by_itself(raise_task, ops):
    recon = raise_task("RECONCILIATION", 2026, 10, 5, 9, 0)
    other = raise_task("FEED_UPLOAD", 2026, 10, 5, 9, 5)
    with _at(2026, 10, 6, 9, 0):
        _complete(other, ops["rahul"])
        services.evaluate_clocks()
    assert _links() == [] and _clock(recon).start_at is None
    assert services.task_sla(recon, datetime.now(IST))["resolution"]["waiting_for"] == WAITING


# --- 28-29. once started, the existing checker and the Phase 9 workflow take over -------------


def test_a_started_dependent_becomes_overdue_and_opens_one_overdue_case(raise_task, ops, ist):
    brokerage = raise_task("BROKERAGE_CALCULATION", 2026, 10, 5, 9, 0)
    recon = raise_task("RECONCILIATION", 2026, 10, 5, 9, 30)
    with _at(2026, 10, 5, 12, 0):
        _complete(brokerage, ops["rahul"])
    with _at(2026, 10, 6, 12, 1):
        services.evaluate_clocks()
        services.evaluate_clocks()  # a second checker run changes nothing
    clock = _clock(recon)
    assert (clock.state, clock.overdue_at) == ("OVERDUE", ist(2026, 10, 6, 12, 0))
    case = OverdueCase.objects.get(clock=clock)
    assert (case.task, case.status, case.sla_rule_code) == (recon, "OPEN", "RECON_24H")
    assert (case.sla_start_at, case.sla_due_at) == (clock.start_at, clock.due_at)


# --- 30. the same engine, another department, no engine change -------------------------------


def test_an_insurance_style_chain_works_without_engine_changes(
    admin_user, ops, new_task, chain, dept, ist
):
    SlaRule.objects.create(code="INS_SUBMIT_4H", version=1, name="Insurer submission",
                           rule_type="DURATION", clock="CALENDAR", duration_minutes=240)
    documents, submission = chain("VERIFIED", verification=True, department="INS",
                                  rule="INS_SUBMIT_4H")
    insurance = dept("INS")
    with _at(2026, 10, 5, 9, 0):
        submit = new_task(admin_user, ops["amit_emp"], template=submission, department=insurance)
    with _at(2026, 10, 5, 9, 30):
        docs = new_task(admin_user, ops["rahul_emp"], template=documents, department=insurance)
    with _at(2026, 10, 5, 9, 45):  # an Operations task of the same date is never involved
        new_task(ops["manager"], ops["rahul_emp"], template=documents)
    assert [(link.prerequisite, link.dependent) for link in _links()] == [(docs, submit)]
    with _at(2026, 10, 5, 11, 0):
        _complete(docs, ops["rahul"])
    assert _clock(submit).start_at is None
    with _at(2026, 10, 5, 11, 30):
        _verify(docs, admin_user)
    clock = _clock(submit)
    assert (clock.start_at, clock.due_at) == (ist(2026, 10, 5, 11, 30), ist(2026, 10, 5, 15, 30))


# --- the data migration ---------------------------------------------------------------------


def test_the_reconciliation_migration_is_idempotent_and_never_overwrites(template):
    migration = importlib.import_module("apps.tasks.migrations.0012_reconciliation_prerequisite")
    recon = template("RECONCILIATION")
    migration.configure(live_apps, None)  # already applied: nothing changes
    recon.refresh_from_db()
    assert (recon.prerequisite_template.code, recon.prerequisite_state) == (
        "BROKERAGE_CALCULATION", "COMPLETED"
    )
    TaskTemplate.objects.filter(pk=recon.pk).update(
        prerequisite_template=template("FEED_UPLOAD"), prerequisite_state="VERIFIED"
    )
    migration.configure(live_apps, None)  # an Admin's later choice is kept
    migration.unconfigure(live_apps, None)  # and not undone either
    recon.refresh_from_db()
    assert (recon.prerequisite_template.code, recon.prerequisite_state) == (
        "FEED_UPLOAD", "VERIFIED"
    )
    TaskTemplate.objects.filter(pk=recon.pk).update(prerequisite_template=None,
                                                     prerequisite_state="")
    migration.configure(live_apps, None)
    recon.refresh_from_db()
    assert recon.prerequisite_template == template("BROKERAGE_CALCULATION")
    migration.unconfigure(live_apps, None)
    recon.refresh_from_db()
    assert (recon.prerequisite_template, recon.prerequisite_state) == (None, "")
