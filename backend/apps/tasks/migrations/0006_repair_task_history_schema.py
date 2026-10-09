"""Repair development databases whose task tables came from an older Phase 3 attempt.

Why: that attempt also shipped a migration named tasks.0001_initial, so Django recorded 0001 as
applied while the tables kept the old shape (verification: reviewer/reason, no submitted_at; no
comment/attachment tables; narrower or wider column types). 0004/0005 repaired tasks_task; this
migration repairs every remaining task table the same way.

How: it inspects the LIVE schema and only changes what differs from the models:
  * renames the known legacy verification columns (reviewer_id -> decided_by_id,
    reason -> rejection_reason) when the new column does not exist yet;
  * creates missing tables; adds missing columns (a NOT NULL column added to a table that already
    has rows is backfilled from a documented source, else the migration stops with a message);
  * fixes nullability, safe integer widening and varchar lengths (only when the data fits);
  * adds missing foreign keys, unique constraints, check constraints and indexes;
  * never drops a column or a row: a leftover legacy column that is NOT NULL is made NULL so it
    cannot block inserts, and is otherwise left in place (`schema_audit` reports it).
On a database that already matches (tests, CI, fresh installs) it changes nothing. It does not
change Django's migration state, so `makemigrations --check` stays clean. PostgreSQL DDL is
transactional: if any step fails, the whole repair is rolled back.
"""

import re

from django.db import migrations

TASK_MODELS = [
    "TaskTemplate",
    "Task",
    "TaskAssignment",
    "TaskVerification",
    "TaskComment",
    "TaskAttachment",
]
LEGACY_RENAMES = {
    "tasks_verification": [("reviewer_id", "decided_by_id"), ("reason", "rejection_reason")],
}
# NOT NULL columns that may have to be added to tables that already contain rows.
BACKFILL = {("tasks_verification", "submitted_at"): '"decided_at"'}
INTEGERS = {"smallint": 1, "integer": 2, "bigint": 3}
SIMPLE = {"timestamp with time zone": "timestamp with time zone", "time": "time without time zone"}


def _columns(cursor, table):
    cursor.execute(
        """
        SELECT column_name, is_nullable, data_type, character_maximum_length
        FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = %s
        """,
        [table],
    )
    return {row[0]: {"nullable": row[1] == "YES", "type": row[2], "length": row[3]}
            for row in cursor.fetchall()}


def _scalar(cursor, sql):
    cursor.execute(sql)
    return cursor.fetchone()[0]


def _wanted_type(field, connection):
    db_type = field.db_type(connection)
    if db_type is None:
        return None
    match = re.fullmatch(r"varchar\((\d+)\)", db_type)
    if match:
        return "character varying", int(match.group(1))
    return SIMPLE.get(db_type, db_type), None


def _fix_type(schema_editor, cursor, table, column, actual, wanted):
    q = schema_editor.quote_name
    if wanted[0] in INTEGERS and actual["type"] in INTEGERS:
        if INTEGERS[wanted[0]] < INTEGERS[actual["type"]]:
            raise RuntimeError(f"{table}.{column}: refusing to narrow {actual['type']}.")
        schema_editor.execute(
            f"ALTER TABLE {q(table)} ALTER COLUMN {q(column)} TYPE {wanted[0]}"
        )
        return
    if wanted[0] == "character varying" and actual["type"] in ("character varying", "text"):
        longest = _scalar(cursor, f"SELECT COALESCE(MAX(LENGTH({q(column)})), 0) FROM {q(table)}")
        if longest > wanted[1]:
            raise RuntimeError(f"{table}.{column}: data is longer than {wanted[1]} characters.")
        schema_editor.execute(
            f"ALTER TABLE {q(table)} ALTER COLUMN {q(column)} TYPE varchar({wanted[1]})"
        )
        return
    raise RuntimeError(
        f"{table}.{column}: type {actual['type']} cannot be converted safely to {wanted[0]}."
    )


def _add_column(schema_editor, cursor, model, field, has_rows):
    table, q = model._meta.db_table, schema_editor.quote_name
    if field.null or field.has_default() or not has_rows:
        schema_editor.add_field(model, field)
        return
    source = BACKFILL.get((table, field.column))
    if source is None:
        raise RuntimeError(
            f"{table}.{field.column} is NOT NULL, the table has rows and no backfill is defined."
        )
    temporary = field.clone()
    temporary.null = True
    temporary.set_attributes_from_name(field.name)
    temporary.model = model
    schema_editor.add_field(model, temporary)
    schema_editor.execute(
        f"UPDATE {q(table)} SET {q(field.column)} = {source} WHERE {q(field.column)} IS NULL"
    )
    schema_editor.execute(f"ALTER TABLE {q(table)} ALTER COLUMN {q(field.column)} SET NOT NULL")


def _repair_table(schema_editor, model):
    connection, q = schema_editor.connection, schema_editor.quote_name
    meta = model._meta
    table = meta.db_table
    fields = meta.local_fields
    with connection.cursor() as cursor:
        columns = _columns(cursor, table)
        has_rows = _scalar(cursor, f"SELECT EXISTS (SELECT 1 FROM {q(table)})")
        added = set()
        for field in fields:
            if field.column not in columns:
                _add_column(schema_editor, cursor, model, field, has_rows)
                added.add(field.column)
        columns = _columns(cursor, table)
        constraints = connection.introspection.get_constraints(cursor, table)
        for field in fields:
            actual = columns[field.column]
            wanted = _wanted_type(field, connection)
            if wanted and (actual["type"], actual["length"]) != wanted:
                _fix_type(schema_editor, cursor, table, field.column, actual, wanted)
            if field.null and not actual["nullable"]:
                schema_editor.execute(
                    f"ALTER TABLE {q(table)} ALTER COLUMN {q(field.column)} DROP NOT NULL"
                )
            elif not field.null and actual["nullable"]:
                nulls = _scalar(
                    cursor, f"SELECT COUNT(*) FROM {q(table)} WHERE {q(field.column)} IS NULL"
                )
                if nulls:
                    raise RuntimeError(f"{table}.{field.column} has {nulls} NULL row(s).")
                schema_editor.execute(
                    f"ALTER TABLE {q(table)} ALTER COLUMN {q(field.column)} SET NOT NULL"
                )
            if field.column in added:
                continue  # add_field already queued its FK / unique constraint (deferred SQL)
            if field.remote_field and field.db_constraint:
                target = field.remote_field.model._meta.db_table
                if not any(
                    c["foreign_key"] and c["columns"] == [field.column]
                    and c["foreign_key"][0] == target
                    for c in constraints.values()
                ):
                    schema_editor.execute(
                        schema_editor._create_fk_sql(model, field, "_fk_%(to_table)s_%(to_column)s")
                    )
            if field.unique and not field.primary_key and not any(
                c["unique"] and c["columns"] == [field.column] for c in constraints.values()
            ):
                schema_editor.execute(schema_editor._create_unique_sql(model, [field]))
        for legacy, info in columns.items():
            if legacy not in {f.column for f in fields} and not info["nullable"]:
                schema_editor.execute(f"ALTER TABLE {q(table)} ALTER COLUMN {q(legacy)} DROP NOT NULL")
        for item in meta.constraints:
            if item.name not in constraints:
                schema_editor.add_constraint(model, item)
        for item in meta.indexes:
            if item.name not in constraints:
                schema_editor.add_index(model, item)


def repair(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
        for table, renames in LEGACY_RENAMES.items():
            if table not in tables:
                continue
            columns = _columns(cursor, table)
            for old, new in renames:
                if old in columns and new not in columns:
                    schema_editor.execute(
                        f'ALTER TABLE "{table}" RENAME COLUMN "{old}" TO "{new}"'
                    )
    for name in TASK_MODELS:
        model = apps.get_model("tasks", name)
        if model._meta.db_table not in tables:
            schema_editor.create_model(model)
        else:
            _repair_table(schema_editor, model)


class Migration(migrations.Migration):
    dependencies = [("tasks", "0005_fix_task_nullability")]

    operations = [migrations.RunPython(repair, migrations.RunPython.noop)]
