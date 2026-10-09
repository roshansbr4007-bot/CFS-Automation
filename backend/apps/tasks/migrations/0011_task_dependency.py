"""Task Dependency Engine (schema only): the prerequisite configuration on task types and the
TaskDependency link table. No data is written and no existing row changes."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0010_phase5_template_rules"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="tasktemplate",
            name="prerequisite_template",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="dependent_templates",
                to="tasks.tasktemplate",
            ),
        ),
        migrations.AddField(
            model_name="tasktemplate",
            name="prerequisite_state",
            field=models.CharField(
                blank=True,
                choices=[("COMPLETED", "Completed"), ("VERIFIED", "Verified")],
                default="",
                max_length=10,
            ),
        ),
        migrations.AddConstraint(
            model_name="tasktemplate",
            constraint=models.CheckConstraint(
                condition=models.Q(prerequisite_template__isnull=True, prerequisite_state="")
                | models.Q(
                    prerequisite_template__isnull=False,
                    prerequisite_state__in=["COMPLETED", "VERIFIED"],
                ),
                name="tasks_template_prerequisite_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="tasktemplate",
            constraint=models.CheckConstraint(
                condition=~models.Q(prerequisite_template=models.F("id")),
                name="tasks_template_not_own_prerequisite_chk",
            ),
        ),
        migrations.CreateModel(
            name="TaskDependency",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "required_state",
                    models.CharField(
                        choices=[("COMPLETED", "Completed"), ("VERIFIED", "Verified")],
                        max_length=10,
                    ),
                ),
                ("satisfied_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "dependent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="prerequisite_links",
                        to="tasks.task",
                    ),
                ),
                (
                    "prerequisite",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="dependent_links",
                        to="tasks.task",
                    ),
                ),
            ],
            options={
                "db_table": "tasks_dependency",
                "ordering": ["created_at", "id"],
                "default_permissions": (),
                "indexes": [
                    models.Index(
                        fields=["prerequisite", "satisfied_at"], name="tasks_dep_prereq_sat_idx"
                    ),
                    models.Index(
                        fields=["dependent", "satisfied_at"], name="tasks_dep_dependent_sat_idx"
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("prerequisite", "dependent"), name="tasks_dependency_pair_uniq"
                    ),
                    models.CheckConstraint(
                        condition=~models.Q(prerequisite=models.F("dependent")),
                        name="tasks_dependency_not_self_chk",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(required_state__in=["COMPLETED", "VERIFIED"]),
                        name="tasks_dependency_state_chk",
                    ),
                ],
            },
        ),
    ]
