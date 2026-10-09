from datetime import date

import pytest
import time_machine

from apps.performance import services
from apps.performance.models import KPIWeightVersion
from apps.recurring.tests.conftest import (  # noqa: F401  (shared fixtures)
    category,
    grant_assign,
    ist,
    known_schedule_start,
    new_task,
    now,
    ops,
    own,
    private_media,
    resp,
    staff,
)
from apps.sla import services as sla_services
from apps.tasks import services as task_services


@pytest.fixture
def ops_version():
    return KPIWeightVersion.objects.get(configuration="OPERATIONS", version=1)


@pytest.fixture
def assign(admin_user, ops_version):
    """assign(employee) -> the seeded Operations version for the whole of 2026 onwards."""

    def _assign(employee, version=None, start=date(2026, 1, 1), end=None):
        return services.assign_kpi_version(
            actor=admin_user, employee=employee, version=version or ops_version,
            effective_from=start, effective_to=end,
        )

    return _assign


@pytest.fixture
def sla_24h(admin_user):
    """Manual HIGH-priority tasks get a 24-hour SLA from assignment (Phase 5.2 mechanism)."""
    sla_services.create_rule(actor=admin_user, code="PERF_TEST_24H", name="24 hours",
                             duration_minutes=1440)
    sla_services.set_priority_rule(actor=admin_user, priority="HIGH", rule_code="PERF_TEST_24H")


@pytest.fixture
def work(ops, new_task, ist):
    """Helpers that drive tasks through the EXISTING task services at given IST times."""

    class Work:
        def raise_task(self, assignee, *at, priority="HIGH", title="Work item"):
            with time_machine.travel(ist(*at), tick=False):
                return new_task(ops["manager"], assignee, title=title, priority=priority)

        def complete(self, task, user, *at):
            with time_machine.travel(ist(*at), tick=False):
                task.refresh_from_db()
                task_services.start_task(actor=user, task=task, version=task.version)
                task.refresh_from_db()
                task_services.complete_task(actor=user, task=task, version=task.version)

        def cancel(self, task, *at):
            with time_machine.travel(ist(*at), tick=False):
                task.refresh_from_db()
                task_services.cancel_task(actor=ops["manager"], task=task, version=task.version,
                                          reason="Not needed")

        def reassign(self, task, to, *at):
            with time_machine.travel(ist(*at), tick=False):
                task.refresh_from_db()
                task_services.reassign_task(actor=ops["manager"], task=task, version=task.version,
                                            assigned_to=to)

    return Work()
