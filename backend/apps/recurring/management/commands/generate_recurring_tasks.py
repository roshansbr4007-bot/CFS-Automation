"""`python manage.py generate_recurring_tasks` - one manual pass of the generator (support and
testing). The scheduler itself is Celery Beat; this is not a second scheduler."""

from django.core.management.base import BaseCommand

from apps.recurring.generator import generate_due_occurrences


class Command(BaseCommand):
    help = "Run the recurring responsibility generator once."

    def handle(self, *args, **options):
        summary = generate_due_occurrences()
        self.stdout.write(
            "Recurring generator: "
            + ", ".join(f"{count} {name}" for name, count in summary.items())
            + "."
        )
