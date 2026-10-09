"""Seeds the approved initial task categories (Phase 4). Admin-managed data, not code.

Safe to run more than once; existing rows are never overwritten.
"""

from django.db import migrations

CATEGORIES = [
    ("SALES", "Sales"),
    ("MARKETING", "Marketing"),
    ("OPERATIONS", "Operations"),
    ("COMPLIANCE", "Compliance"),
    ("FINANCE", "Finance"),
    ("HR", "HR"),
    ("CLIENT_SERVICING", "Client Servicing"),
    ("COORDINATION", "Coordination"),
]


def seed(apps, schema_editor):
    TaskCategory = apps.get_model("tasks", "TaskCategory")
    for code, name in CATEGORIES:
        TaskCategory.objects.get_or_create(code=code, defaults={"name": name})


def unseed(apps, schema_editor):
    TaskCategory = apps.get_model("tasks", "TaskCategory")
    TaskCategory.objects.filter(code__in=[c for c, _ in CATEGORIES], tasks__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("tasks", "0007_taskcategory_task_category_phase4_permissions")]

    operations = [migrations.RunPython(seed, unseed)]
