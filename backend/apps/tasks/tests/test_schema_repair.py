"""The development database held task tables from an older Phase 3 attempt while migration
history said they were current. These tests rebuild that drift inside the test database
(PostgreSQL DDL is transactional, so it is rolled back afterwards) and prove that migration
0006 repairs it, and that `schema_audit` detects it.
"""

import importlib
from io import StringIO

import pytest
from django.apps import apps as live_apps
from django.core.management import CommandError, call_command
from django.db import connection
from django.utils import timezone

from apps.core.schema_check import audit
from apps.org.models import Department
from apps.tasks.models import Task, TaskVerification


repair = importlib.import_module(
    "apps.tasks.migrations.0006_repair_task_history_schema"
).repair


TASK_MODELS = [
    live_apps.get_model("tasks", name)
    for name in (
        "TaskTemplate",
        "Task",
        "TaskAssignment",
        "TaskVerification",
        "TaskComment",
        "TaskAttachment",
    )
]


pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def ensure_test_departments():
    Department.objects.get_or_create(
        code="OPS",
        defaults={
            "name": "Operations",
            "is_live": True,
        },
    )


@pytest.fixture(autouse=True)
def ensure_task_role_permissions(transactional_db):
    """
    transaction=True tests flush the database between tests.

    Django does not re-run data migrations after each flush, so the
    role permissions seeded by migration 0004 need to be restored
    before each test.

    This is test-only and does not modify production permission logic.
    """
    from django.apps import apps

    migration = importlib.import_module(
        "apps.accounts.migrations.0004_task_role_permissions"
    )

    migration.grant_task_permissions(apps, None)


def _problems():
    return {
        r.table: (r.missing_table, r.problems)
        for r in audit(TASK_MODELS, connection)
    }


def _recreate_the_old_attempt_schema():
    with connection.cursor() as cursor:
        for sql in (
            'ALTER TABLE tasks_verification RENAME COLUMN decided_by_id TO reviewer_id',
            'ALTER TABLE tasks_verification RENAME COLUMN rejection_reason TO reason',
            'ALTER TABLE tasks_verification DROP COLUMN submitted_at',
            'ALTER TABLE tasks_verification DROP COLUMN rework_seconds',
            'ALTER TABLE tasks_verification DROP CONSTRAINT tasks_rejection_needs_reason_chk',
            'ALTER TABLE tasks_verification ALTER COLUMN decision TYPE varchar(10)',
            'DROP TABLE tasks_attachment',
            'DROP TABLE tasks_comment',
            'ALTER TABLE tasks_task ALTER COLUMN rework_count TYPE smallint',
            "ALTER TABLE tasks_task ADD COLUMN category varchar(100) NOT NULL DEFAULT ''",
            'ALTER TABLE tasks_task ALTER COLUMN category DROP DEFAULT',
            'DROP INDEX tasks_dept_status_idx',
        ):
            cursor.execute(sql)


def test_clean_database_matches_every_model():
    assert all(r.ok for r in audit(TASK_MODELS, connection))

    call_command(
        "schema_audit",
        stdout=StringIO(),
    )


def test_repair_is_a_no_op_on_a_correct_database():
    with connection.schema_editor() as editor:
        repair(live_apps, editor)

    assert all(r.ok for r in audit(TASK_MODELS, connection))


def test_drift_is_detected_and_repaired_without_losing_rows(ops, new_task):
    task = new_task(
        ops["manager"],
        ops["rahul_emp"],
    )

    decided = timezone.now()

    row = TaskVerification.objects.create(
        task=task,
        cycle_no=1,
        submitted_at=decided,
        decision="VERIFIED",
        decided_by=ops["manager"],
        decided_at=decided,
    )

    _recreate_the_old_attempt_schema()

    found = _problems()

    assert found["tasks_comment"] == (True, [])
    assert found["tasks_attachment"] == (True, [])

    assert "missing column submitted_at" in found["tasks_verification"][1]
    assert "extra column reviewer_id (NOT NULL)" in found["tasks_verification"][1]
    assert "missing constraint/index tasks_dept_status_idx" in found["tasks_task"][1]

    out = StringIO()

    with pytest.raises(CommandError):
        call_command(
            "schema_audit",
            "tasks",
            stdout=out,
        )

    assert (
        "DRIFT    tasks_verification: missing column submitted_at"
        in out.getvalue()
    )

    with connection.schema_editor() as editor:
        repair(live_apps, editor)

    after = _problems()

    assert after.pop("tasks_task") == (
        False,
        ["extra column category (NULL)"],
    )

    assert all(
        value == (False, [])
        for value in after.values()
    ), after

    row.refresh_from_db()

    assert row.decided_by == ops["manager"]
    assert row.submitted_at == row.decided_at

    assert Task.objects.filter(
        pk=task.pk
    ).exists()


def test_not_null_column_without_backfill_stops_the_repair(
    ops,
    new_task,
):
    new_task(
        ops["manager"],
        ops["rahul_emp"],
    )

    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE tasks_task DROP COLUMN assigned_at"
        )

    with pytest.raises(
        RuntimeError,
        match="no backfill is defined",
    ):
        with connection.schema_editor() as editor:
            repair(live_apps, editor)