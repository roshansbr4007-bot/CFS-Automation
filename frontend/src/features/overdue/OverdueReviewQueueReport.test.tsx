import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";
import { REVIEWED, SUBMITTED, overdueHandlers, page, type Seen } from "./testData";

const queued = { ...SUBMITTED, can_review: true };
const lastParams = (seen: Seen[]) => Object.fromEntries(seen[seen.length - 1].url.searchParams.entries());

async function choose(label: string, option: string) {
  await userEvent.click(screen.getByRole("combobox", { name: label }));
  await userEvent.click(await screen.findByRole("option", { name: option }));
}

/** Report + export endpoints (registered before the case handlers: ":id" would match "reports"). */
function reportHandlers(seen: Seen[], options: { report?: () => Response; exportFile?: () => Response } = {}) {
  const record = (request: Request) => seen.push({ method: request.method, url: new URL(request.url), body: null });
  return [
    http.get("*/api/v1/overdue-cases/reports/export/:format/", ({ request }) => {
      record(request);
      return (options.exportFile ?? (() => new HttpResponse("Case ID\r\n", {
        headers: { "Content-Type": "text/csv", "Content-Disposition": 'attachment; filename="overdue-cases.csv"' },
      })))();
    }),
    http.get("*/api/v1/overdue-cases/reports/", ({ request }) => {
      record(request);
      return (options.report ?? (() => HttpResponse.json(page([REVIEWED, { ...queued, id: 6 }]))))();
    }),
  ];
}

async function openReport() {
  await userEvent.click(await screen.findByRole("tab", { name: "All cases (report)" }));
  return screen.findByRole("table", { name: "Overdue cases report" });
}

beforeEach(() => {
  Object.assign(URL, { createObjectURL: vi.fn(() => "blob:overdue"), revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
});
afterEach(() => vi.restoreAllMocks());

describe("Review queue — filters and pagination (Stage 5.3)", () => {
  it("clears the filters and returns to the unfiltered queue", async () => {
    const seen: Seen[] = [];
    server.use(signedInAs("Operations Manager"), ...overdueHandlers({ seen, list: () => HttpResponse.json(page([queued])) }));
    renderApp("/overdue-cases/review");
    await screen.findByRole("table", { name: "Overdue review queue" });
    await choose("Priority", "High");
    fireEvent.change(screen.getByLabelText("Opened to"), { target: { value: "2026-10-31" } });
    await waitFor(() => expect(lastParams(seen)).toEqual({ priority: "HIGH", date_to: "2026-10-31", page: "1", reviewable: "1" }));
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(lastParams(seen)).toEqual({ page: "1", reviewable: "1" }));
  });

  it("pages through the queue and resets the page on a filter change", async () => {
    const errors = vi.spyOn(console, "error");
    const seen: Seen[] = [];
    const many = { count: 40, next: "http://x/?page=2", previous: null, results: [queued] };
    server.use(signedInAs("HR"), ...overdueHandlers({ seen, list: () => HttpResponse.json(many) }));
    renderApp("/overdue-cases/review");
    await screen.findByRole("table", { name: "Overdue review queue" });
    await userEvent.click(await screen.findByRole("button", { name: "Go to next page" }));
    await waitFor(() => expect(lastParams(seen)).toMatchObject({ page: "2", reviewable: "1" }));
    await choose("Employee reason", "Client");
    await waitFor(() => expect(lastParams(seen)).toMatchObject({ page: "1", reason_category: "CLIENT" }));
    expect(errors.mock.calls.flat().join(" ")).not.toMatch(/out of range/);
  });

  it("shows loading, filtered-empty and error states", async () => {
    let mode: "wait" | "empty" | "error" = "wait";
    server.use(
      signedInAs("Admin"),
      http.get("*/api/v1/overdue-cases/", async () => {
        if (mode === "wait") { await delay("infinite"); }
        if (mode === "error") return HttpResponse.json({ code: "error", message: "Queue is unavailable.", fields: {} }, { status: 500 });
        return HttpResponse.json(page([]));
      }),
      ...overdueHandlers({}),
    );
    renderApp("/overdue-cases/review");
    // Wait for the page itself: before auth resolves, ProtectedRoute shows its own "Loading" spinner.
    await screen.findByRole("region", { name: "Filters" });
    expect(screen.getByRole("progressbar")).toBeInTheDocument(); // the table's loading bar
    expect(screen.queryByText("No overdue cases are waiting for your review.")).not.toBeInTheDocument();
    mode = "empty";
    await choose("Priority", "Urgent");
    expect(await screen.findByText("No overdue cases match the selected filters.")).toBeInTheDocument();
    mode = "error";
    await choose("Priority", "Low");
    expect(await screen.findByText("Queue is unavailable.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

describe("Overdue report and export (Stage 5.3)", () => {
  it("an Operations Manager sees the scoped report but no export controls", async () => {
    const seen: Seen[] = [];
    server.use(signedInAs("Operations Manager"), ...reportHandlers(seen), ...overdueHandlers({}));
    renderApp("/overdue-cases/review");
    const table = await openReport();
    expect(await within(table).findByText("System")).toBeInTheDocument(); // reviewer's cause column
    expect(seen.some((s) => s.url.pathname === "/api/v1/overdue-cases/reports/")).toBe(true);
    const exportArea = screen.getByRole("region", { name: "Export" });
    expect(within(exportArea).queryByRole("button")).not.toBeInTheDocument();
    expect(within(exportArea).getByText("Exports are available to HR and Admin.")).toBeInTheDocument();
  });

  it.each(["HR", "Admin"] as const)("%s sees the export controls", async (role) => {
    server.use(signedInAs(role), ...reportHandlers([]), ...overdueHandlers({}));
    renderApp("/overdue-cases/review");
    await openReport();
    expect(screen.getByRole("button", { name: "Export CSV" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Export Excel" })).toBeEnabled();
  });

  it("sends the report filters and pages with them", async () => {
    const errors = vi.spyOn(console, "error");
    const seen: Seen[] = [];
    const many = () => HttpResponse.json({ count: 60, next: "http://x/?page=2", previous: null, results: [REVIEWED] });
    server.use(signedInAs("HR"), ...reportHandlers(seen, { report: many }), ...overdueHandlers({}));
    renderApp("/overdue-cases/review");
    await openReport();
    await choose("Status", "Reviewed");
    await choose("Reviewer's cause", "System");
    await choose("Department", "OPS");
    const reportCalls = () => seen.filter((s) => s.url.pathname === "/api/v1/overdue-cases/reports/");
    await waitFor(() => expect(lastParams(reportCalls())).toEqual({ status: "REVIEWED", cause: "SYSTEM", department: "1", page: "1" }));
    await userEvent.click(screen.getByRole("button", { name: "Go to next page" }));
    await waitFor(() => expect(lastParams(reportCalls())).toMatchObject({ page: "2", status: "REVIEWED" }));
    expect(errors.mock.calls.flat().join(" ")).not.toMatch(/out of range/);
  });

  it("exports CSV and Excel with the current filters (never the page)", async () => {
    const seen: Seen[] = [];
    server.use(signedInAs("Admin"), ...reportHandlers(seen), ...overdueHandlers({}));
    renderApp("/overdue-cases/review");
    await openReport();
    await choose("Status", "Reviewed");
    await choose("Employee reason", "Dependency");
    fireEvent.change(screen.getByLabelText("Opened from"), { target: { value: "2026-10-01" } });
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    expect(await screen.findByText("Downloaded overdue-cases.csv.")).toBeInTheDocument();
    const csv = seen.filter((s) => s.url.pathname === "/api/v1/overdue-cases/reports/export/csv/");
    expect(csv).toHaveLength(1);
    expect(Object.fromEntries(csv[0].url.searchParams.entries())).toEqual({
      status: "REVIEWED", reason_category: "DEPENDENCY", date_from: "2026-10-01",
    });
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Export Excel" }));
    await waitFor(() => expect(seen.some((s) => s.url.pathname === "/api/v1/overdue-cases/reports/export/excel/")).toBe(true));
  });

  it("surfaces an export error from the backend", async () => {
    server.use(signedInAs("HR"), ...reportHandlers([], {
      exportFile: () => HttpResponse.json({ code: "permission_denied", message: "Exporting overdue reports needs organisation-wide access.", fields: {} }, { status: 403 }),
    }), ...overdueHandlers({}));
    renderApp("/overdue-cases/review");
    await openReport();
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    expect(await screen.findByText("Exporting overdue reports needs organisation-wide access.")).toBeInTheDocument();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
});
