import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0008_seed_task_categories"),
        ("recurring", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="source",
            field=models.CharField(
                choices=[
                    ("MANUAL", "Manually created / assigned"),
                    ("SCHEDULED", "Generated from a responsibility schedule"),
                ],
                default="MANUAL",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="responsibility",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="tasks",
                to="recurring.responsibility",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="schedule",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="tasks",
                to="recurring.recurringschedule",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="occurrence_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="task",
            name="generated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="task",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("source", "MANUAL"))
                    & models.Q(("schedule__isnull", True))
                    & models.Q(("occurrence_date__isnull", True))
                )
                | (
                    models.Q(("source", "SCHEDULED"))
                    & models.Q(("responsibility__isnull", False))
                    & models.Q(("schedule__isnull", False))
                    & models.Q(("occurrence_date__isnull", False))
                    & models.Q(("generated_at__isnull", False))
                ),
                name="tasks_source_fields_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="task",
            constraint=models.UniqueConstraint(
                condition=models.Q(("schedule__isnull", False)),
                fields=("schedule", "occurrence_date"),
                name="tasks_one_task_per_occurrence",
            ),
        ),
    ]
