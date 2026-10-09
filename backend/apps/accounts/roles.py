"""Role names and Phase 1 permission codes: the single source for code and tests.

The latest role-seed migration (currently 0010) keeps a frozen copy of ROLE_PERMISSIONS;
a test checks they match.
Changing a role's permissions needs a new migration and business approval.
"""

EMPLOYEE = "Employee"
OPERATIONS_MANAGER = "Operations Manager"
HR = "HR"
ADMIN = "Admin"

ROLE_NAMES = (EMPLOYEE, OPERATIONS_MANAGER, HR, ADMIN)

PERM_MANAGE_USERS = "accounts.manage_users"
PERM_VIEW_AUDIT_LOG = "audit.view_audit_log"

# Phase 2 org permissions (approved Q7/Q8; codes live in apps/org/perms.py).
PERM_VIEW_ALL_EMPLOYEES = "org.view_all_employees"
PERM_VIEW_TEAM_EMPLOYEES = "org.view_team_employees"
PERM_MANAGE_EMPLOYEES = "org.manage_employees"
PERM_LINK_EMPLOYEE_LOGIN = "org.link_employee_login"
PERM_MANAGE_DEPARTMENTS = "org.manage_departments"

# Phase 3 task permissions (codes live in apps/tasks/perms.py). tasks.assign was granted to
# no role in Phase 3; the approved Phase 4 decision grants it to every task-creating role.
PERM_CREATE_TASK = "tasks.create_task"
PERM_ASSIGN_TASK = "tasks.assign"
PERM_VIEW_ALL_TASKS = "tasks.view_all_tasks"
PERM_VIEW_TEAM_TASKS = "tasks.view_team_tasks"
PERM_MANAGE_TEAM_TASKS = "tasks.manage_team_tasks"
PERM_MANAGE_ALL_TASKS = "tasks.manage_all_tasks"

# Phase 4 task assignment & hand-off.
PERM_EDIT_ALL_TASKS = "tasks.edit_all_tasks"
PERM_DELETE_TASK = "tasks.delete_task"
PERM_MANAGE_TASK_CATEGORIES = "tasks.manage_task_categories"

# Phase 5 responsibilities, recurring schedules and the Company Calendar.
PERM_VIEW_ALL_RESPONSIBILITIES = "recurring.view_all_responsibilities"
PERM_MANAGE_TEAM_RESPONSIBILITIES = "recurring.manage_team_responsibilities"
PERM_MANAGE_ALL_RESPONSIBILITIES = "recurring.manage_all_responsibilities"
PERM_MANAGE_SCHEDULES = "recurring.manage_schedules"
PERM_MANAGE_COMPANY_CALENDAR = "calendars.manage_company_calendar"

# Phase 6 SLA foundation: supersede SLA rules, set company work_end / login fallback time.
PERM_MANAGE_SLA_RULES = "sla.manage_sla_rules"

# Phase 9 overdue cases (approved Q4). Employees answer their own cases without a permission.
PERM_VIEW_ALL_OVERDUE_CASES = "overdue.view_all_cases"
PERM_REVIEW_TEAM_OVERDUE_CASES = "overdue.review_team_cases"
PERM_REVIEW_ALL_OVERDUE_CASES = "overdue.review_all_cases"

# Phase 7.1 performance (codes live in apps/performance/perms.py; approved P11: HR owns the
# monthly review, Admin approves configuration; the Operations Manager has none of these).
PERM_CONFIGURE_KPIS = "performance.configure_kpis"
PERM_APPROVE_KPI_CONFIG = "performance.approve_kpi_config"
PERM_MANAGE_PERFORMANCE = "performance.manage_performance"
PERM_FINALIZE_PERFORMANCE = "performance.finalize_performance"
PERM_REOPEN_PERFORMANCE = "performance.reopen_performance"

ROLE_PERMISSIONS = {
    # Phase 4: every task-creating role may assign across users and departments.
    EMPLOYEE: (PERM_CREATE_TASK, PERM_ASSIGN_TASK),
    # Team-scoped audit access (approved choice C) arrives in the next Phase 2 step.
    OPERATIONS_MANAGER: (
        PERM_VIEW_TEAM_EMPLOYEES,
        PERM_CREATE_TASK,
        PERM_VIEW_TEAM_TASKS,
        PERM_MANAGE_TEAM_TASKS,
        PERM_ASSIGN_TASK,
        PERM_MANAGE_TEAM_RESPONSIBILITIES,
        PERM_REVIEW_TEAM_OVERDUE_CASES,
    ),
    # Phase 4: HR views, creates, edits, reassigns and deletes any task; it does NOT cancel,
    # block/unblock or verify (those stay with the manage_*_tasks permissions).
    HR: (
        PERM_VIEW_AUDIT_LOG,
        PERM_VIEW_ALL_EMPLOYEES,
        PERM_MANAGE_EMPLOYEES,
        PERM_VIEW_ALL_TASKS,
        PERM_CREATE_TASK,
        PERM_ASSIGN_TASK,
        PERM_EDIT_ALL_TASKS,
        PERM_DELETE_TASK,
        PERM_VIEW_ALL_RESPONSIBILITIES,
        PERM_MANAGE_ALL_RESPONSIBILITIES,  # Phase A (D1): HR manages organisation-wide
        PERM_VIEW_ALL_OVERDUE_CASES,
        PERM_REVIEW_ALL_OVERDUE_CASES,
        PERM_CONFIGURE_KPIS,
        PERM_MANAGE_PERFORMANCE,
        PERM_FINALIZE_PERFORMANCE,
        PERM_REOPEN_PERFORMANCE,
    ),
    ADMIN: (
        PERM_MANAGE_USERS,
        PERM_VIEW_AUDIT_LOG,
        PERM_VIEW_ALL_EMPLOYEES,
        PERM_MANAGE_EMPLOYEES,
        PERM_LINK_EMPLOYEE_LOGIN,
        PERM_MANAGE_DEPARTMENTS,
        PERM_CREATE_TASK,
        PERM_VIEW_ALL_TASKS,
        PERM_MANAGE_ALL_TASKS,
        PERM_MANAGE_SLA_RULES,
        PERM_DELETE_TASK,
        PERM_MANAGE_TASK_CATEGORIES,
        PERM_VIEW_ALL_RESPONSIBILITIES,
        PERM_MANAGE_ALL_RESPONSIBILITIES,
        PERM_MANAGE_SCHEDULES,
        PERM_MANAGE_COMPANY_CALENDAR,
        PERM_VIEW_ALL_OVERDUE_CASES,
        PERM_REVIEW_ALL_OVERDUE_CASES,
        PERM_APPROVE_KPI_CONFIG,
        PERM_REOPEN_PERFORMANCE,
    ),
}
