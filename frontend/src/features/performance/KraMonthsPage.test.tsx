import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { KraListRow, KraReviewMonth, RoleName } from "../../api/types";
import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";

const ROW: KraListRow = {
  id: 7, employee: { id: 42, employee_code: "E42", full_name: "Rahul Verma" },
  department: { id: 1, code: "OPS", name: "Operations" }, year: 2026, month: 11, status: "FINALIZED",
  provisional: false, reopen_count: 0, max_points_applicable: "8.500000", auto_total: "7.800000",
  adjustment_total: "0.000000", deduction_total: "0.000000", final_total: "7.800000",
  band: "Consistent Performer", band_ceiling: "", finalized_at: "2026-12-05T10:00:00+05:30",
};
const DETAIL: KraReviewMonth = {
  id: 7, employee: 42, year: 2026, month: 11, status: "FINALIZED", version: 9, plan_version: 3,
  cutoff_at: "2026-12-01T00:00:00+05:30", provisional: false, max_points_applicable: "8.500000",
  auto_total: "7.800000", adjustment_total: "-0.500000", deduction_total: "0.250000", final_total: "7.050000",
  band: "Needs Improvement", band_ceiling: "Needs Improvement", reopen_count: 1, reviewed_at: null,
  finalized_at: "2026-12-05T10:00:00+05:30",
  kpis: [{ kpi_id: 1, code: "ACCURACY", name: "Accuracy", weight: "3.00", not_applicable: false, na_reason: "", auto_points: "3.000000", adjustment_points: "-0.500000", deduction_points: "0.250000", final_points: "2.250000" }],
  deduction_applications: [{ id: 5, rule_code: "DELAYED_SYSTEM_UPDATE", rule_name: "Delayed system update", kind: "PERCENT_RANGE", scope: "KPI", kpi_id: 1, component_id: null, percent: "10.00", ceiling_band: "", evidence: "Ticket 42", reason: "Late update", applied_at: "2026-12-04T10:00:00+05:30", reverses: null, reversed_by: null, active: true }],
  deduction_lines: [{ scope: "KPI", rule_code: "DELAYED_SYSTEM_UPDATE", rule_name: "Delayed system update", kpi: "Accuracy", component: "", rate_pct: "10.000000", effective: true, points: "0.250000" }],
  blockers: [],
};
const page = (rows: KraListRow[]) => ({ count: rows.length, next: null, previous: null, results: rows });

function handlers(seen: URL[], options: { list?: () => Response; exportFile?: () => Response } = {}) {
  return [
    http.get("*/api/v1/performance/months/export/:format/", ({ request }) => {
      seen.push(new URL(request.url));
      return (options.exportFile ?? (() => new HttpResponse("Employee ID\r\n", {
        headers: { "Content-Type": "text/csv", "Content-Disposition": 'attachment; filename="kra-performance.csv"' },
      })))();
    }),
    http.get("*/api/v1/performance/months/", ({ request }) => {
      seen.push(new URL(request.url));
      return (options.list ?? (() => HttpResponse.json(page([ROW]))))();
    }),
    http.get("*/api/v1/performance/months/:id/", () => HttpResponse.json(DETAIL)),
    http.get("*/api/v1/departments/", () => HttpResponse.json([{ id: 1, code: "OPS", name: "Operations", is_live: true, created_at: "", updated_at: "" }])),
    http.get("*/api/v1/employees/", () => HttpResponse.json(page([]))),
    http.get("*/api/v1/performance/band-schemes/", () => HttpResponse.json([{ bands: [{ name: "High Performer" }, { name: "Consistent Performer" }] }])),
  ];
}
const listCalls = (seen: URL[]) => seen.filter((u) => u.pathname === "/api/v1/performance/months/");
const params = (url: URL) => Object.fromEntries(url.searchParams.entries());

async function choose(label: string, option: string) {
  await userEvent.click(screen.getByRole("combobox", { name: label }));
  await userEvent.click(await screen.findByRole("option", { name: option }));
}

beforeEach(() => {
  Object.assign(URL, { createObjectURL: vi.fn(() => "blob:kra"), revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
});
afterEach(() => vi.restoreAllMocks());

describe("KRA performance for HR / Admin (Phase 7.4)", () => {
  for (const role of ["HR", "Admin"] as RoleName[]) {
    it(`lists KRA months for ${role} with the department recorded at calculation`, async () => {
      server.use(signedInAs(role), ...handlers([]));
      renderApp("/performance/months");
      const table = await screen.findByRole("table", { name: "KRA months" });
      expect(await within(table).findByText("Rahul Verma")).toBeInTheDocument();
      expect(within(table).getByText("OPS")).toBeInTheDocument();
      expect(within(table).getByText("7.80 / 8.50")).toBeInTheDocument();
    });
  }

  for (const role of ["Employee", "Operations Manager"] as RoleName[]) {
    it(`keeps ${role} out (P11)`, async () => {
      server.use(signedInAs(role), ...handlers([]));
      renderApp("/performance/months");
      expect(await screen.findByRole("heading", { name: /access/i })).toBeInTheDocument();
      expect(screen.queryByRole("table", { name: "KRA months" })).toBeNull();
    });
  }

  it("sends the filters and exports every matching month with them (never the page)", async () => {
    const seen: URL[] = [];
    server.use(signedInAs("HR"), ...handlers(seen));
    renderApp("/performance/months");
    await screen.findByRole("table", { name: "KRA months" });
    await choose("Status", "Finalized");
    await choose("Department", "OPS");
    await choose("Band", "High Performer");
    await choose("Provisional", "Month closed");
    await waitFor(() => expect(params(listCalls(seen).at(-1) as URL)).toEqual({
      status: "FINALIZED", department: "1", band: "High Performer", provisional: "false", page: "1",
    }));
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    expect(await screen.findByText("Downloaded kra-performance.csv.")).toBeInTheDocument();
    const csv = seen.filter((u) => u.pathname === "/api/v1/performance/months/export/csv/");
    expect(csv.map(params)).toEqual([{ status: "FINALIZED", department: "1", band: "High Performer", provisional: "false" }]);
    await userEvent.click(screen.getByRole("button", { name: "Export Excel" }));
    await waitFor(() => expect(seen.some((u) => u.pathname === "/api/v1/performance/months/export/excel/")).toBe(true));
  });

  it("shows an API error from the list and from an export", async () => {
    server.use(signedInAs("HR"), ...handlers([], {
      list: () => HttpResponse.json({ code: "validation_error", message: "Some fields are not valid.", fields: { band: ["Unknown band."] } }, { status: 400 }),
      exportFile: () => HttpResponse.json({ code: "permission_denied", message: "You do not have permission to do this.", fields: {} }, { status: 403 }),
    }));
    renderApp("/performance/months");
    expect(await screen.findByText("Some fields are not valid.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    expect(await screen.findByText("You do not have permission to do this.")).toBeInTheDocument();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it("says so for a month id that cannot exist", async () => {
    server.use(signedInAs("HR"), ...handlers([]));
    renderApp("/performance/months/abc");
    expect(await screen.findByText("This KRA month does not exist.")).toBeInTheDocument();
  });

  it("opens a read-only month with its points, deduction records and per-rule points", async () => {
    server.use(signedInAs("HR"), ...handlers([]));
    renderApp("/performance/months/7");
    expect(await screen.findByRole("heading", { name: "KRA month · November 2026" })).toBeInTheDocument();
    const kpis = screen.getByRole("table", { name: "KPI points" });
    expect(within(kpis).getByText("2.25")).toBeInTheDocument();
    const records = screen.getByRole("table", { name: "Deduction records" });
    expect(within(records).getByText("Ticket 42")).toBeInTheDocument(); // HR / Admin only
    expect(within(screen.getByRole("table", { name: "Points taken per rule" })).getByText("0.25")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /finalize|reopen|submit/i })).toBeNull();
  });
});
