import factory

from apps.org.models import Department, Employee


class EmployeeFactory(factory.django.DjangoModelFactory):
    """Test data only. Bypasses services, so it writes no audit rows."""

    class Meta:
        model = Employee

    full_name = factory.Sequence(lambda n: f"Employee {n}")
    email = factory.Sequence(lambda n: f"employee{n}@example.com")
    department = factory.LazyFunction(lambda: Department.objects.get(code="OPS"))
    is_active = True
