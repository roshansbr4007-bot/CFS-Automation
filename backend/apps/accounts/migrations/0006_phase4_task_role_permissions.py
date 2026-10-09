"""Phase 4 task authority (approved decisions). Safe to run more than once.

- Every task-creating role gets tasks.assign: assignment across users and departments.
- HR: create, edit and reassign any task (tasks.edit_all_tasks) and physically delete tasks.
  HR does NOT get cancel / block / verification (those stay with manage_*_tasks).
- Admin: physical delete and task-category management.
ROLE_PERMISSIONS is a frozen copy of apps/accounts/roles.py; a test checks the two agree.
Reversing restores migration 0005's grants.
"""

import importlib

from django.db import migrations

_phase2 = importlib.import_module("apps.accounts.migrations.0003_org_role_permissions")
_sla = importlib.import_module("apps.accounts.migrations.0005_sla_role_permissions")

ROLE_PERMISSIONS = {
    "Employee": ["tasks.create_task", "tasks.assign"],
    "Operations Manager": [
        "org.view_team_employees",
        "tasks.create_task",
        "tasks.view_team_tasks",
        "tasks.manage_team_tasks",
        "tasks.assign",
    ],
    "HR": [
        "audit.view_audit_log",
        "org.view_all_employees",
        "org.manage_employees",
        "tasks.view_all_tasks",
        "tasks.create_task",
        "tasks.assign",
        "tasks.edit_all_tasks",
        "tasks.delete_task",
    ],
    "Admin": [
        *_sla.ROLE_PERMISSIONS["Admin"],
        "tasks.delete_task",
        "tasks.manage_task_categories",
    ],
}


def grant_phase4_permissions(apps, schema_editor):
    _phase2._apply(apps, ROLE_PERMISSIONS)


def restore_phase3_permissions(apps, schema_editor):
    _phase2._apply(apps, _sla.ROLE_PERMISSIONS)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_sla_role_permissions"),
        ("tasks", "0007_taskcategory_task_category_phase4_permissions"),
    ]

    operations = [migrations.RunPython(grant_phase4_permissions, restore_phase3_permissions)]
