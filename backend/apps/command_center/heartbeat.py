"""Records scheduler heartbeats around the existing Celery jobs (observability only).

run_with_heartbeat(job, fn) calls the UNCHANGED business function and returns its result or
re-raises its exception exactly as before. Every heartbeat write is isolated (own savepoint, all
errors caught and logged), so a heartbeat problem can never make the business task fail.
"""

import logging

from django.db import transaction
from django.utils import timezone

from .models import SchedulerHeartbeat

logger = logging.getLogger(__name__)

GENERATOR_JOB = "recurring.generate_recurring_tasks"
SLA_JOB = "sla.evaluate_sla_clocks"
JOBS = {
    GENERATOR_JOB: "Recurring task generator",
    SLA_JOB: "SLA checker",
}


def _write(job: str, **fields) -> None:
    try:
        with transaction.atomic():
            SchedulerHeartbeat.objects.update_or_create(job=job, defaults=fields)
    except Exception:  # observability must never break the business job
        logger.warning("Could not record the scheduler heartbeat for %s", job, exc_info=True)


def run_with_heartbeat(job: str, fn):
    _write(job, last_started_at=timezone.now(), last_status=SchedulerHeartbeat.STATUS_RUNNING)
    try:
        result = fn()
    except Exception as error:
        _write(
            job,
            last_finished_at=timezone.now(),
            last_status=SchedulerHeartbeat.STATUS_FAILED,
            last_error=f"{type(error).__name__}: {error}"[:2000],
        )
        raise
    finished = timezone.now()
    _write(
        job,
        last_finished_at=finished,
        last_success_at=finished,
        last_status=SchedulerHeartbeat.STATUS_SUCCESS,
        last_summary=result if isinstance(result, dict) else {},
        last_error="",
    )
    return result
