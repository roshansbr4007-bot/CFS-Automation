"""The SLA checker: `python manage.py sla_tick --loop 60` (or `--once`).

Calls apps.sla.services.evaluate_clocks(), the same function a Celery beat task will call
later. Safe to run many times or restart: thresholds and notifications are recorded once.
"""

import argparse
import time

from django.core.management.base import BaseCommand

from apps.sla.services import evaluate_clocks


class Command(BaseCommand):
    help = "Evaluate SLA clocks, record 50/75/100% thresholds once, send notifications."

    def add_arguments(self, parser):
        parser.add_argument("--loop", type=int, default=0, help="Repeat every N seconds.")
        parser.add_argument("--once", action="store_true", help="Run one pass (default).")
        parser.add_argument("--max-runs", type=int, default=0, help=argparse.SUPPRESS)

    def handle(self, *args, loop=0, once=False, max_runs=0, **options):
        runs = 0
        while True:
            summary = evaluate_clocks()
            runs += 1
            self.stdout.write(
                f"SLA tick: {summary['thresholds']} threshold(s), "
                f"{summary['login_clocks_started']} login clock(s) started, "
                f"{summary['emails_sent']} email(s) sent."
            )
            if summary["escalation_gaps"]:
                self.stderr.write(
                    f"WARNING: {summary['escalation_gaps']} overdue escalation(s) had no Boss "
                    "recipient. See audit action task.sla_escalation_recipient_missing."
                )
            if once or loop <= 0 or (max_runs and runs >= max_runs):
                return
            time.sleep(loop)

