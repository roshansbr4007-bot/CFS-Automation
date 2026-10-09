import pytest

from apps.accounts import roles
from apps.org.models import Department

from .factories import EmployeeFactory


@pytest.fixture
def dept(db):
    return lambda code: Department.objects.get(code=code)


@pytest.fixture
def person(make_user):
    """A login (optionally with a role) linked to an employee record: returns (user, employee)."""

    def _person(role=None, *, department="OPS", is_active=True, **employee_fields):
        user = make_user(*([role] if role else []))
        employee = EmployeeFactory(
            user=user,
            email=user.email,
            department=Department.objects.get(code=department),
            is_active=is_active,
            **employee_fields,
        )
        return user, employee

    return _person


@pytest.fixture
def hr_client(client_for, make_user):
    return client_for(make_user(roles.HR))


@pytest.fixture
def ops_manager(person):
    return person(roles.OPERATIONS_MANAGER, department="OPS")
