import { http, HttpResponse } from "msw";

import type { OverdueCase } from "../../api/types";

/** A realistic overdue case; tests override what they need. */
export function makeCase(overrides: Partial<OverdueCase> = {}): OverdueCase {
  return {
    id: 5, status: "OPEN", opened_at: "2026-10-05T05:30:00Z", opened_via: "TICK",
    task_id: 9, task_reference: "T-000009", task_title: "Map RM codes",
    employee: { id: 42, full_name: "Rahul Verma", employee_code: "CFS-042" },
    department: { id: 1, code: "OPS", name: "Operations" }, task_creator: { id: 3, email: "ops.manager@example.com" },
    task_assigned_at: "2026-10-05T04:30:00Z", priority: "HIGH", category_name: "Operations",
    sla_start_at: "2026-10-05T04:30:00Z", sla_due_at: "2026-10-05T05:30:00Z", sla_rule_code: "ONE_HOUR",
    sla_rule_name: "One hour", overdue_at: "2026-10-05T05:30:00Z", completed_at: null, overdue_minutes: 75,
    reason_category: "", explanation: "", submitted_at: null, submitted_by: null,
    cause: "", review_remark: "", reviewed_at: null, reviewed_by: null, version: 1,
    can_submit: true, can_review: false,
    ...overrides,
  };
}

export const SUBMITTED = makeCase({
  status: "REASON_SUBMITTED", reason_category: "DEPENDENCY", explanation: "Waiting for the RTA file.",
  submitted_at: "2026-10-05T06:00:00Z", submitted_by: { id: 7, email: "rahul@example.com" }, version: 2,
  can_submit: false,
});

export const REVIEWED = makeCase({
  ...SUBMITTED, status: "REVIEWED", cause: "SYSTEM", review_remark: "RTA portal outage confirmed.",
  reviewed_at: "2026-10-05T08:00:00Z", reviewed_by: { id: 3, email: "ops.manager@example.com" }, version: 3,
  can_submit: false, can_review: false,
});

export const page = (results: OverdueCase[]) => ({ count: results.length, next: null, previous: null, results });

export interface Seen { method: string; url: URL; body: unknown }

/** MSW handlers for the overdue API that record each request. */
export function overdueHandlers(options: {
  seen?: Seen[];
  list?: () => Response;
  detail?: () => Response;
  submit?: (body: unknown) => Response;
  review?: (body: unknown) => Response;
  ownEmployee?: () => Response;
}) {
  const seen = options.seen ?? [];
  const record = async (request: Request) => {
    const entry: Seen = { method: request.method, url: new URL(request.url), body: request.method === "POST" ? await request.json() : null };
    seen.push(entry);
    return entry;
  };
  return [
    http.get("*/api/v1/employees/me/", () => (options.ownEmployee ?? (() => HttpResponse.json({ id: 42, full_name: "Rahul Verma" })))()),
    http.get("*/api/v1/departments/", () => HttpResponse.json([
      { id: 1, code: "OPS", name: "Operations", is_live: true, created_at: "", updated_at: "" },
      { id: 2, code: "HR", name: "Human Resources", is_live: true, created_at: "", updated_at: "" },
    ])),
    http.get("*/api/v1/overdue-cases/", async ({ request }) => {
      await record(request);
      return (options.list ?? (() => HttpResponse.json(page([makeCase()]))))();
    }),
    http.get("*/api/v1/overdue-cases/:id/", async ({ request }) => {
      await record(request);
      return (options.detail ?? (() => HttpResponse.json(makeCase())))();
    }),
    http.post("*/api/v1/overdue-cases/:id/submit/", async ({ request }) => {
      const entry = await record(request);
      return (options.submit ?? (() => HttpResponse.json(SUBMITTED)))(entry.body);
    }),
    http.post("*/api/v1/overdue-cases/:id/review/", async ({ request }) => {
      const entry = await record(request);
      return (options.review ?? (() => HttpResponse.json(REVIEWED)))(entry.body);
    }),
  ];
}
