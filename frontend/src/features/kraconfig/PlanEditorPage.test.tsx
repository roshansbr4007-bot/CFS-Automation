import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { KpiCfgLine, KpiCfgPlanDetail, KpiCfgReadiness } from "../../api/types";
import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";

const LINE: KpiCfgLine = {
  id: 11, plan_version: 5, kpi: { id: 1, code: "ACCURACY", name: "Accuracy" }, name: "Accuracy", display_name: "",
  weight: "3.00", scoring_rule: { id: 2, code: "KRA_BENCHMARK", version: 1, name: "KRA benchmark", status: "ACTIVE" }, position: 1,
  components: [{
    id: 31, plan_line: 11, position: 1, source_type: "RESPONSIBILITY_TASKS",
    responsibility: { id: 7, code: "FEED_UPLOAD", name: "Feed Upload" }, label: "", contribution_share: "1.000000",
    task_scope: "SCHEDULED", manual_match: "", verification_policy: "NOT_REQUIRED",
  }],
};
const PLAN = (overrides: Partial<KpiCfgPlanDetail> = {}): KpiCfgPlanDetail => ({
  id: 5, configuration: "OPERATIONS_KRA", version: 1, name: "Operations KRA", calculation_model: "KRA_POINTS",
  status: "DRAFT", effective_from: "2026-11-01", effective_to: null,
  band_scheme: { id: 3, code: "KRA_BANDS", version: 1, name: "KRA bands", status: "ACTIVE" },
  credit_on_time: "1.00", credit_late: "0.25", credit_overdue: "0.00", deduction_stacking_method: "",
  created_at: "2026-10-01T10:00:00+05:30", activated_at: null, retired_at: null, retire_reason: "",
  lines: [LINE],
  deduction_rules: [{
    id: 41, plan_version: 5, code: "TRANSACTION_ERROR", name: "Transaction error", kind: "BAND_CEILING", scope: "",
    min_pct: null, max_pct: null, ceiling_band: null, stacking: "", cap_pct: null, uncapped: false, priority: null, description: "",
  }],
  ...overrides,
});
const NOT_READY: KpiCfgReadiness = {
  plan_id: 5, status: "DRAFT", calculation_model: "KRA_POINTS", ready: false, checked_on: "2026-10-09",
  problems: ["Line weights must total exactly 10.00 (they total 3.00).", "Deduction TRANSACTION_ERROR: choose the scope."],
};

interface Seen { method: string; path: string; body: unknown }

function serve(plan: KpiCfgPlanDetail, readiness: KpiCfgReadiness = NOT_READY, extra: Parameters<typeof server.use> = []) {
  const seen: Seen[] = [];
  const record = async (request: Request) => {
    const text = await request.text();
    seen.push({ method: request.method, path: new URL(request.url).pathname, body: text ? JSON.parse(text) : null });
  };
  server.use(
    ...extra,
    http.get("*/api/v1/performance/plans/:id/readiness/", () => HttpResponse.json(readiness)),
    http.get("*/api/v1/performance/plans/:id/", () => HttpResponse.json(plan)),
    http.get("*/api/v1/performance/kpis/", () => HttpResponse.json([
      { id: 1, code: "ACCURACY", name: "Accuracy", description: "", is_active: true },
      { id: 2, code: "TIMELINESS", name: "Timeliness", description: "", is_active: true },
    ])),
    http.get("*/api/v1/performance/scoring-rules/", () => HttpResponse.json([
      { id: 2, code: "KRA_BENCHMARK", version: 1, name: "KRA benchmark", status: "ACTIVE", effective_from: "2026-11-01", effective_to: null, below_min_score_pct: null, steps: [], activated_at: null, retired_at: null, retire_reason: "" },
    ])),
    http.get("*/api/v1/performance/band-schemes/", () => HttpResponse.json([])),
    http.get("*/api/v1/performance/band-schemes/:id/", () => HttpResponse.json({
      id: 3, code: "KRA_BANDS", version: 1, name: "KRA bands", status: "ACTIVE", effective_from: "2026-11-01", effective_to: null,
      bands: [{ id: 91, name: "Needs Improvement", min_points: "6.00", position: 3 }], activated_at: null, retired_at: null, retire_reason: "",
    })),
    http.get("*/api/v1/responsibilities/", () => HttpResponse.json([
      { id: 7, code: "FEED_UPLOAD", name: "Feed Upload", is_active: true, department: { id: 1, code: "OPS", name: "Operations" } },
    ])),
    http.post("*/api/v1/performance/*", async ({ request }) => { await record(request); return HttpResponse.json({ id: 99 }, { status: 201 }); }),
    http.patch("*/api/v1/performance/*", async ({ request }) => { await record(request); return HttpResponse.json({}); }),
    http.delete("*/api/v1/performance/*", async ({ request }) => { await record(request); return new HttpResponse(null, { status: 204 }); }),
  );
  return seen;
}

async function choose(label: string, option: string | RegExp) {
  await userEvent.click(screen.getByRole("combobox", { name: label }));
  await userEvent.click(await screen.findByRole("option", { name: option }));
}

describe("KRA plan editor (Phase 7.5A)", () => {
  it("lets HR edit a draft and shows the backend's readiness problems as given", async () => {
    server.use(signedInAs("HR"));
    serve(PLAN());
    renderApp("/performance/config/plans/5");
    expect(await screen.findByRole("heading", { name: "Operations KRA" })).toBeInTheDocument();
    const readiness = await screen.findByRole("list", { name: "Readiness problems" });
    expect(within(readiness).getAllByRole("listitem").map((li) => li.textContent)).toEqual(NOT_READY.problems);
    expect(screen.getByRole("button", { name: "Edit details" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add KPI line" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete draft" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate" })).toBeNull(); // Admin only
    expect(screen.queryByRole("button", { name: "Retire" })).toBeNull();
    // components are listed under their line, values as stored
    const components = screen.getByRole("table", { name: "Components of Accuracy" });
    expect(within(components).getByText("Feed Upload")).toBeInTheDocument();
    expect(within(components).getByText("Scheduled tasks")).toBeInTheDocument();
  });

  it("sends a new KPI line exactly as entered", async () => {
    server.use(signedInAs("HR"));
    const seen = serve(PLAN());
    renderApp("/performance/config/plans/5");
    await userEvent.click(await screen.findByRole("button", { name: "Add KPI line" }));
    const dialog = await screen.findByRole("dialog", { name: "Add KPI line" });
    // a KPI already in the plan is not offered again
    await userEvent.click(within(dialog).getByRole("combobox", { name: "KPI" }));
    expect(screen.queryByRole("option", { name: /Accuracy/ })).toBeNull();
    await userEvent.click(await screen.findByRole("option", { name: /Timeliness/ }));
    await userEvent.type(within(dialog).getByLabelText(/Points/), "2.00");
    await choose("Benchmark rule", /KRA benchmark/);
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]).toEqual({
      method: "POST", path: "/api/v1/performance/plans/5/lines/",
      body: { kpi: 2, weight: "2.00", display_name: "", scoring_rule: 2 },
    });
  });

  it("sends a manual-entry component without responsibility, scope, matching or verification", async () => {
    server.use(signedInAs("HR"));
    const seen = serve(PLAN());
    renderApp("/performance/config/plans/5");
    await userEvent.click(await screen.findByRole("button", { name: "Add component to Accuracy" }));
    const dialog = await screen.findByRole("dialog");
    await choose("Source", "Manual entry (scored by HR)");
    await userEvent.type(within(dialog).getByLabelText("Name of the manual entry"), "HR assessment");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]).toEqual({
      method: "POST", path: "/api/v1/performance/plan-lines/11/components/",
      body: {
        source_type: "MANUAL_ENTRY", responsibility: null, label: "HR assessment", contribution_share: "1",
        task_scope: "", manual_match: "", verification_policy: "",
      },
    });
  });

  it("sends a band-ceiling deduction with its band and without a range", async () => {
    server.use(signedInAs("HR"));
    const seen = serve(PLAN());
    renderApp("/performance/config/plans/5");
    await userEvent.click(await screen.findByRole("button", { name: "Edit deduction TRANSACTION_ERROR" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Code")).toBeDisabled(); // the code never changes
    await choose("Ceiling band", "Needs Improvement");
    await choose("Scope", "Overall");
    await choose("Stacking", "Stacks with other deductions");
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Uncapped" }));
    await userEvent.type(within(dialog).getByLabelText("Priority"), "1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]).toEqual({ // only what changed; numbers as typed (the backend validates them)
      method: "PATCH", path: "/api/v1/performance/deduction-rules/41/",
      body: { scope: "OVERALL", stacking: "STACK", uncapped: true, ceiling_band: 91, priority: "1" },
    });
  });

  it("puts the backend's field errors on their fields", async () => {
    server.use(signedInAs("HR"));
    serve(PLAN(), NOT_READY, [
      http.post("*/api/v1/performance/plans/:id/lines/", () => HttpResponse.json(
        { code: "validation_error", message: "Check the highlighted fields.", fields: { weight: ["Must be greater than 0."] } },
        { status: 400 },
      )),
    ]);
    renderApp("/performance/config/plans/5");
    await userEvent.click(await screen.findByRole("button", { name: "Add KPI line" }));
    const dialog = await screen.findByRole("dialog", { name: "Add KPI line" });
    await choose("KPI", /Timeliness/);
    await userEvent.type(within(dialog).getByLabelText(/Points/), "0");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByText("Must be greater than 0.")).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/Points/)).toHaveAttribute("aria-invalid", "true");
  });

  it("shows a 409 'configuration locked' as an alert in the dialog", async () => {
    server.use(signedInAs("HR"));
    serve(PLAN(), NOT_READY, [
      http.patch("*/api/v1/performance/plans/:id/", () => HttpResponse.json(
        { code: "configuration_locked", message: "Active or retired configuration cannot be changed. Create a new version.", fields: {} },
        { status: 409 },
      )),
    ]);
    renderApp("/performance/config/plans/5");
    await userEvent.click(await screen.findByRole("button", { name: "Edit details" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit draft details" });
    await userEvent.type(within(dialog).getByLabelText("Name"), " (November)");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByText(/cannot be changed. Create a new version/)).toBeInTheDocument();
  });

  it("shows an ACTIVE version read-only to HR, with clone as the only action", async () => {
    server.use(signedInAs("HR"));
    serve(PLAN({ status: "ACTIVE", activated_at: "2026-10-20T10:00:00+05:30" }),
      { ...NOT_READY, status: "ACTIVE", problems: ["Only a draft plan can be activated."] });
    renderApp("/performance/config/plans/5");
    expect(await screen.findByText(/cannot be edited by anyone/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clone as new draft" })).toBeInTheDocument();
    for (const name of ["Edit details", "Add KPI line", "Delete draft", "Add deduction rule", "Edit Accuracy", "Retire", "Activate"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
  });

  it("never adds up the points itself (the total comes only from readiness)", async () => {
    server.use(signedInAs("HR"));
    serve(PLAN({ lines: [LINE, { ...LINE, id: 12, kpi: { id: 2, code: "TIMELINESS", name: "Timeliness" }, name: "Timeliness", weight: "2.00", components: [] }] }),
      { ...NOT_READY, problems: [], ready: true });
    renderApp("/performance/config/plans/5");
    expect(await screen.findByText("Ready to activate")).toBeInTheDocument();
    const lines = screen.getByRole("region", { name: "KPI lines" });
    expect(lines.textContent).toContain("Accuracy — 3 points");
    expect(lines.textContent).toContain("Timeliness — 2 points");
    expect(document.body.textContent).not.toMatch(/(^|[^\d.])5(\.0+)?([^\d.]|$)/); // 3 + 2 is never shown
  });

  it("sends only what changed when a line is edited, keeping its retired benchmark rule selectable", async () => {
    server.use(signedInAs("HR"));
    const retiredRule = { ...LINE.scoring_rule!, status: "RETIRED" as const };
    const seen = serve(PLAN({ lines: [{ ...LINE, scoring_rule: retiredRule }] }), NOT_READY, [
      http.get("*/api/v1/performance/scoring-rules/", () => HttpResponse.json([
        { id: 2, code: "KRA_BENCHMARK", version: 1, name: "KRA benchmark", status: "RETIRED", effective_from: "2026-11-01", effective_to: null, below_min_score_pct: null, steps: [], activated_at: null, retired_at: null, retire_reason: "" },
      ])),
    ]);
    renderApp("/performance/config/plans/5");
    await userEvent.click(await screen.findByRole("button", { name: "Edit Accuracy" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByRole("combobox", { name: "Benchmark rule" })).toHaveTextContent("KRA benchmark");
    const points = within(dialog).getByLabelText(/Points/);
    await userEvent.clear(points);
    await userEvent.type(points, "3.50");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(seen).toEqual([{ method: "PATCH", path: "/api/v1/performance/plan-lines/11/", body: { weight: "3.50" } }]));
  });

  it("sends only the changed setting when a component is edited", async () => {
    server.use(signedInAs("HR"));
    const seen = serve(PLAN());
    renderApp("/performance/config/plans/5");
    await userEvent.click(await screen.findByRole("button", { name: "Edit component Feed Upload" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeDisabled(); // nothing changed yet
    await choose("Verification policy", "Counts only once verified");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(seen).toEqual([{
      method: "PATCH", path: "/api/v1/performance/components/31/", body: { verification_policy: "REQUIRED" },
    }]));
  });

  it("sends only the changed plan details and refuses to empty a stored credit", async () => {
    server.use(signedInAs("HR"));
    const seen = serve(PLAN());
    renderApp("/performance/config/plans/5");
    await userEvent.click(await screen.findByRole("button", { name: "Edit details" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit draft details" });
    const save = within(dialog).getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    await userEvent.clear(within(dialog).getByLabelText("Credit late"));
    expect(within(dialog).getByText("Enter a value (a stored credit cannot be removed).")).toBeInTheDocument();
    expect(save).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Credit late"), "0.50");
    await choose("Stacking deductions combine", "Additive");
    await userEvent.click(save);
    await waitFor(() => expect(seen).toEqual([{
      method: "PATCH", path: "/api/v1/performance/plans/5/", body: { deduction_stacking_method: "ADDITIVE", credit_late: "0.50" },
    }]));
  });

  it("deletes a draft after confirmation and returns to the plan list without reloading the deleted draft", async () => {
    server.use(signedInAs("HR"));
    let deleted = false;
    const afterDelete: string[] = [];
    const gone = () => HttpResponse.json({ code: "not_found", message: "Not found.", fields: {} }, { status: 404 });
    const seen = serve(PLAN(), NOT_READY, [
      http.get("*/api/v1/performance/plans/", () => HttpResponse.json([])),
      http.get("*/api/v1/performance/plans/:id/readiness/", ({ request }) => {
        if (deleted) { afterDelete.push(new URL(request.url).pathname); return gone(); }
        return HttpResponse.json(NOT_READY);
      }),
      http.get("*/api/v1/performance/plans/:id/", ({ request }) => {
        if (deleted) { afterDelete.push(new URL(request.url).pathname); return gone(); }
        return HttpResponse.json(PLAN());
      }),
      http.delete("*/api/v1/performance/plans/:id/", () => { deleted = true; return new HttpResponse(null, { status: 204 }); }),
    ]);
    renderApp("/performance/config/plans/5");
    await userEvent.click(await screen.findByRole("button", { name: "Delete draft" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete draft" }));
    expect(await screen.findByRole("table", { name: "KRA plan versions" })).toBeInTheDocument();
    expect(deleted).toBe(true);
    expect(seen).toEqual([]); // the DELETE went to its own handler above
    expect(afterDelete).toEqual([]); // the deleted draft and its readiness are never asked for again
    expect(screen.queryByText("This plan version does not exist.")).toBeNull();
  });

  it("explains a plan id that does not exist", async () => {
    server.use(signedInAs("HR"), http.get("*/api/v1/performance/plans/:id/", () => HttpResponse.json(
      { code: "not_found", message: "Not found.", fields: {} }, { status: 404 },
    )));
    renderApp("/performance/config/plans/123456");
    expect(await screen.findByText("This plan version does not exist.")).toBeInTheDocument();
  });
});
