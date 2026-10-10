import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { KpiCfgOverride, KpiCfgPlan, KpiCfgPlanDefault, KpiCfgResolution } from "../../api/types";
import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";

const ACTIVE_PLAN: KpiCfgPlan = {
  id: 5, configuration: "OPERATIONS_KRA", version: 1, name: "Operations KRA", calculation_model: "KRA_POINTS",
  status: "ACTIVE", effective_from: "2026-11-01", effective_to: null, band_scheme: null,
  credit_on_time: "1.00", credit_late: "0.25", credit_overdue: "0.00", deduction_stacking_method: "ADDITIVE",
  created_at: "2026-10-01T10:00:00+05:30", activated_at: "2026-10-20T10:00:00+05:30", retired_at: null, retire_reason: "",
};
const DEFAULT: KpiCfgPlanDefault = {
  id: 21, department: { id: 1, code: "OPS", name: "Operations" }, role: "Employee", configuration: "OPERATIONS_KRA",
  effective_from: "2026-11-01", effective_to: null, created_at: "2026-10-09T10:00:00+05:30", ended_at: null,
};
const LEGACY_ASSIGNMENT: KpiCfgOverride = {
  id: 31, employee: { id: 42, full_name: "Rahul Verma" },
  plan_version: { id: 1, configuration: "OPERATIONS", version: 1, name: "Operations", status: "ACTIVE", calculation_model: "LEGACY_WEIGHTED" },
  effective_from: "2026-01-01", effective_to: null, reason: "", created_at: "2026-01-01T10:00:00+05:30",
};
const AMBIGUOUS: KpiCfgResolution = {
  employee: { id: 42, full_name: "Rahul Verma" }, date: "2026-11-30", state: "AMBIGUOUS_ROLE", source: null,
  reason: "More than one department + role default matches.", roles: ["Employee", "HR"], plan_version: null,
  override_id: null, default_id: null, matching_default_ids: [21, 22],
};
const employees = {
  count: 1, next: null, previous: null,
  results: [{ id: 42, full_name: "Rahul Verma", department: { id: 1, code: "OPS", name: "Operations" } }],
};

function serve(resolution: KpiCfgResolution = AMBIGUOUS) {
  const writes: { path: string; body: unknown }[] = [];
  const resolutions: URL[] = [];
  server.use(
    http.get("*/api/v1/performance/plan-defaults/", () => HttpResponse.json([DEFAULT])),
    http.get("*/api/v1/performance/assignments/", () => HttpResponse.json([LEGACY_ASSIGNMENT])),
    http.get("*/api/v1/performance/plans/", () => HttpResponse.json([ACTIVE_PLAN])),
    http.get("*/api/v1/departments/", () => HttpResponse.json([{ id: 1, code: "OPS", name: "Operations", is_live: true, created_at: "", updated_at: "" }])),
    http.get("*/api/v1/employees/", () => HttpResponse.json(employees)),
    http.get("*/api/v1/performance/plan-resolution/", ({ request }) => { resolutions.push(new URL(request.url)); return HttpResponse.json(resolution); }),
    http.post("*/api/v1/performance/*", async ({ request }) => {
      writes.push({ path: new URL(request.url).pathname, body: await request.json() });
      return HttpResponse.json({}, { status: 201 });
    }),
  );
  return { writes, resolutions };
}

async function choose(within_: HTMLElement, label: string, option: string | RegExp) {
  await userEvent.click(within(within_).getByRole("combobox", { name: label }));
  await userEvent.click(await screen.findByRole("option", { name: option }));
}

describe("Plan selection (Phase 7.5A)", () => {
  it("lists defaults and overrides, marking a legacy assignment as legacy", async () => {
    server.use(signedInAs("Admin"));
    serve();
    renderApp("/performance/config?tab=selection");
    const defaults = await screen.findByRole("table", { name: "Plan defaults table" });
    expect(await within(defaults).findByText("OPERATIONS_KRA")).toBeInTheDocument();
    const overrides = screen.getByRole("table", { name: "Employee overrides table" });
    expect(await within(overrides).findByText("Rahul Verma")).toBeInTheDocument();
    expect(within(overrides).getByText("Legacy (0–100) assignment")).toBeInTheDocument();
    // Admin reads only: no HR controls
    for (const name of ["Add default", "Add override"]) expect(screen.queryByRole("button", { name })).toBeNull();
    expect(screen.queryByRole("button", { name: /^End/ })).toBeNull();
  });

  it("adds a department + role default", async () => {
    server.use(signedInAs("HR"));
    const { writes } = serve();
    renderApp("/performance/config?tab=selection");
    await userEvent.click(await screen.findByRole("button", { name: "Add default" }));
    const dialog = await screen.findByRole("dialog", { name: "Add plan default" });
    await choose(dialog, "Department", /OPS/);
    await choose(dialog, "System role", "Employee");
    await choose(dialog, "Plan family", "OPERATIONS_KRA");
    await userEvent.type(within(dialog).getByLabelText("Effective from"), "2026-11-01");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes).toEqual([{
      path: "/api/v1/performance/plan-defaults/",
      body: { department: 1, role: "Employee", configuration: "OPERATIONS_KRA", effective_from: "2026-11-01", effective_to: null },
    }]));
  });

  it("requires a reason for an override and offers only active KRA plans", async () => {
    server.use(signedInAs("HR"));
    const { writes } = serve();
    renderApp("/performance/config?tab=selection");
    await userEvent.click(await screen.findByRole("button", { name: "Add override" }));
    const dialog = await screen.findByRole("dialog", { name: "Add employee override" });
    await choose(dialog, "Employee", /Rahul Verma/);
    await choose(dialog, "Plan version", /Operations KRA/);
    await userEvent.type(within(dialog).getByLabelText("Effective from"), "2026-11-01");
    const save = within(dialog).getByRole("button", { name: "Save" });
    expect(save).toBeDisabled(); // no reason yet
    await userEvent.type(within(dialog).getByLabelText("Reason"), "Covers Mail Checking in November");
    await userEvent.click(save);
    await waitFor(() => expect(writes).toEqual([{
      path: "/api/v1/performance/assignments/",
      body: { employee: 42, plan_version: 5, effective_from: "2026-11-01", effective_to: null, reason: "Covers Mail Checking in November" },
    }]));
  });

  it("ends an override on a last day", async () => {
    server.use(signedInAs("HR"));
    const { writes } = serve();
    renderApp("/performance/config?tab=selection");
    await userEvent.click(await screen.findByRole("button", { name: "End override for Rahul Verma" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Last day"), "2026-10-31");
    await userEvent.type(within(dialog).getByLabelText("Reason (optional)"), "KRA starts");
    await userEvent.click(within(dialog).getByRole("button", { name: "End" }));
    await waitFor(() => expect(writes).toEqual([{
      path: "/api/v1/performance/assignments/31/end/", body: { last_day: "2026-10-31", reason: "KRA starts" },
    }]));
  });

  it("shows the backend's plan check answer as given, including an ambiguous role", async () => {
    server.use(signedInAs("HR"));
    const { resolutions } = serve();
    renderApp("/performance/config?tab=selection");
    const section = await screen.findByRole("region", { name: "Which plan applies" });
    await choose(section, "Employee to check", /Rahul Verma/);
    const date = within(section).getByLabelText("Date");
    await userEvent.clear(date);
    await userEvent.type(date, "2026-11-30");
    await userEvent.click(within(section).getByRole("button", { name: "Check" }));
    const result = await within(section).findByLabelText("Plan check result");
    expect(result).toHaveTextContent("Rahul Verma on 30 Nov 2026: Ambiguous: more than one default matches");
    expect(result).toHaveTextContent("More than one department + role default matches.");
    expect(result).toHaveTextContent("System roles: Employee, HR");
    expect(result).toHaveTextContent("Matching defaults: 2 (IDs 21, 22)");
    expect(Object.fromEntries(resolutions[0].searchParams)).toEqual({ employee: "42", date: "2026-11-30" });
  });
});
