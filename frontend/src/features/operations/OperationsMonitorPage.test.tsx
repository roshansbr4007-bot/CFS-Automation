import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";
import { makeActivity } from "../tasks/testData";
import { shiftDate } from "./OperationsMonitorPage";

const OPS = { id: 1, code: "OPS", name: "Operations" };
const RAHUL = { id: 10, full_name: "Rahul Sharma", department: OPS };
const counts = (total: number, completed: number, pending: number, overdue: number) => ({ total, completed, pending, overdue, completed_late: 0 });

function monitoringHandlers(seen: URL[]) {
  return [
    signedInAs("Admin"),
    http.get("*/api/v1/operations/daily-summary/", ({ request }) => {
      const url = new URL(request.url);
      seen.push(url);
      return HttpResponse.json({
        date: url.searchParams.get("date") ?? "2026-10-05", server_time: "2026-10-05T08:00:00Z",
        employees: [{ employee: RAHUL, daily_activity: counts(3, 2, 1, 1), assigned_tasks: counts(5, 4, 1, 0) }],
      });
    }),
    http.get("*/api/v1/departments/", () => HttpResponse.json([{ id: 1, code: "OPS", name: "Operations", is_live: true, created_at: "", updated_at: "" }])),
    http.get("*/api/v1/employees/", () => HttpResponse.json({ count: 1, next: null, previous: null, results: [{ ...RAHUL, email: "", is_active: true }] })),
    http.get("*/api/v1/operations/employees/10/daily-activities/", ({ request }) => {
      seen.push(new URL(request.url));
      return HttpResponse.json({
        date: "2026-10-05", server_time: "2026-10-05T08:00:00Z", employee: RAHUL, counts: counts(1, 1, 0, 0),
        activities: [makeActivity({ status: "COMPLETED", completed_at: "2026-10-05T06:05:00Z", completion_result: "ON_TIME", remaining_seconds: null })],
      });
    }),
    http.get("*/api/v1/operations/employees/10/assigned-tasks/", ({ request }) => {
      seen.push(new URL(request.url));
      return HttpResponse.json({
        date: "2026-10-05", server_time: "2026-10-05T08:00:00Z", employee: RAHUL, counts: counts(1, 0, 1, 1),
        tasks: [{
          task_id: 7, reference: "T-000007", title: "Map RM codes", priority: "HIGH",
          raised_by: { id: 5, email: "hr.one@example.com", full_name: "Priya HR" },
          department: { id: 1, code: "OPS", name: "Operations" }, category: { id: 3, code: "OPERATIONS", name: "Operations" },
          assigned_at: "2026-10-03T04:30:00Z",
          assigned_by: { id: 2, email: "ops.manager@example.com", full_name: "Ops Manager" }, deadline: "2026-10-04T04:30:00Z",
          status: "PENDING", sla_state: "OVERDUE", completed_at: null, completion_result: null, completed_on_day: false, is_overdue: true,
        }],
      });
    }),
  ];
}

/** The Operations Manager's team view: only the team endpoints; the department comes from the server. */
function teamHandlers(seen: URL[]) {
  const detail = { date: "2026-10-05", server_time: "2026-10-05T08:00:00Z", employee: RAHUL };
  return [
    signedInAs("Operations Manager"),
    http.get("*/api/v1/operations/team/daily-summary/", ({ request }) => {
      const url = new URL(request.url);
      seen.push(url);
      return HttpResponse.json({
        date: url.searchParams.get("date") ?? "2026-10-05", server_time: "2026-10-05T08:00:00Z", department: OPS,
        employees: [{ employee: RAHUL, daily_activity: counts(1, 0, 1, 1), assigned_tasks: counts(2, 1, 1, 0) }],
      });
    }),
    http.get("*/api/v1/operations/team/employees/10/daily-activities/", ({ request }) => {
      seen.push(new URL(request.url));
      return HttpResponse.json({ ...detail, counts: counts(1, 1, 0, 0), activities: [makeActivity({ status: "COMPLETED", completed_at: "2026-10-05T06:05:00Z", completion_result: "ON_TIME", remaining_seconds: null })] });
    }),
    http.get("*/api/v1/operations/team/employees/10/assigned-tasks/", ({ request }) => {
      seen.push(new URL(request.url));
      return HttpResponse.json({ ...detail, counts: counts(0, 0, 0, 0), tasks: [] });
    }),
    // The organisation-wide endpoints must never be called for a manager.
    http.get("*/api/v1/operations/daily-summary/", ({ request }) => { seen.push(new URL(request.url)); return HttpResponse.json({}, { status: 403 }); }),
    http.get("*/api/v1/operations/employees/:id/:kind/", ({ request }) => { seen.push(new URL(request.url)); return HttpResponse.json({}, { status: 403 }); }),
    http.get("*/api/v1/employees/", () => HttpResponse.json({ count: 1, next: null, previous: null, results: [{ ...RAHUL, email: "", is_active: true }] })),
  ];
}

describe("Operations monitor (Operations Manager: own department)", () => {
  it("uses the team endpoints, shows the department and offers no department filter", async () => {
    const seen: URL[] = [];
    server.use(...teamHandlers(seen));
    renderApp("/admin/operations");
    const table = await screen.findByRole("table", { name: "Employee summary" });
    expect(await within(table).findByText("Rahul Sharma")).toBeInTheDocument();
    expect(await screen.findByText("OPS — Operations")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Department" })).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Employee" })).toBeInTheDocument();
    expect(seen[0].pathname).toBe("/api/v1/operations/team/daily-summary/");
    expect(seen[0].searchParams.get("department")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Yesterday" }));
    await waitFor(() => expect(seen.at(-1)?.searchParams.get("date")).toBe("2026-10-04"));

    await userEvent.click(within(table).getByRole("button", { name: "View" }));
    const daily = await screen.findByRole("table", { name: "Daily activities" });
    expect(await within(daily).findByText("Completed on time")).toBeInTheDocument();
    await waitFor(() => expect(seen.some((u) => u.pathname === "/api/v1/operations/team/employees/10/assigned-tasks/")).toBe(true));
    expect(seen.every((u) => u.pathname.startsWith("/api/v1/operations/team/"))).toBe(true);
  });

  it("is not available to HR", async () => {
    server.use(signedInAs("HR"));
    renderApp("/admin/operations");
    expect(await screen.findByRole("heading", { name: /don't have access/i })).toBeInTheDocument();
  });
});

describe("Operations monitor (Admin = Boss)", () => {
  it("shows daily activities and assigned tasks separately per employee, with date and detail", async () => {
    const seen: URL[] = [];
    server.use(...monitoringHandlers(seen));
    renderApp("/admin/operations");
    const table = await screen.findByRole("table", { name: "Employee summary" });
    expect(await within(table).findByText("Rahul Sharma")).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Daily overdue" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Tasks overdue" })).toBeInTheDocument();
    expect(seen[0].searchParams.get("date")).toBeNull(); // today = the server's business date

    await userEvent.click(screen.getByRole("button", { name: "Yesterday" }));
    await waitFor(() => expect(seen.at(-1)?.searchParams.get("date")).toBe("2026-10-04"));

    await userEvent.click(screen.getByRole("combobox", { name: "Department" }));
    await userEvent.click(await screen.findByRole("option", { name: "OPS" }));
    await waitFor(() => expect(seen.at(-1)?.searchParams.get("department")).toBe("1"));

    await userEvent.click(within(table).getByRole("button", { name: "View" }));
    const daily = await screen.findByRole("table", { name: "Daily activities" });
    expect(await within(daily).findByText("Completed on time")).toBeInTheDocument();
    const tasks = screen.getByRole("table", { name: "Assigned tasks" });
    expect(await within(tasks).findByText("Map RM codes")).toBeInTheDocument();
    expect(within(tasks).getByText("Ops Manager")).toBeInTheDocument(); // assigned by
    expect(within(tasks).getByText("Priya HR")).toBeInTheDocument(); // raised by (another department)
    expect(within(tasks).getByText("OPS / Operations")).toBeInTheDocument();
    expect(within(tasks).getByText("Overdue")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("combobox", { name: "SLA state" }));
    await userEvent.click(await screen.findByRole("option", { name: "Overdue" }));
    await waitFor(() => expect(seen.some((u) => u.pathname.includes("daily-activities") && u.searchParams.get("sla_state") === "OVERDUE")).toBe(true));
    await userEvent.click(screen.getByRole("combobox", { name: "Task status" }));
    await userEvent.click(await screen.findByRole("option", { name: "Overdue" }));
    await waitFor(() => expect(seen.some((u) => u.pathname.includes("assigned-tasks") && u.searchParams.get("status") === "overdue")).toBe(true));
  });

  it("is not available to employees", async () => {
    server.use(signedInAs("Employee"));
    renderApp("/admin/operations");
    expect(await screen.findByRole("heading", { name: /don't have access/i })).toBeInTheDocument();
  });

  it("shifts business dates without a timezone", () => {
    expect(shiftDate("2026-10-01", -1)).toBe("2026-09-30");
    expect(shiftDate("2026-03-01", -1)).toBe("2026-02-28");
  });
});
