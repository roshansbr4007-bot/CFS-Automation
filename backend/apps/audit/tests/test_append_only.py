import pytest
from django.db import DatabaseError, connection, transaction

from apps.accounts.models import User
from apps.accounts.services import create_user
from apps.audit.models import AuditLog, AuditLogImmutable
from apps.audit.services import record

pytestmark = pytest.mark.django_db


@pytest.fixture
def row():
    return record(action="test.action", entity_type="thing", entity_id=1, use_request_user=False)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_log SET action = 'tampered'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ],
)
def test_l1_database_refuses_changes(row, sql):
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(sql)
    row.refresh_from_db()
    assert row.action == "test.action"


def test_l2_python_refuses_changes(row):
    row.action = "tampered"
    with pytest.raises(AuditLogImmutable):
        row.save()
    with pytest.raises(AuditLogImmutable):
        row.delete()
    with pytest.raises(AuditLogImmutable):
        AuditLog.objects.filter(pk=row.pk).update(action="tampered")
    with pytest.raises(AuditLogImmutable):
        AuditLog.objects.all().delete()


def test_l3_audit_rolls_back_with_the_change(admin_user):
    with pytest.raises(RuntimeError), transaction.atomic():
        create_user(actor=admin_user, email="rollback@example.com", password="Ledger-Rollback-2026")
        assert AuditLog.objects.filter(action="user.created").exists()
        raise RuntimeError("the surrounding work failed")
    assert not User.objects.filter(email="rollback@example.com").exists()
    assert not AuditLog.objects.filter(action="user.created").exists()


def test_system_action_has_null_actor(row):
    assert row.actor_user is None
    assert row.request_id is None  # no HTTP request
