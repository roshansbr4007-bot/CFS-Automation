"""Phase A responsibility management (approved D1). Safe to run more than once.

- HR: manage responsibilities, owners and schedules organisation-wide
  (recurring.manage_all_responsibilities; schedule authority follows responsibility authority).
Operations Manager (own department), Admin and Employee grants are unchanged.
ROLE_PERMISSIONS is a frozen copy of apps/accounts/roles.py; a test checks the two agree.
Reversing restores migration 0008's grants.
"""

import importlib

from django.db import migrations

_phase2 = importlib.import_module("apps.accounts.migrations.0003_org_role_permissions")
_phase9 = importlib.import_module("apps.accounts.migrations.0008_phase9_overdue_permissions")

ROLE_PERMISSIONS = {
    "Employee": [*_phase9.ROLE_PERMISSIONS["Employee"]],
    "Operations Manager": [*_phase9.ROLE_PERMISSIONS["Operations Manager"]],
    "HR": [*_phase9.ROLE_PERMISSIONS["HR"], "recurring.manage_all_responsibilities"],
    "Admin": [*_phase9.ROLE_PERMISSIONS["Admin"]],
}


def grant_phase_a_permissions(apps, schema_editor):
    _phase2._apply(apps, ROLE_PERMISSIONS)


def restore_phase9_permissions(apps, schema_editor):
    _phase2._apply(apps, _phase9.ROLE_PERMISSIONS)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0008_phase9_overdue_permissions")]

    operations = [migrations.RunPython(grant_phase_a_permissions, restore_phase9_permissions)]
