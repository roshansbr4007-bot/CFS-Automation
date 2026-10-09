"""Phase 7.2 KRA calculation engine fields (additive; no data is changed).

- MonthlyTaskCredit: task_id becomes optional; occurrence_id records an expected-but-not-
  generated occurrence (generation gap); match_reason says why the row belongs to the component.
  Exactly one of task_id / occurrence_id is set (existing rows all have a task_id).
- MonthlyPerformance: cutoff_at (the instant the month was judged at) and plan_source.
"""

from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("performance", "0004_kra_configuration_seed")]

    operations = [
        migrations.AlterField(
            model_name="monthlytaskcredit",
            name="task_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="monthlytaskcredit",
            name="occurrence_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="monthlytaskcredit",
            name="match_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("SCHEDULED", "Generated from the responsibility's schedule"),
                    ("TASK_RESPONSIBILITY", "The task carries the responsibility"),
                    ("TASK_TYPE", "Same task type"),
                    ("CATEGORY", "Same category and department"),
                    ("OVERRIDE", "HR exception (include)"),
                    ("GAP", "Expected occurrence that was not generated"),
                ],
                default="",
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="monthlytaskcredit",
            constraint=models.UniqueConstraint(
                condition=Q(occurrence_id__isnull=False),
                fields=("component_result", "occurrence_id"),
                name="performance_gap_credit_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="monthlytaskcredit",
            constraint=models.CheckConstraint(
                condition=(Q(task_id__isnull=False) & Q(occurrence_id__isnull=True))
                | (Q(task_id__isnull=True) & Q(occurrence_id__isnull=False)),
                name="performance_task_credit_source_chk",
            ),
        ),
        migrations.AddField(
            model_name="monthlyperformance",
            name="cutoff_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="monthlyperformance",
            name="plan_source",
            field=models.CharField(
                blank=True,
                choices=[
                    ("OVERRIDE", "HR override"),
                    ("DEFAULT", "Department + system role default"),
                ],
                default="",
                max_length=10,
            ),
        ),
    ]
