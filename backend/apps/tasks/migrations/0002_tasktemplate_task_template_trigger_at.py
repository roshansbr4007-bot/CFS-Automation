import django.db.models.deletion
from django.db import migrations, models

TRIGGERS = [
    ("ASSIGNMENT", "Assignment"),
    ("LOGIN", "Employee login"),
    ("FIXED_TIME", "Fixed time"),
    ("EVENT", "Event"),
    ("DEPENDENCY", "Dependency completion"),
]


class Migration(migrations.Migration):
    dependencies = [
        ("org", "0002_seed_departments"),
        ("tasks", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="TaskTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=40, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("resolution_rule_code", models.CharField(blank=True, max_length=40)),
                ("trigger", models.CharField(choices=TRIGGERS, default="ASSIGNMENT", max_length=12)),
                ("fixed_time", models.TimeField(blank=True, null=True)),
                ("acknowledgment_required", models.BooleanField(default=False)),
                ("verification_required", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "department",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="task_templates",
                        to="org.department",
                    ),
                ),
            ],
            options={
                "db_table": "tasks_template",
                "ordering": ["name", "id"],
                "default_permissions": (),
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("trigger", "FIXED_TIME"), _negated=True)
                        | models.Q(("fixed_time__isnull", False)),
                        name="tasks_template_fixed_time_chk",
                    )
                ],
            },
        ),
        migrations.AddField(
            model_name="task",
            name="template",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="tasks",
                to="tasks.tasktemplate",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="trigger_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
