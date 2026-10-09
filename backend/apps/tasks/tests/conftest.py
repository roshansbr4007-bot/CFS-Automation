import pytest
from django.contrib.auth.models import Permission
from django.utils import timezone

from apps.accounts import roles
from apps.org.models import Department
from apps.org.tests.factories import EmployeeFactory
from apps.tasks import services
from apps.tasks.models import TaskCategory


@pytest.fixture(autouse=True)
def private_media(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / "media")


@pytest.fixture
def staff(make_user):
    """A login with a role, linked to an active employee: returns (user, employee)."""

    def _staff(role=roles.EMPLOYEE, *, department="OPS", employee_active=True, **user_fields):
        user = make_user(*([role] if role else []), **user_fields)
        employee = EmployeeFactory(
            user=user,
            email=user.email,
            department=Department.objects.get(code=department),
            is_active=employee_active,
        )
        return user, employee

    return _staff


@pytest.fixture
def grant_assign():
    def _grant(user):
        permission = Permission.objects.get(codename="assign", content_type__app_label="tasks")
        user.user_permissions.add(permission)
        return type(user).objects.get(pk=user.pk)  # fresh instance: no cached permissions

    return _grant


def operations_category():
    """Seeded by migration 0008; get_or_create because transaction=True tests flush data."""
    return TaskCategory.objects.get_or_create(
        code="OPERATIONS", defaults={"name": "Operations"}
    )[0]


@pytest.fixture
def category():
    return operations_category()


@pytest.fixture
def new_task():
    """Create a task through the real service (so it is audited and history is written).

    Department and category are task-level values chosen by the creator (Phase 4). For test
    convenience this helper passes the assignee's department EXPLICITLY unless a test gives one;
    the service itself never derives it.
    """

    def _new_task(actor, assignee, **fields):
        return services.create_task(
            actor=actor,
            title=fields.pop("title", "Process SIP mandate"),
            assigned_to=assignee,
            department=fields.pop("department", assignee.department),
            category=fields.pop("category", None) or operations_category(),
            **fields,
        )

    return _new_task


@pytest.fixture
def ops(staff):
    """A typical Operations team: manager, two employees, all in OPS."""
    manager, manager_emp = staff(roles.OPERATIONS_MANAGER)
    rahul, rahul_emp = staff(roles.EMPLOYEE)
    amit, amit_emp = staff(roles.EMPLOYEE)
    return {
        "manager": manager,
        "manager_emp": manager_emp,
        "rahul": rahul,
        "rahul_emp": rahul_emp,
        "amit": amit,
        "amit_emp": amit_emp,
    }


@pytest.fixture
def now():
    return timezone.now()
