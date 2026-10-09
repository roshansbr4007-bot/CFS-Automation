from datetime import date, datetime

import pytest

from apps.core.timeutils import IST
from apps.recurring.models import RecurringSchedule, Responsibility, ResponsibilityOwner
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
    """ist(2026, 10, 5, 10, 0) -> aware IST datetime."""
    return lambda *parts: datetime(*parts, tzinfo=IST)


@pytest.fixture(autouse=True)
def known_schedule_start():
    """The seeds start on the day migrations ran; tests use fixed 2026 dates instead.
    Daily schedules start Monday 5 Oct 2026 and the monthly one 1 Oct 2026 (no earlier backlog;
    recovery tests move these dates back explicitly)."""
    RecurringSchedule.objects.filter(frequency="DAILY").update(effective_from=date(2026, 10, 5))
    RecurringSchedule.objects.filter(frequency="MONTHLY").update(effective_from=date(2026, 10, 1))


@pytest.fixture
def resp():
    return lambda code: Responsibility.objects.get(code=code)


@pytest.fixture
def own(admin_user):
    """own(responsibility, employee, start, end=None): an ownership period (test data)."""

    def _own(responsibility, employee, start=date(2026, 1, 1), end=None):
        return ResponsibilityOwner.objects.create(
            responsibility=responsibility,
            employee=employee,
            effective_from=start,
            effective_to=end,
            assigned_by=admin_user,
        )

    return _own
