"""`python manage.py calculate_performance --year 2026 --month 10 [--employee ID]`

On-demand monthly calculation (approved P7; not scheduled). Employees without a KPI assignment
for the month are skipped and listed; finalized months are skipped and listed.

Phase 7.2: each employee is calculated by the engine of the plan that applies on the month's
last day (services.calculate_month): a legacy weight assignment keeps the legacy engine; a KRA
plan (HR override or department + system role default) uses the KRA engine. KRA months under
review, months not started yet, employees who joined later and ambiguous system roles are
skipped and listed.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.org.models import Employee
from apps.performance import kra_engine, services


class Command(BaseCommand):
    help = "Calculate (or recalculate, until finalized) monthly performance."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, required=True)
        parser.add_argument("--month", type=int, required=True)
        parser.add_argument("--employee", type=int, help="One employee id (default: all active).")

    def handle(self, *args, year, month, employee=None, **options):
        if not 1 <= month <= 12:
            raise CommandError("--month must be 1 to 12.")
        employees = Employee.objects.filter(is_active=True).order_by("full_name", "id")
        if employee is not None:
            employees = Employee.objects.filter(pk=employee)
            if not employees.exists():
                raise CommandError(f"No employee with id {employee}.")
        calculated, skipped, ambiguous, under_review, not_started, not_joined = (
            [], [], [], [], [], []
        )
        misconfigured, locked, failed = [], [], []
        for person in employees:
            try:
                services.calculate_month(actor=None, employee=person, year=year, month=month)
                calculated.append(person)
            except kra_engine.AmbiguousRole:
                ambiguous.append(person)
            except services.NoKPIAssignment:
                skipped.append(person)
            except kra_engine.UnderReviewNotRecalculated:
                under_review.append(person)
            except kra_engine.MonthNotStarted:
                not_started.append(person)
            except kra_engine.NotEmployedInPeriod:
                not_joined.append(person)
            except kra_engine.VerificationConfigurationError:
                misconfigured.append(person)
            except services.PerformanceError as exc:
                if exc.message.startswith("Finalized"):
                    locked.append(person)
                else:
                    failed.append(f"{person.full_name} ({exc.message})")
        self.stdout.write(f"Performance {year}-{month:02d}: {len(calculated)} calculated.")
        for label, people in (
            ("no KPI assignment", skipped),
            ("ambiguous system role; set an HR override", ambiguous),
            ("KRA month under review; not recalculated", under_review),
            ("month has not started", not_started),
            ("joined after this month", not_joined),
            ("verification configuration error; see the KPI components", misconfigured),
            ("already finalized", locked),
        ):
            if people:
                names = ", ".join(p.full_name for p in people)
                self.stdout.write(f"Skipped ({label}): {names}")
        if failed:
            self.stdout.write(f"Not calculated: {'; '.join(failed)}")
