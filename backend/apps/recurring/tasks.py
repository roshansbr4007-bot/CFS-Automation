from celery import shared_task

from apps.command_center.heartbeat import GENERATOR_JOB, run_with_heartbeat

from .generator import generate_due_occurrences


@shared_task(name="recurring.generate_recurring_tasks")
def generate_recurring_tasks() -> dict:
    """Celery Beat, every minute: generate the responsibility work that is due.
    The heartbeat (Phase 6A) only observes the run; the generator itself is unchanged."""
    return run_with_heartbeat(GENERATOR_JOB, generate_due_occurrences)
