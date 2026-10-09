"""Grants the Phase 2 org permissions to the roles (approved Q7/Q8). Safe to run more than once.

ROLE_PERMISSIONS is a frozen copy of apps/accounts/roles.py at the time of this migration; a test
checks the two agree. Reversing restores the Phase 1 grants from migration 0002.
"""

import importlib

from django.db import migrations

ROLE_PERMISSIONS = {
    "Employee": [],
    "Operations Manager": ["org.view_team_employees"],
    "HR": ["audit.view_audit_log", "org.view_all_employees", "org.manage_employees"],
    "Admin": [
        "accounts.manage_users",
        "audit.view_audit_log",
        "org.view_all_employees",
        "org.manage_employees",
        "org.link_employee_login",
        "org.manage_departments",
    ],
}


def _apply(apps, grants):
    # Permissions are normally created after all migrations (post_migrate); create them now.
    from django.contrib.auth.management import create_permissions

    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    for role, codes in grants.items():
        group, _ = Group.objects.get_or_create(name=role)
        perms = []
        for code in codes:
            app_label, codename = code.split(".")
            perms.append(
                Permission.objects.get(content_type__app_label=app_label, codename=codename)
            )
        group.permissions.set(perms)


def grant_org_permissions(apps, schema_editor):
    _apply(apps, ROLE_PERMISSIONS)


def restore_phase1_permissions(apps, schema_editor):
    phase1 = importlib.import_module("apps.accounts.migrations.0002_seed_roles")
    _apply(apps, phase1.ROLE_PERMISSIONS)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_seed_roles"),
        ("org", "0001_initial"),
    ]

    operations = [migrations.RunPython(grant_org_permissions, restore_phase1_permissions)]
