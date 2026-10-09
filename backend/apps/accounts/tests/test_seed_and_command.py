import importlib

import pytest
from django.contrib.auth.models import Group
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.migrations.loader import MigrationLoader

from apps.accounts import roles
from apps.accounts.models import User
from apps.audit import actions
from apps.audit.models import AuditLog

pytestmark = pytest.mark.django_db


def _group_perms():
    return {
        g.name: sorted(f"{p.content_type.app_label}.{p.codename}" for p in g.permissions.all())
        for g in Group.objects.all()
    }


def test_phase1_seed_migration_is_unchanged():
    """Migration 0002 keeps its original Phase 1 grants; 0003 builds on them."""
    migration = importlib.import_module("apps.accounts.migrations.0002_seed_roles")
    assert migration.ROLE_PERMISSIONS == {
        "Employee": [],
        "Operations Manager": [],
        "HR": ["audit.view_audit_log"],
        "Admin": ["accounts.manage_users", "audit.view_audit_log"],
    }


def test_phase2_seed_migration_is_unchanged():
    """Migration 0003 keeps its Phase 2 grants; 0004 builds on them."""
    migration = importlib.import_module("apps.accounts.migrations.0003_org_role_permissions")
    assert migration.ROLE_PERMISSIONS["Employee"] == []
    assert migration.ROLE_PERMISSIONS["Operations Manager"] == ["org.view_team_employees"]
    assert "tasks.view_all_tasks" not in migration.ROLE_PERMISSIONS["HR"]


def test_phase3_seed_migration_is_unchanged():
    """Migration 0004 keeps its Phase 3 grants; 0005 only adds the SLA permission."""
    migration = importlib.import_module("apps.accounts.migrations.0004_task_role_permissions")
    assert "sla.manage_sla_rules" not in migration.ROLE_PERMISSIONS["Admin"]
    assert migration.ROLE_PERMISSIONS["Employee"] == ["tasks.create_task"]


def test_sla_seed_migration_is_unchanged():
    """Migration 0005 keeps its grants; 0006 adds the Phase 4 task authority."""
    migration = importlib.import_module("apps.accounts.migrations.0005_sla_role_permissions")
    assert migration.ROLE_PERMISSIONS["Employee"] == ["tasks.create_task"]
    assert "tasks.delete_task" not in migration.ROLE_PERMISSIONS["Admin"]


def test_phase4_seed_migration_is_unchanged():
    """Migration 0006 keeps its Phase 4 grants; 0007 adds the Phase 5 authority."""
    migration = importlib.import_module(
        "apps.accounts.migrations.0006_phase4_task_role_permissions"
    )
    assert "recurring.manage_all_responsibilities" not in migration.ROLE_PERMISSIONS["Admin"]
    assert "recurring.view_all_responsibilities" not in migration.ROLE_PERMISSIONS["HR"]


def test_phase5_seed_migration_is_unchanged():
    """Migration 0007 keeps its Phase 5 grants; 0008 adds the Phase 9 overdue authority."""
    migration = importlib.import_module("apps.accounts.migrations.0007_phase5_role_permissions")
    assert "overdue.review_all_cases" not in migration.ROLE_PERMISSIONS["Admin"]
    assert "overdue.review_team_cases" not in migration.ROLE_PERMISSIONS["Operations Manager"]


def test_phase9_seed_migration_is_unchanged():
    """Migration 0008 keeps its Phase 9 grants; 0009 adds HR's responsibility management."""
    migration = importlib.import_module("apps.accounts.migrations.0008_phase9_overdue_permissions")
    assert "recurring.manage_all_responsibilities" not in migration.ROLE_PERMISSIONS["HR"]


def test_phase_a_seed_migration_is_unchanged():
    """Migration 0009 keeps its Phase A grants; 0010 adds the Phase 7.1 performance authority."""
    migration = importlib.import_module(
        "apps.accounts.migrations.0009_phase_a_responsibility_permissions"
    )
    for role in ("HR", "Admin"):
        assert not [p for p in migration.ROLE_PERMISSIONS[role] if p.startswith("performance.")]


def test_phase7_performance_grants():
    """Approved P11 / L: HR prepares and reviews, Admin approves; the Operations Manager and
    the Employee hold no performance permission."""
    grants = {name: set(perms) for name, perms in roles.ROLE_PERMISSIONS.items()}
    performance = {name: {p for p in perms if p.startswith("performance.")}
                   for name, perms in grants.items()}
    assert performance == {
        "Employee": set(),
        "Operations Manager": set(),
        "HR": {"performance.configure_kpis", "performance.manage_performance",
               "performance.finalize_performance", "performance.reopen_performance"},
        "Admin": {"performance.approve_kpi_config", "performance.reopen_performance"},
    }


def test_r3_seed_matches_roles_module_and_is_idempotent():
    expected = {name: sorted(perms) for name, perms in roles.ROLE_PERMISSIONS.items()}
    assert _group_perms() == expected

    # The LATEST role-seed migration holds the frozen copy of the current grants.
    migration = importlib.import_module(
        "apps.accounts.migrations.0010_phase7_performance_permissions"
    )
    assert {k: sorted(v) for k, v in migration.ROLE_PERMISSIONS.items()} == expected
    # Run the seed again against the migration-state app registry (never the live registry).
    state = MigrationLoader(connection).project_state(
        ("accounts", "0010_phase7_performance_permissions")
    )
    migration.grant_phase7_permissions(state.apps, None)
    assert _group_perms() == expected
    assert Group.objects.count() == 4


def test_create_initial_admin(monkeypatch):
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "Initial-Admin-Pass-2026")
    call_command("create_initial_admin", "--email", "Owner@Example.com", "--no-input")
    user = User.objects.get(email="owner@example.com")
    assert user.role_names == ["Admin"] and not user.is_superuser
    row = AuditLog.objects.get(action=actions.SYSTEM_INITIAL_ADMIN_CREATED)
    assert row.actor_user is None and row.new_value["roles"] == ["Admin"]

    with pytest.raises(CommandError):
        call_command("create_initial_admin", "--email", "owner@example.com", "--no-input")


def test_create_initial_admin_needs_password_without_prompt(monkeypatch):
    monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
    with pytest.raises(CommandError):
        call_command("create_initial_admin", "--email", "x@example.com", "--no-input")


def test_create_superuser_is_disabled():
    with pytest.raises(NotImplementedError):
        User.objects.create_superuser("x@example.com", "whatever")
