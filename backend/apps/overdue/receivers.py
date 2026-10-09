"""Listens to the SLA engine's resolution_clock_overdue signal (Phase 9).

Opening a case runs in its own savepoint: whatever goes wrong here is logged and audited
(overdue_case.open_failed) and NEVER rolls back or blocks the SLA transition that sent the
signal. A missed case can be recovered with `open_missing_overdue_cases --since <date>`.
"""

import logging

from django.db import transaction

from . import services

logger = logging.getLogger(__name__)


def on_resolution_clock_overdue(sender, clock, source, **kwargs):
    try:
        with transaction.atomic():
            services.open_case_for_clock(clock, source=source)
    except Exception:
        logger.exception("Could not open the overdue case for clock %s (%s)", clock.pk, source)
        services.record_open_failure(clock, source)
