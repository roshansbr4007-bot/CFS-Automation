from django.db import migrations


def repair_task_schema(apps, schema_editor):
    Task = apps.get_model("tasks", "Task")
    connection = schema_editor.connection

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'tasks_task'
        """)
        existing = {row[0] for row in cursor.fetchall()}

    # Legacy column -> current model name.
    if "cancellation_reason" in existing and "cancelled_reason" not in existing:
        with connection.cursor() as cursor:
            cursor.execute("""
                ALTER TABLE tasks_task
                RENAME COLUMN cancellation_reason TO cancelled_reason
            """)
        existing.remove("cancellation_reason")
        existing.add("cancelled_reason")

    # Fields missing from the actual database.
    missing = [
        "task_type",
        "department",
        "assigned_by",
        "assigned_at",
        "completion_recorded_at",
        "completion_source",
        "verification_required",
        "blocked_at",
        "cancelled_at",
        "version",
    ]

    for field_name in missing:
        field = Task._meta.get_field(field_name)

    # Django ForeignKey field "department" is stored in DB
    # as "department_id". Always compare the real DB column name.
        if field.column in existing:
            continue

        schema_editor.add_field(Task, field)

    # Existing columns whose null/type definition differs from
    # the current Django model.
    for field_name in ("assigned_to", "received_at_source", "rework_count"):
        field = Task._meta.get_field(field_name)
        schema_editor.alter_field(
            Task,
            field,
            field,
            strict=False,
        )

    # Remove the obsolete legacy column.
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'tasks_task'
              AND column_name = 'category'
        """)
        category_exists = cursor.fetchone() is not None

    if category_exists:
        with connection.cursor() as cursor:
            cursor.execute("""
                ALTER TABLE tasks_task
                DROP COLUMN category
            """)


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0003_seed_task_templates"),
    ]

    operations = [
        migrations.RunPython(
            repair_task_schema,
            migrations.RunPython.noop,
        ),
    ]