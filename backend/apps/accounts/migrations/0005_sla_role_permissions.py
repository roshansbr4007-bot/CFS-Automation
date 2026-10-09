"""Grants sla.manage_sla_rules to Admin (Phase 6 SLA foundation). Safe to run more than once.

ROLE_PERMISSIONS is a frozen copy of apps/accounts/roles.py at the time of this migration; a test
checks the two agree. Every earlier grant is unchanged. Reversing restores migration 0004's grants.
"""

import importlib

from django.db import migrations

_phase3 = importlib.import_module("apps.accounts.migrations.0004_task_role_permissions")
_phase2 = importlib.import_module("apps.accounts.migrations.0003_org_role_permissions")

ROLE_PERMISSIONS = {
    **_phase3.ROLE_PERMISSIONS,
    "Admin": [*_phase3.ROLE_PERMISSIONS["Admin"], "sla.manage_sla_rules"],
}


def grant_sla_permissions(apps, schema_editor):
    _phase2._apply(apps, ROLE_PERMISSIONS)


def restore_phase3_permissions(apps, schema_editor):
    _phase2._apply(apps, _phase3.ROLE_PERMISSIONS)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_task_role_permissions"),
        ("sla", "0001_initial"),
    ]

    operations = [migrations.RunPython(grant_sla_permissions, restore_phase3_permissions)]
