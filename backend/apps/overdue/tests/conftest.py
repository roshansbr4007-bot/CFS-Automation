from datetime import datetime

import pytest
import time_machine

from apps.core.timeutils import IST
from apps.sla import services as sla_services
from apps.tasks import services as task_services
from apps.tasks.tests.conftest import (  # noqa: F401  (shared fixtures)
    category,
    grant_assign,
    new_task,
    now,
    ops,
    private_media,
    staff,
)


@pytest.fixture
def ist():
    return lambda *parts: datetime(*parts, tzinfo=IST)


@pytest.fixture
def sla_60(admin_user):
    """Manual HIGH tasks get a 60-minute resolution SLA from assignment (Phase 5.2 mechanism):
    warning at 10:30, critical at 10:45, overdue at 11:00 for a task raised at 10:00."""
    sla_services.create_rule(actor=admin_user, code="OVERDUE_TEST_60M", name="One hour",
                             duration_minutes=60)
    sla_services.set_priority_rule(actor=admin_user, priority="HIGH",
                                   rule_code="OVERDUE_TEST_60M")


@pytest.fixture
def work(ops, new_task, ist, sla_60):
    """Drive tasks and the SLA checker only through the existing services."""

    class Work:
        def raise_task(self, assignee, *at, actor=None, **kwargs):
            kwargs.setdefault("priority", "HIGH")
            kwargs.setdefault("title", "Map RM codes")
            with time_machine.travel(ist(*at), tick=False):
                return new_task(actor or ops["manager"], assignee, **kwargs)

        def tick(self, *at):
            with time_machine.travel(ist(*at), tick=False):
                return sla_services.evaluate_clocks()

        def complete(self, task, user, *at):
            with time_machine.travel(ist(*at), tick=False):
                task.refresh_from_db()
                task_services.start_task(actor=user, task=task, version=task.version)
                task.refresh_from_db()
                task_services.complete_task(
                    actor=user, task=task, version=task.version, work_response="Work done."
                )

        def cancel(self, task, *at):
            with time_machine.travel(ist(*at), tick=False):
                task.refresh_from_db()
                task_services.cancel_task(actor=ops["manager"], task=task,
                                          version=task.version, reason="Not needed")

        def reassign(self, task, to, *at):
            with time_machine.travel(ist(*at), tick=False):
                task.refresh_from_db()
                task_services.reassign_task(actor=ops["manager"], task=task,
                                            version=task.version, assigned_to=to)

    return Work()
