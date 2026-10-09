"""Overdue-case permissions (Phase 9, approved Q4). An employee needs no permission to see and
answer their OWN cases."""

VIEW_ALL_CASES = "overdue.view_all_cases"  # HR, Admin: every case
REVIEW_TEAM_CASES = "overdue.review_team_cases"  # Operations Manager: own (task) department
REVIEW_ALL_CASES = "overdue.review_all_cases"  # HR, Admin: organisation-wide
