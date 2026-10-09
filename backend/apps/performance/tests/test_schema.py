"""The schema audit compares numeric precision and scale (exposed by the Performance models,
the first with decimal columns). Each mutation runs inside a savepoint that is always rolled
back, and the test re-audits to prove the original column is restored."""

from contextlib import contextmanager

import pytest
from django.apps import apps
from django.db import connection, transaction

from apps.core.schema_check import audit, audit_model
from apps.performance.models import KPIWeight, MonthlyKPIScore

pytestmark = pytest.mark.django_db


@contextmanager
def altered_column(sql):
    """Apply one ALTER TABLE for the duration of the block; always undone (PostgreSQL DDL is
    transactional, so rolling back the savepoint restores the column type)."""
    savepoint = transaction.savepoint()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
        yield
    finally:
        transaction.savepoint_rollback(savepoint)


def test_every_performance_table_passes_the_audit():
    models = list(apps.get_app_config("performance").get_models())
    assert len(models) == 20  # 6 (Phase 7) + 14 (Phase 7.1 KRA configuration and results)
    reports = audit(models, connection)
    assert [r.problems for r in reports if not r.ok] == []


def test_wrong_numeric_precision_and_scale_is_drift():
    with altered_column(
        "ALTER TABLE performance_kpi_weight ALTER COLUMN weight TYPE numeric(6, 3)"
    ):
        problems = audit_model(KPIWeight, connection).problems
        assert any(p.startswith("type weight:") and "(6, 3)" in p for p in problems), problems
    assert audit_model(KPIWeight, connection).ok  # restored


def test_unconstrained_numeric_is_drift():
    with altered_column(
        "ALTER TABLE performance_monthly_kpi_score ALTER COLUMN score TYPE numeric"
    ):
        problems = audit_model(MonthlyKPIScore, connection).problems
        assert any(p.startswith("type score:") and "(None, None)" in p for p in problems), problems
    assert audit_model(MonthlyKPIScore, connection).ok  # restored
