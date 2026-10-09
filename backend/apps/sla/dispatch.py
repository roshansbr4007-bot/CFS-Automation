"""SLA engine announcements for other apps (Phase 9). Deliberately free of imports from
services.py (no import cycle). The SLA engine only ANNOUNCES; listeners react.

resolution_clock_overdue(sender=TaskSla, clock=<TaskSla>, source="TICK" | "COMPLETION")
    Sent once when a task's RESOLUTION clock first counts as overdue: the SLA checker records
    its 100% threshold ("TICK"), or the task completes after its deadline before the checker
    saw it ("COMPLETION"). Listeners must never break or roll back the SLA transition.
"""

from django.dispatch import Signal

resolution_clock_overdue = Signal()
