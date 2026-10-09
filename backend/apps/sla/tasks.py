from celery import shared_task

from apps.command_center.heartbeat import SLA_JOB, run_with_heartbeat

from .services import evaluate_clocks


@shared_task(name="sla.evaluate_sla_clocks")
def evaluate_sla_clocks() -> dict:
    """Celery Beat, every minute: the same pass `sla_tick` runs (thresholds, notifications).
    The heartbeat (Phase 6A) only observes the run; the SLA checker itself is unchanged."""
    return run_with_heartbeat(SLA_JOB, evaluate_clocks)
