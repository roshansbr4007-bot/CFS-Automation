"""Grants the Phase 3 task permissions to the roles. Safe to run more than once.

ROLE_PERMISSIONS is a frozen copy of apps/accounts/roles.py at the time of this migration; a test
checks the two agree. tasks.assign is created but granted to NO role (approved Phase 3 rule).
Reversing restores the Phase 2 grants from migration 0003.
"""

import importlib

from django.db import migrations

ROLE_PERMISSIONS = {
    "Employee": ["tasks.create_task"],
    "Operations Manager": [
        "org.view_team_employees",
        "tasks.create_task",
        "tasks.view_team_tasks",
        "tasks.manage_team_tasks",
    ],
    "HR": [
        "audit.view_audit_log",
        "org.view_all_employees",
        "org.manage_employees",
        "tasks.view_all_tasks",
    ],
    "Admin": [
        "accounts.manage_users",
        "audit.view_audit_log",
        "org.view_all_employees",
        "org.manage_employees",
        "org.link_employee_login",
        "org.manage_departments",
        "tasks.create_task",
        "tasks.view_all_tasks",
        "tasks.manage_all_tasks",
    ],
}

_phase2 = importlib.import_module("apps.accounts.migrations.0003_org_role_permissions")


def grant_task_permissions(apps, schema_editor):
    _phase2._apply(apps, ROLE_PERMISSIONS)


def restore_phase2_permissions(apps, schema_editor):
    _phase2._apply(apps, _phase2.ROLE_PERMISSIONS)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_org_role_permissions"),
        ("tasks", "0001_initial"),
    ]

    operations = [migrations.RunPython(grant_task_permissions, restore_phase2_permissions)]
