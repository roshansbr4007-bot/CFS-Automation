"""Phase 7.1: Employee.date_of_joining (optional) through the existing employee API."""

import pytest

from apps.audit.models import AuditLog
from apps.org.tests.factories import EmployeeFactory

EMPLOYEES = "/api/v1/employees/"
pytestmark = pytest.mark.django_db


def test_create_with_and_without_a_joining_date(hr_client, dept):
    base = {"full_name": "Ravi Sharma", "department": dept("OPS").pk}
    with_date = hr_client.post(
        EMPLOYEES, {**base, "email": "ravi@example.com", "date_of_joining": "2026-04-15"}
    )
    assert with_date.status_code == 201
    assert with_date.json()["date_of_joining"] == "2026-04-15"
    row = AuditLog.objects.get(action="employee.created", entity_id=str(with_date.json()["id"]))
    assert row.new_value["date_of_joining"] == "2026-04-15"
    without = hr_client.post(EMPLOYEES, {**base, "email": "anu@example.com"})
    assert without.status_code == 201 and without.json()["date_of_joining"] is None
    bad = hr_client.post(EMPLOYEES, {**base, "email": "x@example.com",
                                     "date_of_joining": "15/04/2026"})
    assert bad.status_code == 400


def test_update_and_clear_the_joining_date_is_audited(hr_client):
    employee = EmployeeFactory()
    url = f"{EMPLOYEES}{employee.pk}/"
    set_date = hr_client.patch(url, {"version": employee.version, "date_of_joining": "2025-07-01"})
    assert set_date.status_code == 200 and set_date.json()["date_of_joining"] == "2025-07-01"
    row = AuditLog.objects.get(action="employee.updated")
    assert row.old_value == {"date_of_joining": None}
    assert row.new_value == {"date_of_joining": "2025-07-01"}
    cleared = hr_client.patch(
        url, {"version": set_date.json()["version"], "date_of_joining": None}
    )
    assert cleared.status_code == 200 and cleared.json()["date_of_joining"] is None
    unchanged = hr_client.patch(url, {"version": cleared.json()["version"], "full_name": "New"})
    assert unchanged.json()["date_of_joining"] is None
