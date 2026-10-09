"""Permission codes owned by the recurring app (granted to roles in apps/accounts/roles.py)."""

VIEW_ALL_RESPONSIBILITIES = "recurring.view_all_responsibilities"  # HR, Admin
MANAGE_TEAM_RESPONSIBILITIES = "recurring.manage_team_responsibilities"  # Ops Manager, own dept
MANAGE_ALL_RESPONSIBILITIES = "recurring.manage_all_responsibilities"  # HR (Phase A), Admin
# Admin's global schedule authority (incl. past start dates). Since Phase A, whoever manages a
# responsibility also manages its schedules (services.can_manage_schedule).
MANAGE_SCHEDULES = "recurring.manage_schedules"  # Admin
