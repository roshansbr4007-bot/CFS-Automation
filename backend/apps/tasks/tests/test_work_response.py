"""Mandatory work response before completion.

Workflow: Assigned -> In Progress -> Submit work response -> Completed (no approval step).
The response is validated by the backend (required, trimmed, never blank), saved as an
append-only WORK_RESPONSE comment in the same transaction as the completion, and readable by
managers in the task details and comments. SLA, audit and lifecycle behaviour are unchanged.
"""

from datetime import datetime
from unittest import mock

import pytest
import time_machine

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.core.errors import FieldValidationError
from apps.core.timeutils import IST
from apps.overdue.models import OverdueCase
from apps.sla import services as sla_services
from apps.sla.models import TaskSla
from apps.tasks import services
from apps.tasks.models import CommentKind, Task, TaskComment, TaskStatus

TASKS = "/api/v1/tasks/"
WORK = "Reconciled the October SIP mandates with the RTA report; 3 mismatches corrected."
pytestmark = pytest.mark.django_db


def ist(*parts):
    return datetime(*parts, tzinfo=IST)


def _act(client, task, name, **body):
    task.refresh_from_db()
    return client.post(f"{TASKS}{task.pk}/{name}/", {"version": task.version, **body})


def _responses(task):
    return TaskComment.objects.filter(task=task, kind=CommentKind.WORK_RESPONSE)


def _completed_events(task):
    return AuditLog.objects.filter(action="task.completed", entity_id=str(task.pk))


@pytest.fixture
def task(ops, new_task):
    return new_task(ops["manager"], ops["rahul_emp"])


@pytest.fixture
def started(client_for, ops, task):
    assert _act(client_for(ops["rahul"]), task, "start").status_code == 200
    task.refresh_from_db()
    return task


def _assert_untouched(task, version):
    """A refused completion writes nothing: status, version, timestamps, comments, audit."""
    task.refresh_from_db()
    assert task.status == TaskStatus.IN_PROGRESS
    assert task.version == version
    assert task.completed_at is None and task.completed_by_id is None
    assert task.completion_recorded_at is None
    assert not _responses(task).exists()
    assert not _completed_events(task).exists()
    assert not TaskSla.objects.filter(task=task, stopped_at__isnull=False).exists()


# --- 1. start is unchanged --------------------------------------------------------------------


def test_assignee_can_start_without_a_response(client_for, ops, task):
    response = _act(client_for(ops["rahul"]), task, "start")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "IN_PROGRESS" and body["started_at"]
    assert "complete" in body["allowed_actions"]
    assert body["work_response"] is None


# --- 2-3. a missing or blank response is refused ----------------------------------------------


@pytest.mark.parametrize("body", [{}, {"work_response": ""}], ids=["missing", "empty"])
def test_missing_response_is_refused(client_for, ops, started, body):
    version = started.version  # captured before the attempt
    response = _act(client_for(ops["rahul"]), started, "complete", **body)
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert response.json()["fields"]["work_response"] == [
        "Describe the work you did before completing the task."
    ]
    _assert_untouched(started, version)


@pytest.mark.parametrize("blank", ["   ", "\n\t  \r\n", "\u00a0 "])
def test_whitespace_only_response_is_refused(client_for, ops, started, blank):
    version = started.version  # captured before the attempt
    response = _act(client_for(ops["rahul"]), started, "complete", work_response=blank)
    assert response.status_code == 400
    assert "work_response" in response.json()["fields"]
    _assert_untouched(started, version)


def test_null_and_too_long_responses_are_refused(client_for, ops, started):
    version = started.version  # captured before the attempt
    client = client_for(ops["rahul"])
    null = client.post(
        f"{TASKS}{started.pk}/complete/",
        {"version": started.version, "work_response": None},
        format="json",
    )
    assert null.status_code == 400 and "work_response" in null.json()["fields"]
    too_long = _act(client, started, "complete", work_response="x" * 5001)
    assert too_long.status_code == 400 and "work_response" in too_long.json()["fields"]
    _assert_untouched(started, version)


def test_the_service_itself_refuses_a_blank_response(ops, started):
    """The rule lives in the service, so no caller (API, command, future UI) can bypass it."""
    version = started.version  # captured before the attempt
    for blank in ("", "  \n ", None):
        with pytest.raises(FieldValidationError):
            services.complete_task(
                actor=ops["rahul"], task=started, version=started.version, work_response=blank
            )
    with pytest.raises(TypeError):
        services.complete_task(actor=ops["rahul"], task=started, version=started.version)
    _assert_untouched(started, version)


# --- 4-5. a valid response is saved and completes the task ------------------------------------


def test_valid_response_is_saved_trimmed_and_completes_directly(client_for, ops, started):
    with time_machine.travel(ist(2026, 10, 5, 15, 42, 7), tick=False):
        response = _act(
            client_for(ops["rahul"]), started, "complete", work_response=f"  \n{WORK}\n  "
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["completion_source"] == "DIRECT"
    assert body["completed_by"]["id"] == ops["rahul"].pk
    assert body["completed_at"] == "2026-10-05T15:42:07+05:30"
    assert body["work_response"]["body"] == WORK  # trimmed, otherwise unchanged
    assert body["work_response"]["kind"] == "WORK_RESPONSE"
    assert body["work_response"]["author"]["id"] == ops["rahul"].pk
    assert body["work_response"]["created_at"] == "2026-10-05T15:42:07+05:30"

    saved = _responses(started).get()
    assert saved.body == WORK and saved.author_id == ops["rahul"].pk
    started.refresh_from_db()
    # One instant: completion, the recorded completion and the response submission.
    assert started.completed_at == started.completion_recorded_at == saved.created_at
    assert started.completed_at == ist(2026, 10, 5, 15, 42, 7)


def test_inner_text_is_preserved_exactly(client_for, ops, started):
    text = "Line one\n\n  - indented item\nLine three"
    _act(client_for(ops["rahul"]), started, "complete", work_response=f"\n{text}\n")
    assert _responses(started).get().body == text


def test_completion_audit_references_the_response(client_for, ops, started):
    _act(client_for(ops["rahul"]), started, "complete", work_response=WORK)
    saved = _responses(started).get()
    event = _completed_events(started).get()
    assert event.actor_user_id == ops["rahul"].pk
    assert event.new_value["work_response_id"] == saved.pk
    assert event.new_value["work_response_length"] == len(WORK)
    assert event.new_value["status"] == "COMPLETED"
    # The response is part of the completion event, not a separate comment event.
    assert not AuditLog.objects.filter(action="task.comment_added").exists()


# --- 6. atomic --------------------------------------------------------------------------------


def test_response_and_completion_are_atomic(ops, started):
    """If anything in the completion fails after the response row was written, neither the
    response nor the completion survives."""
    version = started.version  # captured before the attempt
    with mock.patch.object(services.sla, "on_completed", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            services.complete_task(
                actor=ops["rahul"], task=started, version=started.version, work_response=WORK
            )
    _assert_untouched(started, version)


# --- 7. authorization -------------------------------------------------------------------------


def test_other_users_cannot_complete_even_with_a_response(client_for, ops, started, admin_client):
    version = started.version  # captured before the attempt
    for client in (client_for(ops["amit"]), client_for(ops["manager"]), admin_client):
        response = _act(client, started, "complete", work_response=WORK)
        assert response.status_code in (403, 404)
    _assert_untouched(started, version)


@pytest.mark.parametrize(
    "extra",
    [{}, {"work_response": ""}, {"work_response": "x" * 5001}, {"work_response": None}],
    ids=["missing", "empty", "too-long", "null"],
)
def test_a_non_assignee_is_refused_before_the_response_is_judged(client_for, ops, started, extra):
    """403, not a validation error: whatever the body holds, the response rule is judged only
    for the assignee."""
    started.refresh_from_db()
    response = client_for(ops["manager"]).post(
        f"{TASKS}{started.pk}/complete/", {"version": started.version, **extra}, format="json"
    )
    assert response.status_code == 403


def test_a_completed_task_is_a_409_whatever_the_body_holds(client_for, ops, started):
    client = client_for(ops["rahul"])
    _act(client, started, "complete", work_response=WORK)
    started.refresh_from_db()
    for extra in ({}, {"work_response": None}, {"work_response": "x" * 5001}):
        response = client.post(
            f"{TASKS}{started.pk}/complete/", {"version": started.version, **extra}, format="json"
        )
        assert response.status_code == 409
        assert response.json()["code"] == "invalid_state_transition"
    assert _responses(started).count() == 1


# --- 8. invalid transitions -------------------------------------------------------------------


@pytest.mark.parametrize("status", [TaskStatus.PENDING, TaskStatus.BLOCKED, TaskStatus.CANCELLED])
def test_invalid_transitions_are_refused_even_with_a_response(client_for, ops, task, status):
    Task.objects.filter(pk=task.pk).update(status=status)
    response = _act(client_for(ops["rahul"]), task, "complete", work_response=WORK)
    assert response.status_code == 409
    assert response.json()["code"] == "invalid_state_transition"
    task.refresh_from_db()
    assert task.status == status and task.completed_at is None
    assert not _responses(task).exists()


def test_acknowledgment_gate_still_applies(client_for, ops, new_task):
    task = new_task(ops["manager"], ops["rahul_emp"], acknowledgment_required=True)
    Task.objects.filter(pk=task.pk).update(status=TaskStatus.IN_PROGRESS)
    response = _act(client_for(ops["rahul"]), task, "complete", work_response=WORK)
    assert response.status_code == 409 and response.json()["code"] == "acknowledgment_required"
    assert not _responses(task).exists()


# --- 9. repeated requests ---------------------------------------------------------------------


def test_repeated_submission_does_not_duplicate_or_move_timestamps(client_for, ops, started):
    client = client_for(ops["rahul"])
    version = started.version
    with time_machine.travel(ist(2026, 10, 5, 15, 0), tick=False):
        first = client.post(
            f"{TASKS}{started.pk}/complete/", {"version": version, "work_response": WORK}
        )
    assert first.status_code == 200
    with time_machine.travel(ist(2026, 10, 5, 15, 5), tick=False):
        # A double-click resends the same version; a retry after reload sends the new one.
        stale = client.post(
            f"{TASKS}{started.pk}/complete/", {"version": version, "work_response": "Again"}
        )
        fresh = _act(client, started, "complete", work_response="Again")
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
    assert fresh.status_code == 409 and fresh.json()["code"] == "invalid_state_transition"
    started.refresh_from_db()
    assert started.completed_at == started.completion_recorded_at == ist(2026, 10, 5, 15, 0)
    assert list(_responses(started).values_list("body", flat=True)) == [WORK]
    assert _completed_events(started).count() == 1


# --- 10-11. managers read it; no approval -----------------------------------------------------


def test_managers_read_the_response_in_details_and_comments(client_for, ops, staff, started):
    hr, _ = staff(roles.HR)
    _act(client_for(ops["rahul"]), started, "complete", work_response=WORK)
    for reader in (ops["manager"], hr, ops["rahul"]):
        client = client_for(reader)
        detail = client.get(f"{TASKS}{started.pk}/")
        assert detail.status_code == 200
        assert detail.json()["work_response"]["body"] == WORK
        comments = client.get(f"{TASKS}{started.pk}/comments/").json()
        assert [(c["kind"], c["body"]) for c in comments] == [("WORK_RESPONSE", WORK)]


def test_no_manager_approval_is_needed(client_for, ops, started):
    body = _act(client_for(ops["rahul"]), started, "complete", work_response=WORK).json()
    assert body["status"] == "COMPLETED"
    assert body["verification_status"] == "NOT_REQUIRED"
    assert "verify" not in body["allowed_actions"]
    assert body["verifications"] == []


def test_the_comments_api_cannot_create_or_alter_a_work_response(client_for, ops, started):
    client = client_for(ops["rahul"])
    _act(client, started, "complete", work_response=WORK)
    posted = client.post(
        f"{TASKS}{started.pk}/comments/", {"body": "Forged", "kind": "WORK_RESPONSE"}
    )
    assert posted.status_code == 201 and posted.json()["kind"] == "COMMENT"
    started.refresh_from_db()
    detail = client.get(f"{TASKS}{started.pk}/").json()
    assert detail["work_response"]["body"] == WORK
    assert detail["completed_at"] == started.completed_at.astimezone(IST).isoformat()
    # The comments endpoint offers no edit or delete.
    comment_id = _responses(started).get().pk
    for method in ("put", "patch", "delete"):
        response = getattr(client, method)(f"{TASKS}{started.pk}/comments/{comment_id}/")
        assert response.status_code in (404, 405)
    assert _responses(started).get().body == WORK


def test_a_reopened_task_needs_a_new_response_and_keeps_the_first(client_for, ops, new_task):
    task = new_task(ops["manager"], ops["rahul_emp"], verification_required=True)
    rahul, manager = client_for(ops["rahul"]), client_for(ops["manager"])
    _act(rahul, task, "start")
    _act(rahul, task, "complete", work_response="First attempt")
    rejected = _act(manager, task, "reject-verification", reason="Wrong folio", remarks="Fix")
    assert rejected.json()["status"] == "IN_PROGRESS"
    assert rejected.json()["work_response"]["body"] == "First attempt"  # history is kept
    assert _act(rahul, task, "complete").status_code == 400
    again = _act(rahul, task, "complete", work_response="Corrected folio 1234").json()
    assert again["status"] == "COMPLETED" and again["verification_status"] == "PENDING"
    assert again["work_response"]["body"] == "Corrected folio 1234"
    assert list(_responses(task).order_by("id").values_list("body", flat=True)) == [
        "First attempt",
        "Corrected folio 1234",
    ]


# --- 12. SLA and overdue ----------------------------------------------------------------------


@pytest.fixture
def sla_60(admin_user):
    """HIGH manual tasks get a 60-minute resolution SLA (overdue at 11:00 for a 10:00 task)."""
    sla_services.create_rule(
        actor=admin_user, code="WORK_RESPONSE_60M", name="One hour", duration_minutes=60
    )
    sla_services.set_priority_rule(actor=admin_user, priority="HIGH", rule_code="WORK_RESPONSE_60M")


def test_sla_stops_at_the_completion_and_overdue_is_unchanged(client_for, ops, new_task, sla_60):
    with time_machine.travel(ist(2026, 10, 5, 10, 0), tick=False):
        on_time = new_task(ops["manager"], ops["rahul_emp"], priority="HIGH")
        late = new_task(ops["manager"], ops["amit_emp"], priority="HIGH")
    rahul, amit = client_for(ops["rahul"]), client_for(ops["amit"])
    with time_machine.travel(ist(2026, 10, 5, 10, 30), tick=False):
        _act(rahul, on_time, "start")
        _act(amit, late, "start")
    with time_machine.travel(ist(2026, 10, 5, 10, 40), tick=False):
        # A refused attempt does not stop the clock.
        assert _act(rahul, on_time, "complete", work_response=" ").status_code == 400
    assert TaskSla.objects.get(task=on_time, kind="RESOLUTION").stopped_at is None
    with time_machine.travel(ist(2026, 10, 5, 10, 50), tick=False):
        body = _act(rahul, on_time, "complete", work_response=WORK).json()
    assert body["sla"]["resolution"]["outcome"] == "MET"
    with time_machine.travel(ist(2026, 10, 5, 11, 1), tick=False):
        sla_services.evaluate_clocks()
    assert OverdueCase.objects.filter(task=late).count() == 1
    assert not OverdueCase.objects.filter(task=on_time).exists()
    with time_machine.travel(ist(2026, 10, 5, 11, 30), tick=False):
        body = _act(amit, late, "complete", work_response=WORK).json()
    assert body["sla"]["resolution"]["outcome"] == "MISSED"
    clock = TaskSla.objects.get(task=late, kind="RESOLUTION", is_current=True)
    assert clock.stopped_at == ist(2026, 10, 5, 11, 30) == _responses(late).get().created_at
    assert OverdueCase.objects.filter(task=late).count() == 1  # no second case


# --- migration 0013 ---------------------------------------------------------------------------


def test_migration_keeps_existing_comments_as_ordinary_comments(ops, task):
    """Rolling 0013 back and forward again (the same path as applying it to a database that
    already has comments) keeps every row and makes each one an ordinary COMMENT. PostgreSQL
    DDL is transactional, so the test's own transaction undoes all of it afterwards."""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    services.add_comment(actor=ops["manager"], task=task, body="Please use folio 1234")
    services.add_comment(actor=ops["rahul"], task=task, body="Noted")
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")  # no pending FK checks before ALTER TABLE
    MigrationExecutor(connection).migrate([("tasks", "0012_reconciliation_prerequisite")])
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'tasks_comment' AND column_name = 'kind'"
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT count(*) FROM tasks_comment")
        assert cursor.fetchone()[0] == 2
    MigrationExecutor(connection).migrate([("tasks", "0013_taskcomment_kind")])
    rows = list(TaskComment.objects.order_by("id").values_list("body", "kind"))
    assert rows == [("Please use folio 1234", "COMMENT"), ("Noted", "COMMENT")]
