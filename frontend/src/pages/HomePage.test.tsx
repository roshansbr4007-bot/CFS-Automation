import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { KraMyAnnual, KraMyHistory, KraMyMonth, LegacyPerformanceRow, RoleName } from "../api/types";
import { server, signedInAs } from "../test/server";
import { renderApp } from "../test/utils";

// Fixed data (no clocks): the backend decides the states and the current month.
const HISTORY: KraMyHistory = {
  employee: { id: 42, full_name: "Rahul Verma", date_of_joining: "2025-01-10" },
  current: { year: 2026, month: 11 },
  months: [
    { id: 3, year: 2026, month: 11, state: "PROVISIONAL", final_total: "3.000000", max_points_applicable: "3.000000", band: "Performance Concern" },
    { id: 2, year: 2026, month: 10, state: "PENDING_REVIEW", final_total: null, max_points_applicable: null, band: null },
    { id: 1, year: 2026, month: 9, state: "FINALIZED", final_total: "7.800000", max_points_applicable: "8.500000", band: "Consistent Performer" },
  ],
};
const KPI = (name: string, auto: string, final: string) => ({
  name, weight: "3.00", not_applicable: false, na_label: null, achievement_pct: "100.000000",
  auto_points: auto, final_points: final,
  components: [{ label: "Feed Upload", applicable: true, na_label: null, achievement_pct: "100.000000", on_time_count: 2, late_count: 0, overdue_count: 0 }],
});
const NA_KPI = {
  name: "Financial Accuracy", weight: "1.50", not_applicable: true, na_label: "Not applicable this month",
  achievement_pct: null, auto_points: "0.000000", final_points: "0.000000",
  components: [{ label: "HR FINANCIAL_ACCURACY", applicable: false, na_label: "Awaiting HR assessment", achievement_pct: null, on_time_count: 0, late_count: 0, overdue_count: 0 }],
};
const MONTHS: Record<number, KraMyMonth> = {
  3: { id: 3, year: 2026, month: 11, state: "PROVISIONAL", auto_total: "3.000000", final_total: "3.000000", max_points_applicable: "3.000000", band: "Performance Concern", kpis: [KPI("Accuracy", "3.000000", "3.000000"), NA_KPI], deductions: [] },
  2: { id: 2, year: 2026, month: 10, state: "PENDING_REVIEW" },
  1: { id: 1, year: 2026, month: 9, state: "FINALIZED", auto_total: "7.800000", final_total: "7.550000", max_points_applicable: "8.500000", band: "Consistent Performer", kpis: [KPI("Accuracy", "3.000000", "2.750000")], deductions: [{ rule: "Delayed system update", kpi: "Accuracy", component: "", points: "0.250000" }] },
};
const ANNUAL: KraMyAnnual = {
  year: 2026, applicable_months: 1, annual_total: "7.550000", annual_average: "7.550000", maximum_total: "120.00",
  months: [{ id: 1, month: 9, final_total: "7.550000", max_points_applicable: "8.500000", band: "Consistent Performer" }],
  excluded: [{ month: 10, reason: "NOT_FINALIZED" }],
};
const LEGACY: LegacyPerformanceRow[] = [
  { id: 9, employee: { id: 42, employee_id: "E42", full_name: "Rahul Verma" }, year: 2025, month: 8, status: "FINALIZED", overall_score: "82.50", performance_band: "Very Good" },
];

function serve(history: KraMyHistory = HISTORY) {
  const legacyRequests: URL[] = [];
  server.use(
    http.get("*/api/v1/performance/my/kra-months/", () => HttpResponse.json(history)),
    http.get("*/api/v1/performance/my/kra-months/:id/", ({ params }) => HttpResponse.json(MONTHS[Number(params.id)])),
    http.get("*/api/v1/performance/my/annual/", () => HttpResponse.json(ANNUAL)),
    http.get("*/api/v1/performance/reports/", ({ request }) => {
      legacyRequests.push(new URL(request.url));
      return HttpResponse.json({ count: LEGACY.length, next: null, previous: null, results: LEGACY });
    }),
  );
  return legacyRequests;
}

describe("Employee Home: my performance (Phase 7.4)", () => {
  it("explains that there is nothing to show without an employee record", async () => {
    server.use(signedInAs("Employee"));
    renderApp("/");
    expect(await screen.findByText(/No employee record is linked to your login/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Welcome, Asha" })).toBeInTheDocument();
  });

  for (const role of ["Employee", "HR"] as RoleName[]) {
    it(`shows ${role} their own current month, annual figures, trend and finalized legacy history`, async () => {
      server.use(signedInAs(role));
      const legacyRequests = serve();
      renderApp("/");
      const current = await screen.findByLabelText("Current month");
      expect(within(current).getByText("3.00")).toBeInTheDocument();
      expect(within(current).getByText("Performance Concern (provisional)")).toBeInTheDocument();
      const annual = await screen.findByLabelText("Annual 2026");
      expect(await within(annual).findAllByText("7.55")).toHaveLength(2); // total and average
      expect(within(annual).getByText("Finalized months counted: 1")).toBeInTheDocument();
      const trend = screen.getByRole("list", { name: "Monthly trend 2026" });
      const labels = within(trend).getAllByRole("listitem").map((item) => item.getAttribute("aria-label"));
      expect(labels).toContain("November: 3.00 of 10, provisional");
      expect(labels).toContain("October: under review, score pending");
      expect(labels).toContain("September: 7.80 of 10, finalized");
      expect(labels).toContain("January: no result");
      expect(screen.getByTestId("bar-11")).toHaveAttribute("data-state", "PROVISIONAL");
      expect(screen.queryByTestId("bar-10")).toBeNull(); // pending: no bar
      expect(screen.queryByTestId("bar-1")).toBeNull(); // no result: never drawn as 0
      const legacy = await screen.findByRole("table", { name: "Earlier results" });
      expect(within(legacy).getByText("August 2025")).toBeInTheDocument();
      expect(within(legacy).getByText("82.50")).toBeInTheDocument();
      expect(legacyRequests).toHaveLength(1);
      const query = legacyRequests[0].searchParams;
      expect([query.get("employee"), query.get("status"), query.get("page_size")]).toEqual(["42", "FINALIZED", "200"]);
    });
  }

  it("shows the provisional month's KPIs with N/A labels", async () => {
    server.use(signedInAs("Employee"));
    serve();
    renderApp("/");
    const table = await screen.findByRole("table", { name: "KPI scores November 2026" });
    expect(within(table).getByText("Accuracy")).toBeInTheDocument();
    expect(within(table).getByText("Not applicable this month")).toBeInTheDocument();
    expect(within(table).getByText("Awaiting HR assessment")).toBeInTheDocument();
    expect(screen.getByText(/Provisional: the month is still running/)).toBeInTheDocument();
  });

  it("shows no score for a month under review", async () => {
    server.use(signedInAs("Employee"));
    serve();
    renderApp("/");
    const history = await screen.findByRole("table", { name: "My KRA months" });
    const row = within(history).getByText("October 2026").closest("tr") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "View" }));
    const details = await screen.findByLabelText("Details October 2026");
    expect(within(details).getByText(/Under review – score pending/)).toBeInTheDocument();
    expect(within(details).queryByRole("table")).toBeNull();
  });

  it("shows a finalized month's deductions as rule, target and points only", async () => {
    server.use(signedInAs("Employee"));
    serve();
    renderApp("/");
    const history = await screen.findByRole("table", { name: "My KRA months" });
    const row = within(history).getByText("September 2026").closest("tr") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "View" }));
    const deductions = await screen.findByRole("table", { name: "Deductions September 2026" });
    expect(within(deductions).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Deduction", "Applies to", "Points"]);
    expect(within(deductions).getByText("Delayed system update")).toBeInTheDocument();
    expect(within(deductions).getByText("0.25")).toBeInTheDocument();
    expect(screen.queryByText(/evidence|reason/i)).toBeNull();
  });

  it("says when there are no KRA results yet", async () => {
    server.use(signedInAs("Employee"));
    serve({ ...HISTORY, months: [] });
    renderApp("/");
    expect(await screen.findByText("No KRA results yet.")).toBeInTheDocument();
    expect(screen.getByText("Not calculated yet.")).toBeInTheDocument();
  });
});
