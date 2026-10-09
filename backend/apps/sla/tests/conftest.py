from datetime import datetime

import pytest

from apps.core.timeutils import IST
from apps.org.models import Department
from apps.tasks.models import TaskTemplate
from apps.tasks.tests.conftest import (  # noqa: F401  (shared fixtures)
    grant_assign,
    new_task,
    now,
    ops,
    private_media,
    staff,
)


@pytest.fixture
def template():
    return lambda code: TaskTemplate.objects.get(code=code)


@pytest.fixture
def ist():
    """ist(2026, 10, 5, 11, 30) -> aware IST datetime."""
    return lambda *parts: datetime(*parts, tzinfo=IST)


@pytest.fixture
def dept():
    return lambda code: Department.objects.get(code=code)
