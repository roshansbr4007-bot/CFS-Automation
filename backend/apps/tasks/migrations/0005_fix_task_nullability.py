from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0004_repair_task_schema"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                """
                ALTER TABLE tasks_task
                ALTER COLUMN assigned_to_id SET NOT NULL
                """,
                """
                ALTER TABLE tasks_task
                ALTER COLUMN received_at_source DROP NOT NULL
                """,
            ],
            reverse_sql=[
                """
                ALTER TABLE tasks_task
                ALTER COLUMN assigned_to_id DROP NOT NULL
                """,
                """
                ALTER TABLE tasks_task
                ALTER COLUMN received_at_source SET NOT NULL
                """,
            ],
        ),
    ]