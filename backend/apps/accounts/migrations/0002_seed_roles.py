"""Seeds the four roles and their Phase 1 permissions. Safe to run more than once.

ROLE_PERMISSIONS is a frozen copy of apps/accounts/roles.py at the time of this migration;
a test checks the two agree. Future permission changes need a new migration.
"""

from django.db import migrations

ROLE_PERMISSIONS = {
    "Employee": [],
    "Operations Manager": [],
    "HR": ["audit.view_audit_log"],
    "Admin": ["accounts.manage_users", "audit.view_audit_log"],
}


def seed_roles(apps, schema_editor):
    # Permissions are normally created after all migrations (post_migrate); create them now.
    from django.contrib.auth.management import create_permissions

    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    for role, codes in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=role)
        perms = []
        for code in codes:
            app_label, codename = code.split(".")
            perms.append(
                Permission.objects.get(content_type__app_label=app_label, codename=codename)
            )
        group.permissions.set(perms)


def unseed_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=ROLE_PERMISSIONS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("audit", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(seed_roles, unseed_roles)]
