"""Performance permission codes (Phase 7.1). Grants: apps/accounts/roles.py."""

CONFIGURE_KPIS = "performance.configure_kpis"  # HR: drafts, plan defaults, employee overrides
APPROVE_KPI_CONFIG = "performance.approve_kpi_config"  # Admin: activate / retire configuration
MANAGE_PERFORMANCE = "performance.manage_performance"  # HR: monthly review (Phase 7.2/7.3)
FINALIZE_PERFORMANCE = "performance.finalize_performance"  # HR (Phase 7.3)
REOPEN_PERFORMANCE = "performance.reopen_performance"  # HR, Admin (Phase 7.3)

# Who may read the KRA configuration.
CONFIG_READERS = (CONFIGURE_KPIS, APPROVE_KPI_CONFIG)
# Who may ask which plan applies to an employee.
RESOLUTION_READERS = (CONFIGURE_KPIS, APPROVE_KPI_CONFIG, MANAGE_PERFORMANCE)
# Phase 7.3: who may read KRA months, blockers, leave / task exceptions and annual figures
# (HR, and Admin read-only). Writes: MANAGE_PERFORMANCE, finalize: FINALIZE_PERFORMANCE,
# reopen: REOPEN_PERFORMANCE.
REVIEW_READERS = (MANAGE_PERFORMANCE, FINALIZE_PERFORMANCE, REOPEN_PERFORMANCE)
