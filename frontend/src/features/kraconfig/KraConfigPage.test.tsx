import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { KpiCfgPlan, RoleName } from "../../api/types";
import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";

const plan = (overrides: Partial<KpiCfgPlan>): KpiCfgPlan => ({
  id: 5, configuration: "OPERATIONS_KRA", version: 1, name: "Operations KRA", calculation_model: "KRA_POINTS",
  status: "DRAFT", effective_from: "2026-11-01", effective_to: null, band_scheme: null,
  credit_on_time: "1.00", credit_late: "0.25", credit_overdue: "0.00", deduction_stacking_method: "",
  created_at: "2026-10-01T10:00:00+05:30", activated_at: null, retired_at: null, retire_reason: "", ...overrides,
});
const PLANS = [
  plan({ id: 1, configuration: "OPERATIONS", name: "Operations (legacy)", calculation_model: "LEGACY_WEIGHTED", status: "ACTIVE", effective_from: "2026-01-01" }),
  plan({}),
];

function serve() {
  const posts: { path: string; body: unknown }[] = [];
  server.use(
    http.get("*/api/v1/performance/plans/", () => HttpResponse.json(PLANS)),
    http.get("*/api/v1/performance/band-schemes/", () => HttpResponse.json([])),
    http.get("*/api/v1/performance/kpis/", () => HttpResponse.json([
      { id: 1, code: "ACCURACY", name: "Accuracy", description: "Zero errors", is_active: true },
    ])),
    http.post("*/api/v1/performance/*", async ({ request }) => {
      const text = await request.text();
      posts.push({ path: new URL(request.url).pathname, body: text ? JSON.parse(text) : null });
      return HttpResponse.json({ ...plan({ id: 77 }), lines: [], deduction_rules: [] }, { status: 201 });
    }),
    http.patch("*/api/v1/performance/kpis/:id/", async ({ request }) => {
      posts.push({ path: new URL(request.url).pathname, body: await request.json() });
      return HttpResponse.json({});
    }),
    http.get("*/api/v1/performance/plans/:id/", () => HttpResponse.json({ ...plan({ id: 77, version: 2 }), lines: [], deduction_rules: [] })),
    http.get("*/api/v1/performance/plans/:id/readiness/", () => HttpResponse.json({
      plan_id: 77, status: "DRAFT", calculation_model: "KRA_POINTS", ready: false, problems: ["Add the KPI lines."], checked_on: "2026-10-09",
    })),
  );
  return posts;
}

describe("KRA configuration: access and plan list (Phase 7.5A)", () => {
  for (const role of ["Employee", "Operations Manager"] as RoleName[]) {
    it(`keeps ${role} out of every configuration page`, async () => {
      server.use(signedInAs(role));
      serve();
      for (const path of ["/performance/config", "/performance/config/plans/5", "/performance/config/scoring-rules/2", "/performance/config/band-schemes/3"]) {
        const { unmount } = renderApp(path);
        expect(await screen.findByRole("heading", { name: /access/i })).toBeInTheDocument();
        unmount();
      }
    });
  }

  it("lists plan versions for HR, legacy ones marked read-only, with draft creation and KRA cloning", async () => {
    server.use(signedInAs("HR"));
    serve();
    renderApp("/performance/config");
    const table = await screen.findByRole("table", { name: "KRA plan versions" });
    expect(await within(table).findByText("Operations KRA")).toBeInTheDocument();
    expect(within(table).getByText("Legacy (0–100), read-only")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New draft plan" })).toBeInTheDocument();
    expect(within(table).getByRole("button", { name: "Clone Operations KRA (OPERATIONS_KRA v1)" })).toBeInTheDocument();
    expect(within(table).queryByRole("button", { name: /Clone Operations \(legacy\)/ })).toBeNull();
  });

  it("shows Admin the same list without any HR preparation controls", async () => {
    server.use(signedInAs("Admin"));
    serve();
    renderApp("/performance/config");
    const table = await screen.findByRole("table", { name: "KRA plan versions" });
    expect(await within(table).findByText("Operations KRA")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New draft plan" })).toBeNull();
    expect(within(table).queryByRole("button", { name: /^Clone/ })).toBeNull();
    expect(within(table).getByRole("link", { name: "Open Operations KRA (OPERATIONS_KRA v1)" })).toBeInTheDocument();
  });

  it("creates a draft plan with what HR entered and opens it", async () => {
    server.use(signedInAs("HR"));
    const posts = serve();
    renderApp("/performance/config");
    await userEvent.click(await screen.findByRole("button", { name: "New draft plan" }));
    const dialog = await screen.findByRole("dialog", { name: "New draft plan" });
    await userEvent.type(within(dialog).getByLabelText("Plan family code"), "hr_kra");
    await userEvent.type(within(dialog).getByLabelText("Name"), "HR KRA");
    await userEvent.type(within(dialog).getByLabelText("Effective from"), "2026-12-01");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create draft" }));
    await waitFor(() => expect(posts).toEqual([{
      path: "/api/v1/performance/plans/",
      body: { configuration: "HR_KRA", name: "HR KRA", effective_from: "2026-12-01", effective_to: null, band_scheme: null },
    }]));
    expect(await screen.findByText("Add the KPI lines.")).toBeInTheDocument(); // the new draft's page
  });

  it("clones a KRA version after confirmation and opens the new draft", async () => {
    server.use(signedInAs("HR"));
    const posts = serve();
    renderApp("/performance/config");
    await userEvent.click(await screen.findByRole("button", { name: "Clone Operations KRA (OPERATIONS_KRA v1)" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/original version does not change/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Clone" }));
    await waitFor(() => expect(posts).toEqual([{ path: "/api/v1/performance/plans/5/clone/", body: null }]));
    expect(await screen.findByText("OPERATIONS_KRA v2")).toBeInTheDocument();
  });

  it("lets HR edit only a KPI's description; Admin only reads the KPI list", async () => {
    server.use(signedInAs("HR"));
    const posts = serve();
    const { unmount } = renderApp("/performance/config?tab=kpis");
    await userEvent.click(await screen.findByRole("button", { name: "Edit description of Accuracy" }));
    const dialog = await screen.findByRole("dialog");
    const field = within(dialog).getByLabelText("Description");
    await userEvent.clear(field);
    await userEvent.type(field, "No transaction errors");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posts).toEqual([{ path: "/api/v1/performance/kpis/1/", body: { description: "No transaction errors" } }]));
    unmount();

    server.use(signedInAs("Admin"));
    renderApp("/performance/config?tab=kpis");
    expect(await screen.findByText("Zero errors")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit description/ })).toBeNull();
  });
});
