"""Seeds the five approved department codes (Q1). Safe to run more than once.

OPS is the only live department at launch. Admin can rename departments, change is_live and
add departments on screen; codes never change.
"""

from django.db import migrations

DEPARTMENTS = [
    ("OPS", "Operations", True),
    ("RM", "Relationship Management", False),
    ("INS", "Insurance", False),
    ("LOAN", "Loans", False),
    ("HR", "Human Resources", False),
]


def seed_departments(apps, schema_editor):
    Department = apps.get_model("org", "Department")
    for code, name, is_live in DEPARTMENTS:
        Department.objects.get_or_create(code=code, defaults={"name": name, "is_live": is_live})


def unseed_departments(apps, schema_editor):
    Department = apps.get_model("org", "Department")
    Department.objects.filter(
        code__in=[code for code, _, _ in DEPARTMENTS], employees__isnull=True
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("org", "0001_initial")]

    operations = [migrations.RunPython(seed_departments, unseed_departments)]
