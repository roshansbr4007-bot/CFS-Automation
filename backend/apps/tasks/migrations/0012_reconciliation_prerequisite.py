"""Approved D1: Reconciliation waits for Brokerage Calculation to be COMPLETED.

Configuration only. Safe to run more than once: an existing prerequisite (for example one an
Admin changed later) is never overwritten. No task, SLA clock or dependency link is created or
changed, so historical tasks are untouched and nothing is backfilled; only tasks created after
this migration are linked by the engine.
"""

from django.db import migrations

DEPENDENT = "RECONCILIATION"
PREREQUISITE = "BROKERAGE_CALCULATION"
STATE = "COMPLETED"


def configure(apps, schema_editor):
    TaskTemplate = apps.get_model("tasks", "TaskTemplate")
    prerequisite = TaskTemplate.objects.filter(code=PREREQUISITE).first()
    if prerequisite is None:
        return
    TaskTemplate.objects.filter(code=DEPENDENT, prerequisite_template__isnull=True).update(
        prerequisite_template=prerequisite, prerequisite_state=STATE
    )


def unconfigure(apps, schema_editor):
    TaskTemplate = apps.get_model("tasks", "TaskTemplate")
    TaskTemplate.objects.filter(
        code=DEPENDENT, prerequisite_template__code=PREREQUISITE, prerequisite_state=STATE
    ).update(prerequisite_template=None, prerequisite_state="")


class Migration(migrations.Migration):
    dependencies = [("tasks", "0011_task_dependency")]

    operations = [migrations.RunPython(configure, unconfigure)]
