"""Phase 7.1 performance authority (approved P11 / L). Safe to run more than once.

- HR: prepare KPI configuration (drafts, plan defaults, employee overrides), and the monthly
  review permissions used from Phase 7.2/7.3 (manage, finalize, reopen).
- Admin: approve (activate / retire) KPI configuration, and reopen.
- Operations Manager and Employee: no performance management permission.
ROLE_PERMISSIONS is a frozen copy of apps/accounts/roles.py; a test checks the two agree.
Reversing restores migration 0009's grants.
"""

import importlib

from django.db import migrations

_phase2 = importlib.import_module("apps.accounts.migrations.0003_org_role_permissions")
_phase_a = importlib.import_module("apps.accounts.migrations.0009_phase_a_responsibility_permissions")

ROLE_PERMISSIONS = {
    "Employee": [*_phase_a.ROLE_PERMISSIONS["Employee"]],
    "Operations Manager": [*_phase_a.ROLE_PERMISSIONS["Operations Manager"]],
    "HR": [
        *_phase_a.ROLE_PERMISSIONS["HR"],
        "performance.configure_kpis",
        "performance.manage_performance",
        "performance.finalize_performance",
        "performance.reopen_performance",
    ],
    "Admin": [
        *_phase_a.ROLE_PERMISSIONS["Admin"],
        "performance.approve_kpi_config",
        "performance.reopen_performance",
    ],
}


def grant_phase7_permissions(apps, schema_editor):
    _phase2._apply(apps, ROLE_PERMISSIONS)


def restore_phase_a_permissions(apps, schema_editor):
    _phase2._apply(apps, _phase_a.ROLE_PERMISSIONS)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0009_phase_a_responsibility_permissions"),
        ("performance", "0003_kra_configuration_schema"),
    ]

    operations = [migrations.RunPython(grant_phase7_permissions, restore_phase_a_permissions)]
