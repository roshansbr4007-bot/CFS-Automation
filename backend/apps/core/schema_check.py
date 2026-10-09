"""Compare Django models with the real database schema (read-only).

Used by `python manage.py schema_audit` and by tests. It answers the question that migration
history cannot: do the tables actually match the models? (A database can claim a migration is
applied while holding tables from an older version of the code.)
"""

import re
from dataclasses import dataclass, field

SIMPLE_TYPES = {
    "integer": "integer",
    "bigint": "bigint",
    "smallint": "smallint",
    "text": "text",
    "boolean": "boolean",
    "timestamp with time zone": "timestamp with time zone",
    "time": "time without time zone",
    "date": "date",
    "jsonb": "jsonb",
    "uuid": "uuid",
    "double precision": "double precision",
}


@dataclass
class TableReport:
    table: str
    missing_table: bool = False
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_table and not self.problems


def expected_type(model_field, connection) -> tuple[str, int | tuple | None] | None:
    db_type = model_field.db_type(connection)
    if db_type is None:
        return None
    match = re.fullmatch(r"varchar\((\d+)\)", db_type)
    if match:
        return "character varying", int(match.group(1))
    # numeric(precision, scale): both must match, so numeric(6,3) or an unconstrained numeric
    # is drift against numeric(5,2).
    match = re.fullmatch(r"numeric\((\d+),\s*(\d+)\)", db_type)
    if match:
        return "numeric", (int(match.group(1)), int(match.group(2)))
    base = db_type.split(" CHECK")[0].strip()
    return SIMPLE_TYPES.get(base, base), None


def table_columns(cursor, table: str) -> dict[str, dict]:
    cursor.execute(
        """
        SELECT column_name, is_nullable, data_type, character_maximum_length,
               numeric_precision, numeric_scale
        FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = %s
        """,
        [table],
    )
    columns = {}
    for name, nullable, data_type, length, precision, scale in cursor.fetchall():
        if data_type == "numeric":  # only numeric: integer types also report a precision
            length = (precision, scale)  # (None, None) for an unconstrained numeric
        columns[name] = {"nullable": nullable == "YES", "type": data_type, "length": length}
    return columns


def audit_model(model, connection) -> TableReport:
    meta = model._meta
    report = TableReport(meta.db_table)
    with connection.cursor() as cursor:
        if meta.db_table not in connection.introspection.table_names(cursor):
            report.missing_table = True
            return report
        columns = table_columns(cursor, meta.db_table)
        constraints = connection.introspection.get_constraints(cursor, meta.db_table)
    expected_columns = set()
    for f in meta.local_fields:
        col = f.column
        expected_columns.add(col)
        if col not in columns:
            report.problems.append(f"missing column {col}")
            continue
        actual = columns[col]
        if f.null != actual["nullable"]:
            want = "NULL" if f.null else "NOT NULL"
            have = "NULL" if actual["nullable"] else "NOT NULL"
            report.problems.append(f"nullability {col}: model {want}, database {have}")
        wanted = expected_type(f, connection)
        if wanted and (wanted[0], wanted[1]) != (actual["type"], actual["length"]):
            have = (actual["type"], actual["length"])
            report.problems.append(f"type {col}: model {wanted}, database {have}")
        if f.remote_field and f.db_constraint:
            target = f.remote_field.model._meta.db_table
            has_fk = any(
                c["foreign_key"] and c["columns"] == [col] and c["foreign_key"][0] == target
                for c in constraints.values()
            )
            if not has_fk:
                report.problems.append(f"foreign key {col} -> {target} missing")
    for extra in sorted(set(columns) - expected_columns):
        nullable = columns[extra]["nullable"]
        report.problems.append(
            f"extra column {extra} ({'NULL' if nullable else 'NOT NULL'})"
        )
    for constraint in [*meta.constraints, *meta.indexes]:
        if constraint.name not in constraints:
            report.problems.append(f"missing constraint/index {constraint.name}")
    return report


def audit(models, connection) -> list[TableReport]:
    return [audit_model(m, connection) for m in models if m._meta.managed]
