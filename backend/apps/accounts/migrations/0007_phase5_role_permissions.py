"""Phase 5 responsibility / schedule / calendar authority. Safe to run more than once.

- Operations Manager: manage responsibilities of their own department (incl. ownership).
- HR: read-only view of all responsibilities, schedules and occurrences.
- Admin: manage all responsibilities, recurring schedules and the Company Calendar.
ROLE_PERMISSIONS is a frozen copy of apps/accounts/roles.py; a test checks the two agree.
Reversing restores migration 0006's grants.
"""

import importlib

from django.db import migrations

_phase2 = importlib.import_module("apps.accounts.migrations.0003_org_role_permissions")
_phase4 = importlib.import_module("apps.accounts.migrations.0006_phase4_task_role_permissions")

ROLE_PERMISSIONS = {
    "Employee": [*_phase4.ROLE_PERMISSIONS["Employee"]],
    "Operations Manager": [
        *_phase4.ROLE_PERMISSIONS["Operations Manager"],
        "recurring.manage_team_responsibilities",
    ],
    "HR": [*_phase4.ROLE_PERMISSIONS["HR"], "recurring.view_all_responsibilities"],
    "Admin": [
        *_phase4.ROLE_PERMISSIONS["Admin"],
        "recurring.view_all_responsibilities",
        "recurring.manage_all_responsibilities",
        "recurring.manage_schedules",
        "calendars.manage_company_calendar",
    ],
}


def grant_phase5_permissions(apps, schema_editor):
    _phase2._apply(apps, ROLE_PERMISSIONS)


def restore_phase4_permissions(apps, schema_editor):
    _phase2._apply(apps, _phase4.ROLE_PERMISSIONS)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0006_phase4_task_role_permissions"),
        ("recurring", "0001_initial"),
        ("calendars", "0001_initial"),
    ]

    operations = [migrations.RunPython(grant_phase5_permissions, restore_phase4_permissions)]
