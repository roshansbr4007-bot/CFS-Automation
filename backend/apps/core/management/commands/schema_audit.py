"""`python manage.py schema_audit [app_label ...]` — read-only model-vs-database comparison.

Exit code 1 when anything differs, so it can guard CI and deployments. Never changes data.
"""

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.core.schema_check import audit

DEFAULT_APPS = [
    "accounts",
    "audit",
    "org",
    "tasks",
    "sla",
    "notifications",
    "calendars",
    "recurring",
    "command_center",
    "performance",
    "overdue",
]


class Command(BaseCommand):
    help = "Compare models with the real tables: columns, nulls, types, FKs, constraints."

    def add_arguments(self, parser):
        parser.add_argument("app_labels", nargs="*", help="Default: all project apps.")

    def handle(self, *args, app_labels=None, **options):
        labels = app_labels or DEFAULT_APPS
        models = [m for label in labels for m in apps.get_app_config(label).get_models()]
        reports = audit(models, connection)
        bad = 0
        for report in reports:
            if report.ok:
                self.stdout.write(f"OK       {report.table}")
                continue
            bad += 1
            if report.missing_table:
                self.stdout.write(f"MISSING  {report.table} (table does not exist)")
            for problem in report.problems:
                self.stdout.write(f"DRIFT    {report.table}: {problem}")
        if bad:
            raise CommandError(f"{bad} table(s) differ from the models.")
        self.stdout.write(self.style.SUCCESS(f"All {len(reports)} tables match the models."))
