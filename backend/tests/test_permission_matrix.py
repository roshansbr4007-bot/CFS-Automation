"""R1: every API endpoint x anonymous and all four roles (Phase 1 plan s.5, Phase 2 org s.5)."""

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import roles
from apps.accounts.tests.factories import UserFactory
from apps.org.models import Department, Employee
from apps.performance.models import KPI, BandScheme, KPIWeightVersion, ScoringRule
from apps.tasks.models import Task, TaskCategory

ANON = "anonymous"
CALLERS = [ANON, roles.EMPLOYEE, roles.OPERATIONS_MANAGER, roles.HR, roles.ADMIN]

# access level -> callers allowed
ALLOWED = {
    "public": set(CALLERS),
    "logged_in": {roles.EMPLOYEE, roles.OPERATIONS_MANAGER, roles.HR, roles.ADMIN},
    "manage_users": {roles.ADMIN},
    "audit": {roles.HR, roles.ADMIN},
    # Phase 2 org (record-level scope is tested in apps/org/tests/test_scope.py)
    "admin": {roles.ADMIN},
    "employee_list": {roles.OPERATIONS_MANAGER, roles.HR, roles.ADMIN},
    "hr_admin": {roles.HR, roles.ADMIN},
    # Phase 3 tasks (record-level rules are tested in apps/tasks/tests)
    # Phase 4: HR now creates tasks too, and HR / Admin physically delete tasks.
    "task_create": {roles.EMPLOYEE, roles.OPERATIONS_MANAGER, roles.HR, roles.ADMIN},
    "task_delete": {roles.HR, roles.ADMIN},
    # Phase 5: responsibilities are read by HR / Ops Manager / Admin (record scope and writes
    # are checked in apps/recurring/tests); schedule writes and the calendar are Admin's.
    "responsibility_view": {roles.OPERATIONS_MANAGER, roles.HR, roles.ADMIN},
    # Phase 7.1 KRA configuration: HR prepares, Admin approves; Ops Manager / Employee: none
    "kpi_config_read": {roles.HR, roles.ADMIN},
    "kpi_config_write": {roles.HR},
    "kpi_config_approve": {roles.ADMIN},
    # Phase 7.3 KRA review: HR reviews and finalizes; Admin reads and reopens; Ops Manager /
    # Employee: none (Employee Home is Phase 7.4)
    "kra_review_read": {roles.HR, roles.ADMIN},
    "kra_review_write": {roles.HR},
    "kra_review_finalize": {roles.HR},
    "kra_review_reopen": {roles.HR, roles.ADMIN},
}

ENDPOINTS = [
    ("get", "/api/v1/auth/csrf/", "public"),
    ("get", "/api/v1/health/", "public"),
    ("get", "/api/v1/auth/me/", "logged_in"),
    ("post", "/api/v1/auth/password-change/", "logged_in"),
    ("post", "/api/v1/auth/logout/", "logged_in"),
    ("get", "/api/v1/users/", "manage_users"),
    ("post", "/api/v1/users/", "manage_users"),
    ("get", "/api/v1/users/{target}/", "manage_users"),
    ("patch", "/api/v1/users/{target}/", "manage_users"),
    ("post", "/api/v1/users/{target}/set-password/", "manage_users"),
    ("get", "/api/v1/roles/", "manage_users"),
    ("get", "/api/v1/audit-log/", "audit"),
    ("get", "/api/v1/departments/", "logged_in"),
    ("get", "/api/v1/departments/{dept}/", "logged_in"),
    ("post", "/api/v1/departments/", "admin"),
    ("patch", "/api/v1/departments/{dept}/", "admin"),
    ("get", "/api/v1/employees/", "employee_list"),
    ("post", "/api/v1/employees/", "hr_admin"),
    ("get", "/api/v1/employees/me/", "logged_in"),
    ("get", "/api/v1/employees/me/logins/", "logged_in"),
    ("get", "/api/v1/employees/{emp}/", "logged_in"),
    ("get", "/api/v1/employees/{emp}/logins/", "logged_in"),
    ("patch", "/api/v1/employees/{emp}/", "hr_admin"),
    ("post", "/api/v1/employees/{emp}/link-login/", "admin"),
    ("post", "/api/v1/employees/{emp}/unlink-login/", "admin"),
    ("get", "/api/v1/tasks/", "logged_in"),
    ("post", "/api/v1/tasks/", "task_create"),
    ("get", "/api/v1/tasks/assignees/", "task_create"),
    # Phase 6 SLA foundation and notifications
    ("get", "/api/v1/task-templates/", "logged_in"),
    ("post", "/api/v1/tasks/sla-preview/", "task_create"),
    # Phase 4 task assignment & hand-off
    ("delete", "/api/v1/tasks/{task}/", "task_delete"),
    ("get", "/api/v1/task-categories/", "logged_in"),
    ("post", "/api/v1/task-categories/", "admin"),
    ("patch", "/api/v1/task-categories/{category}/", "admin"),
    # Phase 5 responsibilities, schedules, occurrences and the Company Calendar
    ("get", "/api/v1/responsibilities/", "responsibility_view"),
    ("post", "/api/v1/responsibilities/", "responsibility_view"),
    ("get", "/api/v1/recurring-schedules/", "responsibility_view"),
    # Phase A: schedules are written by whoever manages the responsibility (scope in services)
    ("post", "/api/v1/recurring-schedules/", "responsibility_view"),
    ("post", "/api/v1/responsibilities/setup/", "responsibility_view"),
    ("get", "/api/v1/schedule-occurrences/", "responsibility_view"),
    ("get", "/api/v1/calendars/company/", "logged_in"),
    ("get", "/api/v1/calendars/company/working-days/", "logged_in"),
    ("post", "/api/v1/calendars/company/days/", "admin"),
    # Phase 5.1: own daily activities (any signed-in user) and Admin (Boss) monitoring
    ("get", "/api/v1/tasks/daily-activities/", "logged_in"),
    ("get", "/api/v1/operations/daily-summary/", "admin"),
    ("get", "/api/v1/operations/employees/{emp}/daily-activities/", "admin"),
    ("get", "/api/v1/operations/employees/{emp}/assigned-tasks/", "admin"),
    # Phase 5.2: duration rules and the priority -> SLA rule mapping (configuration)
    ("post", "/api/v1/sla-rules/", "admin"),
    ("get", "/api/v1/sla-priority-rules/", "logged_in"),
    ("put", "/api/v1/sla-priority-rules/HIGH/", "admin"),
    # Phase 6A: Admin (Boss) Command Center (read-only)
    ("get", "/api/v1/command-center/summary/", "admin"),
    # Phase 6B: read-only drill-down and SLA attention (Admin only)
    ("get", "/api/v1/command-center/employees/{emp}/", "admin"),
    ("get", "/api/v1/command-center/sla-attention/", "admin"),
    # Phase 9: overdue cases (scoped list/report; exports for organisation-wide roles)
    ("get", "/api/v1/overdue-cases/", "logged_in"),
    ("get", "/api/v1/overdue-cases/reports/", "logged_in"),
    ("get", "/api/v1/overdue-cases/reports/export/csv/", "hr_admin"),
    ("get", "/api/v1/overdue-cases/reports/export/excel/", "hr_admin"),
    ("get", "/api/v1/sla-rules/", "logged_in"),
    ("get", "/api/v1/sla-settings/", "logged_in"),
    ("patch", "/api/v1/sla-settings/", "admin"),
    ("get", "/api/v1/notifications/", "logged_in"),
    ("get", "/api/v1/notifications/unread-count/", "logged_in"),
    ("post", "/api/v1/notifications/read-all/", "logged_in"),
    # Phase 7.1 KRA configuration (configuration only; no calculation / review endpoints)
    ("get", "/api/v1/performance/kpis/", "kpi_config_read"),
    ("patch", "/api/v1/performance/kpis/{kpi}/", "kpi_config_write"),
    ("get", "/api/v1/performance/plans/", "kpi_config_read"),
    ("post", "/api/v1/performance/plans/", "kpi_config_write"),
    ("get", "/api/v1/performance/plans/{plan}/", "kpi_config_read"),
    ("patch", "/api/v1/performance/plans/{plan}/", "kpi_config_write"),
    ("post", "/api/v1/performance/plans/{plan}/clone/", "kpi_config_write"),
    ("post", "/api/v1/performance/plans/{plan}/activate/", "kpi_config_approve"),
    ("post", "/api/v1/performance/plans/{plan}/retire/", "kpi_config_approve"),
    ("get", "/api/v1/performance/plans/{plan}/lines/", "kpi_config_read"),
    ("post", "/api/v1/performance/plans/{plan}/lines/", "kpi_config_write"),
    ("patch", "/api/v1/performance/plan-lines/{line}/", "kpi_config_write"),
    ("get", "/api/v1/performance/plan-lines/{line}/components/", "kpi_config_read"),
    ("post", "/api/v1/performance/plan-lines/{line}/components/", "kpi_config_write"),
    ("get", "/api/v1/performance/plans/{plan}/deduction-rules/", "kpi_config_read"),
    ("post", "/api/v1/performance/plans/{plan}/deduction-rules/", "kpi_config_write"),
    ("get", "/api/v1/performance/scoring-rules/", "kpi_config_read"),
    ("post", "/api/v1/performance/scoring-rules/", "kpi_config_write"),
    ("patch", "/api/v1/performance/scoring-rules/{rule}/", "kpi_config_write"),
    ("post", "/api/v1/performance/scoring-rules/{rule}/clone/", "kpi_config_write"),
    ("post", "/api/v1/performance/scoring-rules/{rule}/activate/", "kpi_config_approve"),
    ("post", "/api/v1/performance/scoring-rules/{rule}/retire/", "kpi_config_approve"),
    ("get", "/api/v1/performance/band-schemes/", "kpi_config_read"),
    ("post", "/api/v1/performance/band-schemes/", "kpi_config_write"),
    ("patch", "/api/v1/performance/band-schemes/{scheme}/", "kpi_config_write"),
    ("post", "/api/v1/performance/band-schemes/{scheme}/activate/", "kpi_config_approve"),
    ("get", "/api/v1/performance/plan-defaults/", "kpi_config_read"),
    ("post", "/api/v1/performance/plan-defaults/", "kpi_config_write"),
    ("get", "/api/v1/performance/assignments/", "kpi_config_read"),
    ("post", "/api/v1/performance/assignments/", "kpi_config_write"),
    ("get", "/api/v1/performance/plan-resolution/", "kpi_config_read"),
    # Phase 7.5A: the read-only readiness check, and configuration routes the new screens use
    # that were not listed before (999999 does not exist: allowed callers get 404)
    ("get", "/api/v1/performance/plans/{plan}/readiness/", "kpi_config_read"),
    ("get", "/api/v1/performance/kpis/{kpi}/", "kpi_config_read"),
    ("delete", "/api/v1/performance/plans/999999/", "kpi_config_write"),
    ("get", "/api/v1/performance/plan-lines/{line}/", "kpi_config_read"),
    ("delete", "/api/v1/performance/plan-lines/999999/", "kpi_config_write"),
    ("get", "/api/v1/performance/components/999999/", "kpi_config_read"),
    ("patch", "/api/v1/performance/components/999999/", "kpi_config_write"),
    ("delete", "/api/v1/performance/components/999999/", "kpi_config_write"),
    ("get", "/api/v1/performance/deduction-rules/999999/", "kpi_config_read"),
    ("patch", "/api/v1/performance/deduction-rules/999999/", "kpi_config_write"),
    ("delete", "/api/v1/performance/deduction-rules/999999/", "kpi_config_write"),
    ("get", "/api/v1/performance/scoring-rules/{rule}/", "kpi_config_read"),
    ("get", "/api/v1/performance/band-schemes/{scheme}/", "kpi_config_read"),
    ("post", "/api/v1/performance/band-schemes/{scheme}/clone/", "kpi_config_write"),
    ("post", "/api/v1/performance/band-schemes/{scheme}/retire/", "kpi_config_approve"),
    ("post", "/api/v1/performance/plan-defaults/999999/end/", "kpi_config_write"),
    ("post", "/api/v1/performance/assignments/999999/end/", "kpi_config_write"),
    # Phase 7.3 KRA review (month 999999 does not exist: allowed callers get 400 / 404)
    ("post", "/api/v1/performance/months/calculate/", "kra_review_write"),
    ("get", "/api/v1/performance/months/999999/", "kra_review_read"),
    ("get", "/api/v1/performance/months/999999/blockers/", "kra_review_read"),
    ("post", "/api/v1/performance/months/999999/submit/", "kra_review_write"),
    ("post", "/api/v1/performance/months/999999/return/", "kra_review_write"),
    ("post", "/api/v1/performance/months/999999/finalize/", "kra_review_finalize"),
    ("post", "/api/v1/performance/months/999999/reopen/", "kra_review_reopen"),
    ("post", "/api/v1/performance/months/999999/kpis/{kpi}/adjust/", "kra_review_write"),
    ("post", "/api/v1/performance/months/999999/deductions/", "kra_review_write"),
    ("post", "/api/v1/performance/months/999999/deductions/1/reverse/", "kra_review_write"),
    ("post", "/api/v1/performance/months/999999/gap-decisions/", "kra_review_write"),
    ("post", "/api/v1/performance/months/999999/components/1/manual-entry/",
     "kra_review_write"),
    ("post", "/api/v1/performance/months/999999/components/1/mark-na/", "kra_review_write"),
    ("get", "/api/v1/performance/approved-leave/", "kra_review_read"),
    ("post", "/api/v1/performance/approved-leave/", "kra_review_write"),
    ("post", "/api/v1/performance/approved-leave/999999/cancel/", "kra_review_write"),
    ("get", "/api/v1/performance/task-overrides/", "kra_review_read"),
    ("post", "/api/v1/performance/task-overrides/", "kra_review_write"),
    ("post", "/api/v1/performance/task-overrides/999999/remove/", "kra_review_write"),
    ("get", "/api/v1/performance/annual/", "kra_review_read"),
    # Phase 7.4: own KRA performance (any signed-in user; 404 without an own employee record)
    ("get", "/api/v1/performance/my/kra-months/", "logged_in"),
    ("get", "/api/v1/performance/my/kra-months/999999/", "logged_in"),
    ("get", "/api/v1/performance/my/annual/", "logged_in"),
    # Phase 7.4: organisation-wide KRA months and exports (HR, Admin; Ops Manager none - P11)
    ("get", "/api/v1/performance/months/", "kra_review_read"),
    ("get", "/api/v1/performance/months/export/csv/", "kra_review_read"),
    ("get", "/api/v1/performance/months/export/excel/", "kra_review_read"),
]


@pytest.mark.django_db
@pytest.mark.parametrize("caller", CALLERS)
@pytest.mark.parametrize(("method", "path", "level"), ENDPOINTS)
def test_r1_permission_matrix(caller, method, path, level, make_user):
    target = UserFactory(email="target@example.com")
    department = Department.objects.get(code="OPS")
    employee = Employee.objects.create(
        full_name="Target Employee", email="target.employee@example.com", department=department
    )
    category = TaskCategory.objects.get(code="OPERATIONS")
    task = Task.objects.create(
        title="Matrix task",
        department=department,
        category=category,
        created_by=target,
        assigned_to=employee,
        assigned_by=target,
        assigned_at=timezone.now(),
    )
    client = APIClient()
    if caller != ANON:
        client.force_login(make_user(caller, email="caller@example.com"))

    plan = KPIWeightVersion.objects.get(configuration="OPERATIONS_KRA", version=1)
    url = path.format(
        target=target.pk, dept=department.pk, emp=employee.pk, task=task.pk, category=category.pk,
        kpi=KPI.objects.get(code="ACCURACY").pk, plan=plan.pk, line=plan.weights.first().pk,
        rule=ScoringRule.objects.get(code="KRA_BENCHMARK", version=1).pk,
        scheme=BandScheme.objects.get(code="KRA_BANDS", version=1).pk,
    )
    response = getattr(client, method)(url, {})

    if caller in ALLOWED[level]:
        assert response.status_code not in (401, 403), response.content
    elif caller == ANON:
        assert response.status_code == 401
    else:
        assert response.status_code == 403


@pytest.mark.django_db
def test_login_is_public(api_client):
    assert api_client.post("/api/v1/auth/login/", {}).status_code == 400  # reached validation
