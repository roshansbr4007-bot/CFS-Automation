import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

import type { Me, RoleName } from "../api/types";

// Mirrors the backend role grants (apps/accounts/roles.py, migration 0010).
const PERMS: Record<RoleName, string[]> = {
  Employee: ["tasks.assign", "tasks.create_task"],
  "Operations Manager": [
    "org.view_team_employees", "recurring.manage_team_responsibilities", "tasks.assign",
    "tasks.create_task", "tasks.manage_team_tasks", "tasks.view_team_tasks",
    "overdue.review_team_cases",
  ],
  HR: [
    "audit.view_audit_log", "org.manage_employees", "org.view_all_employees",
    "recurring.view_all_responsibilities", "recurring.manage_all_responsibilities", "tasks.assign", "tasks.create_task", "tasks.delete_task",
    "tasks.edit_all_tasks", "tasks.view_all_tasks",
    "overdue.view_all_cases", "overdue.review_all_cases",
    "performance.configure_kpis", "performance.manage_performance",
    "performance.finalize_performance", "performance.reopen_performance",
  ],
  Admin: [
    "accounts.manage_users", "audit.view_audit_log", "org.link_employee_login", "org.manage_departments",
    "calendars.manage_company_calendar", "org.manage_employees", "org.view_all_employees",
    "recurring.manage_all_responsibilities", "recurring.manage_schedules",
    "recurring.view_all_responsibilities", "sla.manage_sla_rules", "tasks.create_task",
    "tasks.delete_task", "tasks.manage_all_tasks", "tasks.manage_task_categories",
    "tasks.view_all_tasks",
    "overdue.view_all_cases", "overdue.review_all_cases",
    "performance.approve_kpi_config", "performance.reopen_performance",
  ],
};

export function makeMe(role: RoleName, overrides: Partial<Me> = {}): Me {
  return {
    id: 1,
    email: "asha@example.com",
    first_name: "Asha",
    last_name: "Verma",
    full_name: "Asha Verma",
    roles: [role],
    is_active: true,
    last_login: null,
    date_joined: "2026-10-05T10:00:00+05:30",
    permissions: PERMS[role],
    ...overrides,
  };
}

export const anonymous = () =>
  HttpResponse.json(
    { code: "not_authenticated", message: "Sign in to continue.", fields: {} },
    { status: 401 },
  );

export function signedInAs(role: RoleName) {
  return http.get("*/api/v1/auth/me/", () => HttpResponse.json(makeMe(role)));
}

export const server = setupServer(
  http.get("*/api/v1/auth/me/", anonymous),
  http.get("*/api/v1/auth/csrf/", () => HttpResponse.json({ detail: "CSRF cookie set." })),
  http.get("*/api/v1/users/", () =>
    HttpResponse.json({ count: 0, next: null, previous: null, results: [] }),
  ),
  http.get("*/api/v1/audit-log/", () =>
    HttpResponse.json({ count: 0, next: null, previous: null, results: [] }),
  ),
  // Phase 5.1: the Received view always shows today's generated daily activities.
  http.get("*/api/v1/tasks/daily-activities/", () =>
    HttpResponse.json({ date: "2026-10-05", server_time: "2026-10-05T05:30:00Z", activities: [] }),
  ),
  // The notification bell in the app shell asks for these on every page.
  http.get("*/api/v1/notifications/unread-count/", () => HttpResponse.json({ unread: 0 })),
  http.get("*/api/v1/notifications/", () =>
    HttpResponse.json({ count: 0, next: null, previous: null, results: [] }),
  ),
  // Phase 7.4: Home asks for my KRA months; by default the login has no employee record.
  http.get("*/api/v1/performance/my/kra-months/", () =>
    HttpResponse.json(
      { code: "no_employee_record", message: "You do not have an employee record yet.", fields: {} },
      { status: 404 },
    ),
  ),
);
