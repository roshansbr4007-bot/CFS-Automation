"""Phase 5.2: same-day responsibility owner correction, and generated activities that are
reassigned after a correction. Ownership history is never deleted or rewritten."""

from datetime import date

import pytest
import time_machine

from apps.audit.models import AuditLog
from apps.recurring import generator
from apps.recurring.models import ResponsibilityOwner
from apps.recurring.services import assign_owner, current_owner_row
from apps.sla.models import TaskSla
from apps.tasks.models import Task, TaskAssignment

pytestmark = pytest.mark.django_db
TASKS = "/api/v1/tasks/"


def _owners_url(responsibility):
    return f"/api/v1/responsibilities/{responsibility.pk}/owners/"


def test_same_day_correction_supersedes_the_mistaken_owner_and_keeps_history(
    client_for, admin_user, ops, resp, ist
):
    """Monday 5 Oct: the Admin was set up as owner from today by mistake; the operations
    employee is made owner from today. The Admin row is kept, marked superseded."""
    feed = resp("FEED_UPLOAD")
    with time_machine.travel(ist(2026, 10, 5, 8, 0), tick=False):
        mistaken = assign_owner(
            actor=admin_user, responsibility=feed, employee=ops["manager_emp"],
            effective_from=date(2026, 10, 5),
        )
        response = client_for(ops["manager"]).post(
            _owners_url(feed), {"employee": ops["rahul_emp"].pk, "effective_from": "2026-10-05"}
        )
        assert response.status_code == 201
        assert current_owner_row(feed).employee == ops["rahul_emp"]
    mistaken.refresh_from_db()
    assert mistaken.superseded_at is not None and mistaken.superseded_by == ops["manager"]
    assert ResponsibilityOwner.objects.filter(responsibility=feed).count() == 2  # nothing deleted
    history = client_for(ops["manager"]).get(_owners_url(feed)).json()
    assert [(h["employee"]["id"], h["superseded_at"] is not None) for h in history] == [
        (ops["manager_emp"].pk, True),
        (ops["rahul_emp"].pk, False),
    ]
    row = AuditLog.objects.get(action="responsibility.owner_corrected")
    assert row.old_value["employee_id"] == ops["manager_emp"].pk and row.old_value["superseded"]
    assert row.new_value == {"employee_id": ops["rahul_emp"].pk, "effective_from": "2026-10-05"}


def test_generator_uses_the_corrected_owner_the_same_day(admin_user, ops, resp, ist):
    feed = resp("FEED_UPLOAD")
    with time_machine.travel(ist(2026, 10, 5, 8, 0), tick=False):
        assign_owner(actor=admin_user, responsibility=feed, employee=ops["manager_emp"],
                     effective_from=date(2026, 10, 5))
        assign_owner(actor=admin_user, responsibility=feed, employee=ops["rahul_emp"],
                     effective_from=date(2026, 10, 5))
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        generator.generate_due_occurrences()
    task = Task.objects.get(responsibility=feed)
    assert task.assigned_to == ops["rahul_emp"]  # not the superseded owner


def test_a_future_dated_mistake_can_be_corrected_from_today(admin_user, ops, resp, ist):
    feed = resp("FEED_UPLOAD")
    with time_machine.travel(ist(2026, 10, 5, 8, 0), tick=False):
        future = assign_owner(actor=admin_user, responsibility=feed, employee=ops["manager_emp"],
                              effective_from=date(2026, 10, 9))
        assert current_owner_row(feed) is None  # nobody owns it today yet
        assign_owner(actor=admin_user, responsibility=feed, employee=ops["rahul_emp"],
                     effective_from=date(2026, 10, 5))
        assert current_owner_row(feed).employee == ops["rahul_emp"]
        assert current_owner_row(feed, date(2026, 10, 9)).employee == ops["rahul_emp"]
    future.refresh_from_db()
    assert future.superseded_at is not None and future.effective_to is None


def test_corrections_are_only_for_today(client_for, admin_user, ops, resp, ist):
    feed = resp("FEED_UPLOAD")
    with time_machine.travel(ist(2026, 10, 5, 8, 0), tick=False):
        assign_owner(actor=admin_user, responsibility=feed, employee=ops["manager_emp"],
                     effective_from=date(2026, 10, 9))
        later = client_for(ops["manager"]).post(
            _owners_url(feed), {"employee": ops["rahul_emp"].pk, "effective_from": "2026-10-08"}
        )
    assert later.status_code == 400 and "effective_from" in later.json()["fields"]
    assert not ResponsibilityOwner.objects.filter(superseded_at__isnull=False).exists()


def test_a_generated_activity_is_reassigned_without_moving_its_schedule(
    client_for, admin_user, ops, resp, ist
):
    """Today's activity was generated for the mistaken owner: it is not deleted or duplicated;
    it is reassigned, keeping its 10:00 start and 12:00 deadline and its assignment history."""
    feed = resp("FEED_UPLOAD")
    with time_machine.travel(ist(2026, 10, 5, 8, 0), tick=False):
        assign_owner(actor=admin_user, responsibility=feed, employee=ops["manager_emp"],
                     effective_from=date(2026, 10, 5))
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        generator.generate_due_occurrences()
    task = Task.objects.get(responsibility=feed)
    assert task.assigned_to == ops["manager_emp"]
    with time_machine.travel(ist(2026, 10, 5, 10, 20), tick=False):
        assign_owner(actor=admin_user, responsibility=feed, employee=ops["rahul_emp"],
                     effective_from=date(2026, 10, 5))
        moved = client_for(ops["manager"]).post(
            f"{TASKS}{task.pk}/reassign/",
            {"version": task.version, "assigned_to": ops["rahul_emp"].pk, "note": "Owner fix"},
        )
        assert moved.status_code == 200
        assert generator.generate_due_occurrences()["generated"] == 0  # no duplicate
    task.refresh_from_db()
    assert task.assigned_to == ops["rahul_emp"] and task.source == "SCHEDULED"
    clock = TaskSla.objects.get(task=task, kind="RESOLUTION")
    assert clock.start_at == ist(2026, 10, 5, 10, 0) and clock.due_at == ist(2026, 10, 5, 12, 0)
    hops = list(TaskAssignment.objects.filter(task=task).order_by("id").values_list(
        "from_employee", "to_employee"))
    assert hops == [(None, ops["manager_emp"].pk), (ops["manager_emp"].pk, ops["rahul_emp"].pk)]
    assert Task.objects.filter(responsibility=feed).count() == 1
