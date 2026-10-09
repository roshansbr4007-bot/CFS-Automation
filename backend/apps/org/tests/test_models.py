import pytest
from django.db import IntegrityError, transaction

from apps.org.models import Department, Employee, EmployeeDailyLogin
from apps.org.tests.factories import EmployeeFactory

pytestmark = pytest.mark.django_db


def test_seeded_departments_and_only_ops_is_live():
    rows = {d.code: d.is_live for d in Department.objects.all()}
    assert rows == {"HR": False, "INS": False, "LOAN": False, "OPS": True, "RM": False}


def test_department_code_must_be_uppercase_in_the_database():
    with pytest.raises(IntegrityError), transaction.atomic():
        Department.objects.create(code="ops2", name="Lower case")


def test_department_code_cannot_be_blank_in_the_database():
    with pytest.raises(IntegrityError), transaction.atomic():
        Department.objects.create(code="", name="Blank")


def test_employee_email_is_lowercased_on_save():
    employee = EmployeeFactory(email="  Mixed.Case@Example.COM ")
    assert employee.email == "mixed.case@example.com"


def test_employee_email_unique_ignoring_case_in_the_database(dept):
    EmployeeFactory(email="same@example.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        # bulk_create skips save(), so the database constraint itself is tested
        Employee.objects.bulk_create(
            [Employee(full_name="X", email="SAME@example.com", department=dept("OPS"))]
        )


def test_employee_code_unique_only_when_set(dept):
    EmployeeFactory(employee_code=None)
    EmployeeFactory(employee_code=None)
    Employee.objects.bulk_create(
        [
            Employee(
                full_name=n, email=f"{n}@example.com", department=dept("OPS"), employee_code=""
            )
            for n in ("a", "b")
        ]
    )
    EmployeeFactory(employee_code="CFS-001")
    with pytest.raises(IntegrityError), transaction.atomic():
        EmployeeFactory(employee_code="CFS-001")


def test_employee_cannot_be_own_manager_in_the_database():
    employee = EmployeeFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        Employee.objects.filter(pk=employee.pk).update(reporting_manager=employee.pk)


def test_one_login_per_employee(make_user):
    user = make_user()
    EmployeeFactory(user=user)
    with pytest.raises(IntegrityError), transaction.atomic():
        EmployeeFactory(user=user)


def test_one_daily_login_per_employee_and_date():
    employee = EmployeeFactory()
    EmployeeDailyLogin.objects.create(
        employee=employee, work_date="2026-10-05", first_login_at="2026-10-05T04:00:00Z"
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        EmployeeDailyLogin.objects.create(
            employee=employee, work_date="2026-10-05", first_login_at="2026-10-05T05:00:00Z"
        )


def test_string_representations():
    employee = EmployeeFactory(full_name="Asha Verma")
    fact = EmployeeDailyLogin.objects.create(
        employee=employee, work_date="2026-10-05", first_login_at="2026-10-05T04:00:00Z"
    )
    assert str(employee) == "Asha Verma"
    assert str(employee.department) == "OPS"
    assert str(fact) == f"{employee.pk} 2026-10-05"
