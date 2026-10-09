"""`python manage.py open_missing_overdue_cases --since YYYY-MM-DD [--dry-run]`

Manual repair only (approved Q2). Recovers overdue cases that should have opened but did not
(e.g. a failure recorded as overdue_case.open_failed), for RESOLUTION clocks that became
overdue inside the window. It is NOT a historical backfill: --since may not be earlier than
the moment Phase 9 was deployed (when this app's first migration was applied), and it never
runs automatically.
"""

from datetime import time

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_date

from apps.core.timeutils import ist_datetime, to_ist
from apps.overdue import services
from apps.overdue.models import OpenedVia


class Command(BaseCommand):
    help = "Recover missing overdue cases since a date (manual repair; never a backfill)."

    def add_arguments(self, parser):
        parser.add_argument("--since", required=True, help="IST date, YYYY-MM-DD.")
        parser.add_argument("--dry-run", action="store_true", help="Only list what would open.")

    def handle(self, *args, since, dry_run=False, **options):
        day = parse_date(since)
        if day is None:
            raise CommandError("--since must be a date in the format YYYY-MM-DD.")
        deployed = services.phase9_deployed_at()
        if deployed is None:
            raise CommandError("Phase 9 is not migrated; nothing to repair.")
        if day < to_ist(deployed).date():
            raise CommandError(
                f"--since {day} is before Phase 9 was deployed ({to_ist(deployed):%Y-%m-%d}); "
                "this command recovers missing cases, it never backfills history."
            )
        window_start = max(ist_datetime(day, time(0, 0)), deployed)
        clocks = list(services.missing_case_clocks(window_start).select_related("task"))
        if dry_run:
            for clock in clocks:
                self.stdout.write(f"would open: clock {clock.pk} (task {clock.task_id})")
            self.stdout.write(f"{len(clocks)} missing case(s) since {day} (dry run).")
            return
        opened = 0
        for clock in clocks:
            with transaction.atomic():
                if services.open_case_for_clock(clock, source=OpenedVia.REPAIR) is not None:
                    opened += 1
        self.stdout.write(f"{opened} missing case(s) opened since {day}.")
