import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tasks", "0006_repair_task_history_schema")]

    operations = [
        migrations.CreateModel(
            name="TaskCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=40, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "tasks_category",
                "ordering": ["name", "id"],
                "default_permissions": (),
                "permissions": [("manage_task_categories", "Create and edit task categories")],
            },
        ),
        migrations.AddField(
            model_name="task",
            name="category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="tasks",
                to="tasks.taskcategory",
            ),
        ),
        migrations.AlterModelOptions(
            name="task",
            options={
                "ordering": ["-created_at", "-id"],
                "default_permissions": (),
                "permissions": [
                    ("create_task", "Create tasks (for oneself unless also allowed to assign)"),
                    ("assign", "Assign tasks to other employees"),
                    ("view_all_tasks", "See every task"),
                    ("view_team_tasks", "See tasks of one's own department"),
                    ("manage_team_tasks", "Assign, edit, cancel and verify tasks of own department"),
                    ("manage_all_tasks", "Assign, edit, cancel and verify any task"),
                    ("edit_all_tasks", "Edit and reassign any task"),
                    ("delete_task", "Physically delete any task"),
                ],
            },
        ),
    ]
