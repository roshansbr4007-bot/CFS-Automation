"""Permission codes owned by the tasks app (granted to roles in apps/accounts/roles.py)."""

CREATE_TASK = "tasks.create_task"
ASSIGN = "tasks.assign"  # assign to any active employee, any department (Phase 4: all roles)
VIEW_ALL_TASKS = "tasks.view_all_tasks"
VIEW_TEAM_TASKS = "tasks.view_team_tasks"
MANAGE_TEAM_TASKS = "tasks.manage_team_tasks"
MANAGE_ALL_TASKS = "tasks.manage_all_tasks"
EDIT_ALL_TASKS = "tasks.edit_all_tasks"  # HR: edit / reassign any task (no cancel, block, verify)
DELETE_TASK = "tasks.delete_task"  # HR, Admin: physical delete
MANAGE_TASK_CATEGORIES = "tasks.manage_task_categories"  # Admin
