import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import type { CommandCenterEmployeeDetail, CommandCenterHealth, CommandCenterSummary, SlaAttention } from "../../api/types";
import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";

const counts = (total: number, completed: number, pending: number, overdue: number, late = 0) =>
  ({ total, completed, pending, overdue, completed_late: late });
const sla = { not_started: 0, on_track: 2, warning: 1, critical: 0, overdue: 1, completed_on_time: 4, completed_late: 1 };

const SUMMARY: CommandCenterSummary = {
  date: "2026-10-05", server_time: "2026-10-05T08:00:00Z",
  scheduler: [
    { job: "recurring.generate_recurring_tasks", name: "Recurring task generator", status: "HEALTHY", detail: "Running on schedule.",
      last_started_at: "2026-10-05T07:59:00Z", last_finished_at: "2026-10-05T07:59:01Z", last_success_at: "2026-10-05T07:59:01Z",
      last_summary: { generated: 3, skipped: 0 } },
    { job: "sla.evaluate_sla_clocks", name: "SLA checker", status: "NEVER_RUN", detail: "No run has been recorded yet.",
      last_started_at: null, last_finished_at: null, last_success_at: null, last_summary: {} },
  ],
  todays_occurrences: { generated: 3, skipped: 1, missed: 0, failed: 0 },
  operations: { daily_activity: counts(3, 2, 1, 1, 1), assigned_tasks: counts(5, 4, 1, 0) },
  sla: { daily_activity: sla, assigned_tasks: sla },
  overview: {
    employees: { total_active: 7, with_work: 4 },
    daily_activities: { scheduled: 3, completed: 2, pending: 0, in_progress: 1, blocked: 0, overdue: 1 },
    assigned_tasks: { total: 5, active: 1, completed: 4, pending: 1, in_progress: 0, blocked: 0, overdue: 0 },
    sla: { on_track: 2, warning: 1, critical: 3, overdue: 1 },
  },
  employees: [{ employee: { id: 10, full_name: "Sourabh Mishra", department: { id: 1, code: "OPS", name: "Operations" } },
    daily_activity: counts(3, 2, 1, 1), assigned_tasks: counts(5, 4, 1, 0), attention: ["OVERDUE", "CRITICAL"] }],
  recent_events: [{ id: 99, action: "task.completed", entity_type: "task", entity_id: "12",
    actor: { id: 3, email: "sourabh@example.com", full_name: "Sourabh Mishra" }, occurred_at: "2026-10-05T07:30:00Z" }],
};
const HEALTH: CommandCenterHealth = {
  checked_at: "2026-10-05T08:00:00Z", overall: "UNKNOWN",
  checks: [
    { name: "api", status: "HEALTHY", detail: "Answered this request." },
    { name: "database", status: "HEALTHY", detail: "Connected." },
    { name: "redis", status: "HEALTHY", detail: "Reachable." },
    { name: "celery_workers", status: "UNKNOWN", detail: "Could not check: TimeoutError" },
  ],
};

const SOURABH = { id: 10, full_name: "Sourabh Mishra", department: { id: 1, code: "OPS", name: "Operations" } };
const ATTENTION: SlaAttention = {
  date: "2026-10-05", server_time: "2026-10-05T08:00:00Z", warning: [], on_track: [], not_started: [],
  critical: [{ employee: SOURABH, task_id: 21, reference: "T-000021", title: "SIP/STP/Switch Checking — 05 Oct 2026", source: "SCHEDULED",
    status: "IN_PROGRESS", deadline: "2026-10-05T07:30:00Z", sla_state: "CRITICAL", remaining_seconds: 2700 }],
  overdue: [{ employee: SOURABH, task_id: 22, reference: "T-000022", title: "Map RM codes", source: "MANUAL",
    status: "PENDING", deadline: "2026-10-04T04:30:00Z", sla_state: "OVERDUE", remaining_seconds: null }],
};
const DETAIL: CommandCenterEmployeeDetail = {
  date: "2026-10-05", server_time: "2026-10-05T08:00:00Z", employee: SOURABH, attention: ["OVERDUE"],
  daily_activities: [{ task_id: 21, reference: "T-000021", title: "Feed Upload — 05 Oct 2026",
    responsibility: { id: 1, code: "FEED_UPLOAD", name: "Feed Upload" }, occurrence_date: "2026-10-05",
    scheduled_start: "2026-10-05T04:30:00Z", deadline: "2026-10-05T06:30:00Z", status: "COMPLETED", sla_state: "ON_TRACK",
    sla_note: null, remaining_seconds: null, completed_at: "2026-10-05T05:30:00Z", completion_result: "ON_TIME",
    is_overdue: false, assignee: { id: 10, full_name: "Sourabh Mishra" }, source: "SCHEDULED" }],
  assigned_tasks: [{ task_id: 22, reference: "T-000022", title: "Map RM codes", priority: "HIGH",
    raised_by: { id: 2, email: "ops@example.com", full_name: "Ops Manager" }, department: { id: 1, code: "OPS", name: "Operations" },
    category: { id: 3, code: "OPERATIONS", name: "Operations" }, assigned_at: "2026-10-03T04:30:00Z",
    assigned_by: { id: 2, email: "ops@example.com", full_name: "Ops Manager" }, deadline: "2026-10-04T04:30:00Z", status: "PENDING",
    sla_state: "OVERDUE", completed_at: null, completion_result: null, completed_on_day: false, is_overdue: true,
    remaining_seconds: null, source: "MANUAL" }],
};

type Calls = { summary: number; health: number; urls?: URL[] };
function handlers(calls: Calls, summary: () => Response = () => HttpResponse.json(SUMMARY), attention: () => Response = () => HttpResponse.json(ATTENTION)) {
  return [
    signedInAs("Admin"),
    http.get("*/api/v1/command-center/summary/", ({ request }) => { calls.summary += 1; calls.urls?.push(new URL(request.url)); return summary(); }),
    http.get("*/api/v1/command-center/health/", () => { calls.health += 1; return HttpResponse.json(HEALTH); }),
    http.get("*/api/v1/command-center/sla-attention/", ({ request }) => { calls.urls?.push(new URL(request.url)); return attention(); }),
    http.get("*/api/v1/command-center/employees/10/", ({ request }) => { calls.urls?.push(new URL(request.url)); return HttpResponse.json(DETAIL); }),
    http.get("*/api/v1/departments/", () => HttpResponse.json([{ id: 1, code: "OPS", name: "Operations", is_live: true, created_at: "", updated_at: "" }])),
    http.get("*/api/v1/employees/", () => HttpResponse.json({ count: 1, next: null, previous: null, results: [{ ...SOURABH, email: "", is_active: true }] })),
  ];
}

describe("Command center (Admin = Boss)", () => {
  it("shows health, scheduler, operations, SLA, employees and events from the backend", async () => {
    const calls = { summary: 0, health: 0 };
    server.use(...handlers(calls));
    renderApp("/admin/command-center");
    const health = await screen.findByRole("table", { name: "System health" });
    expect(await within(health).findByText("Celery workers")).toBeInTheDocument();
    expect(within(health).getByText("Unknown")).toBeInTheDocument(); // never shown as healthy
    const scheduler = await screen.findByRole("table", { name: "Scheduler health" });
    expect(within(scheduler).getByText("Never run")).toBeInTheDocument();
    expect(within(scheduler).getByText("generated: 3, skipped: 0")).toBeInTheDocument();
    const ops = screen.getByRole("region", { name: "Today's operations" });
    expect(within(ops).getByText("Scheduled overdue")).toBeInTheDocument();
    expect(within(ops).getByText("Manual total")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "SLA snapshot" })).getAllByText("Completed on time")).toHaveLength(2);
    const employees = screen.getByRole("table", { name: "Employee summary" });
    expect(within(employees).getByText("Sourabh Mishra")).toBeInTheDocument();
    const events = screen.getByRole("table", { name: "Recent events" });
    expect(within(events).getByText("task.completed")).toBeInTheDocument();
    expect(within(events).getByText("task #12")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(calls).toEqual({ summary: 2, health: 2 }));
  });

  it("links only to existing pages", async () => {
    server.use(...handlers({ summary: 0, health: 0 }));
    renderApp("/admin/command-center");
    const actions = await screen.findByRole("region", { name: "Quick actions" });
    const links = within(actions).getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(links).toEqual(["/admin/operations", "/tasks", "/responsibilities", "/schedules", "/employees", "/admin/audit", "/admin/calendar"]);
  });

  it("shows loading states while the backend answers", async () => {
    server.use(
      ...handlers({ summary: 0, health: 0 }),
      http.get("*/api/v1/command-center/summary/", async () => { await delay("infinite"); return HttpResponse.json(SUMMARY); }),
      http.get("*/api/v1/command-center/health/", async () => { await delay("infinite"); return HttpResponse.json(HEALTH); }),
      http.get("*/api/v1/command-center/sla-attention/", async () => { await delay("infinite"); return HttpResponse.json(ATTENTION); }),
    );
    renderApp("/admin/command-center");
    expect(await screen.findByText("Loading command center…")).toBeInTheDocument();
    expect(screen.getByText("Checking system health…")).toBeInTheDocument();
    expect(screen.queryByText("Active employees")).not.toBeInTheDocument(); // no fake zeros while loading
  });

  it("reports an API error and still shows health", async () => {
    server.use(...handlers({ summary: 0, health: 0 },
      () => HttpResponse.json({ code: "error", message: "Summary unavailable.", fields: {} }, { status: 500 })));
    renderApp("/admin/command-center");
    expect(await screen.findByText("Summary unavailable.")).toBeInTheDocument();
    expect(await screen.findByRole("table", { name: "System health" })).toBeInTheDocument();
  });

  it("is not available to other roles", async () => {
    server.use(signedInAs("HR"));
    renderApp("/admin/command-center");
    expect(await screen.findByRole("heading", { name: /don't have access/i })).toBeInTheDocument();
  });

  // --- Phase 6B ---

  it("shows the overview, the SLA attention groups and each employee's attention", async () => {
    server.use(...handlers({ summary: 0, health: 0 }));
    renderApp("/admin/command-center");
    const overview = await screen.findByRole("region", { name: "Today overview" });
    expect(within(overview).getByText("Active employees")).toBeInTheDocument();
    expect(within(overview).getByText("7")).toBeInTheDocument();
    expect(within(overview).getByText("Critical SLA")).toBeInTheDocument();
    const critical = await screen.findByRole("table", { name: "SLA attention: Critical" });
    expect(await within(critical).findByText("SIP/STP/Switch Checking — 05 Oct 2026")).toBeInTheDocument();
    expect(within(critical).getByText("Daily activity")).toBeInTheDocument();
    expect(within(critical).getByText("45m remaining")).toBeInTheDocument();
    const overdue = screen.getByRole("table", { name: "SLA attention: Overdue" });
    expect(within(overdue).getByText("Assigned task")).toBeInTheDocument();
    const warning = screen.getByRole("table", { name: "SLA attention: Warning" });
    expect(within(warning).getByText("No active items for the selected filters.")).toBeInTheDocument();
    const employees = screen.getByRole("table", { name: "Employee summary" });
    // "Overdue" is also a column header, so the attention chip makes it appear at least twice.
    expect(within(employees).getAllByText("Overdue").length).toBeGreaterThan(1);
    expect(within(employees).getByText("Critical")).toBeInTheDocument();
  });

  it("sends filters to the server instead of filtering in the browser", async () => {
    const calls: Calls = { summary: 0, health: 0, urls: [] };
    server.use(...handlers(calls));
    renderApp("/admin/command-center");
    await screen.findByRole("region", { name: "Today overview" });
    await userEvent.click(screen.getByRole("combobox", { name: "Source" }));
    await userEvent.click(await screen.findByRole("option", { name: "Assigned tasks" }));
    await userEvent.click(screen.getByRole("combobox", { name: "SLA state" }));
    await userEvent.click(await screen.findByRole("option", { name: "Overdue" }));
    await waitFor(() => {
      const last = (path: string) => calls.urls!.filter((u) => u.pathname.endsWith(path)).at(-1);
      for (const path of ["/summary/", "/sla-attention/"]) {
        expect(last(path)?.searchParams.get("source")).toBe("MANUAL");
        expect(last(path)?.searchParams.get("sla_state")).toBe("OVERDUE");
      }
    });
  });

  it("opens a read-only drill-down that keeps the two kinds of work apart", async () => {
    const calls: Calls = { summary: 0, health: 0, urls: [] };
    server.use(...handlers(calls));
    renderApp("/admin/command-center");
    const employees = await screen.findByRole("table", { name: "Employee summary" });
    await userEvent.click(within(employees).getByRole("button", { name: "View" }));
    const daily = await screen.findByRole("table", { name: "Employee daily activities" });
    expect(await within(daily).findByText("Feed Upload")).toBeInTheDocument();
    expect(within(daily).getByText("Daily activity")).toBeInTheDocument();
    const tasks = screen.getByRole("table", { name: "Employee assigned tasks" });
    expect(within(tasks).getByText("Map RM codes")).toBeInTheDocument();
    expect(within(tasks).getByText("Assigned task")).toBeInTheDocument();
    expect(within(tasks).getByText("High")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /edit|complete|reassign|delete/i })).not.toBeInTheDocument();
    expect(calls.urls!.some((u) => u.pathname.endsWith("/command-center/employees/10/"))).toBe(true);
  });

  it("shows an empty state, and a retryable error for SLA attention", async () => {
    let fail = true;
    server.use(...handlers({ summary: 0, health: 0 }, () => HttpResponse.json({ ...SUMMARY, employees: [] }),
      () => (fail ? HttpResponse.json({ code: "error", message: "x", fields: {} }, { status: 500 }) : HttpResponse.json({ ...ATTENTION, critical: [], overdue: [] }))));
    renderApp("/admin/command-center");
    expect(await screen.findByText("No active employees.")).toBeInTheDocument();
    const error = await screen.findByText("Unable to load SLA attention.");
    fail = false;
    await userEvent.click(within(error.closest("[role=alert]") as HTMLElement).getByRole("button", { name: "Retry" }));
    const critical = await screen.findByRole("table", { name: "SLA attention: Critical" });
    expect(within(critical).getByText("No active items for the selected filters.")).toBeInTheDocument();
  });

  it("refreshes the summary and SLA attention every 60 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const calls: Calls = { summary: 0, health: 0, urls: [] };
      server.use(...handlers(calls));
      renderApp("/admin/command-center");
      await screen.findByRole("region", { name: "Today overview" });
      const attentionCalls = () => calls.urls!.filter((u) => u.pathname.endsWith("/sla-attention/")).length;
      const before = { summary: calls.summary, attention: attentionCalls() };
      await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
      await waitFor(() => expect(calls.summary).toBeGreaterThan(before.summary));
      expect(attentionCalls()).toBeGreaterThan(before.attention);
      expect(calls.health).toBe(1); // health stays manual, as in Phase 6A
    } finally {
      vi.useRealTimers();
    }
  });
});
