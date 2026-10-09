"""Phase 9 overdue-case authority (approved Q4). Safe to run more than once.

- Operations Manager: review overdue cases of tasks in their own department.
- HR, Admin: view and review every overdue case.
Employees answer their own cases without a permission.
ROLE_PERMISSIONS is a frozen copy of apps/accounts/roles.py; a test checks the two agree.
Reversing restores migration 0007's grants.
"""

import importlib

from django.db import migrations

_phase2 = importlib.import_module("apps.accounts.migrations.0003_org_role_permissions")
_phase5 = importlib.import_module("apps.accounts.migrations.0007_phase5_role_permissions")

ROLE_PERMISSIONS = {
    "Employee": [*_phase5.ROLE_PERMISSIONS["Employee"]],
    "Operations Manager": [
        *_phase5.ROLE_PERMISSIONS["Operations Manager"],
        "overdue.review_team_cases",
    ],
    "HR": [
        *_phase5.ROLE_PERMISSIONS["HR"],
        "overdue.view_all_cases",
        "overdue.review_all_cases",
    ],
    "Admin": [
        *_phase5.ROLE_PERMISSIONS["Admin"],
        "overdue.view_all_cases",
        "overdue.review_all_cases",
    ],
}


def grant_phase9_permissions(apps, schema_editor):
    _phase2._apply(apps, ROLE_PERMISSIONS)


def restore_phase5_permissions(apps, schema_editor):
    _phase2._apply(apps, _phase5.ROLE_PERMISSIONS)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0007_phase5_role_permissions"),
        ("overdue", "0001_initial"),
    ]

    operations = [migrations.RunPython(grant_phase9_permissions, restore_phase5_permissions)]
