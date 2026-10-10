import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { KpiCfgPlanDetail, KpiCfgReadiness } from "../../api/types";
import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";

const PLAN = (overrides: Partial<KpiCfgPlanDetail> = {}): KpiCfgPlanDetail => ({
  id: 5, configuration: "OPERATIONS_KRA", version: 1, name: "Operations KRA", calculation_model: "KRA_POINTS",
  status: "DRAFT", effective_from: "2026-11-01", effective_to: null, band_scheme: null,
  credit_on_time: "1.00", credit_late: "0.25", credit_overdue: "0.00", deduction_stacking_method: "ADDITIVE",
  created_at: "2026-10-01T10:00:00+05:30", activated_at: null, retired_at: null, retire_reason: "",
  lines: [], deduction_rules: [], ...overrides,
});
const READY: KpiCfgReadiness = {
  plan_id: 5, status: "DRAFT", calculation_model: "KRA_POINTS", ready: true, problems: [], checked_on: "2026-10-09",
};

function serve(plan: KpiCfgPlanDetail, readiness: KpiCfgReadiness, extra: Parameters<typeof server.use> = []) {
  const posts: { path: string; body: unknown }[] = [];
  server.use(
    ...extra,
    http.get("*/api/v1/performance/plans/:id/readiness/", () => HttpResponse.json(readiness)),
    http.get("*/api/v1/performance/plans/:id/", () => HttpResponse.json(plan)),
    http.post("*/api/v1/performance/plans/:id/:action/", async ({ request }) => {
      const text = await request.text();
      posts.push({ path: new URL(request.url).pathname, body: text ? JSON.parse(text) : null });
      return HttpResponse.json({ ...plan, status: "ACTIVE" });
    }),
  );
  return posts;
}

describe("Plan lifecycle: Admin activates and retires (Phase 7.5A)", () => {
  it("gives Admin no edit controls on a draft and keeps Activate disabled until the server says ready", async () => {
    server.use(signedInAs("Admin"));
    serve(PLAN(), { ...READY, ready: false, problems: ["Add the KPI lines."] });
    renderApp("/performance/config/plans/5");
    const activate = await screen.findByRole("button", { name: "Activate" });
    await waitFor(() => expect(screen.getByText("Add the KPI lines.")).toBeInTheDocument());
    expect(activate).toBeDisabled();
    for (const name of ["Edit details", "Add KPI line", "Delete draft", "Clone as new draft", "Add deduction rule"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
  });

  it("says so next to a disabled Activate when readiness cannot be checked", async () => {
    server.use(signedInAs("Admin"));
    serve(PLAN(), READY, [
      http.get("*/api/v1/performance/plans/:id/readiness/", () => HttpResponse.json(
        { code: "permission_denied", message: "You do not have permission to do this.", fields: {} }, { status: 403 },
      )),
    ]);
    renderApp("/performance/config/plans/5");
    expect(await screen.findByText("Readiness could not be checked.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Activate" })).toBeDisabled();
  });

  it("activates a ready draft after confirmation", async () => {
    server.use(signedInAs("Admin"));
    const posts = serve(PLAN(), READY);
    renderApp("/performance/config/plans/5");
    const activate = await screen.findByRole("button", { name: "Activate" });
    await waitFor(() => expect(activate).toBeEnabled());
    await userEvent.click(activate);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/can never be\s+edited again/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Activate" }));
    await waitFor(() => expect(posts).toEqual([{ path: "/api/v1/performance/plans/5/activate/", body: null }]));
  });

  it("lists every problem when the server refuses the activation", async () => {
    server.use(signedInAs("Admin"));
    serve(PLAN(), READY, [
      http.post("*/api/v1/performance/plans/:id/activate/", () => HttpResponse.json({
        code: "validation_error", message: "This plan cannot be activated yet.",
        fields: { activation: ["The effective date must be in the future.", "Choose a band scheme."] },
      }, { status: 400 })),
    ]);
    renderApp("/performance/config/plans/5");
    const activate = await screen.findByRole("button", { name: "Activate" });
    await waitFor(() => expect(activate).toBeEnabled());
    await userEvent.click(activate);
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Activate" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("This plan cannot be activated yet.");
    expect(alert).toHaveTextContent("The effective date must be in the future.");
    expect(alert).toHaveTextContent("Choose a band scheme.");
  });

  it("retires an ACTIVE version only with a last day and a reason", async () => {
    server.use(signedInAs("Admin"));
    const posts = serve(PLAN({ status: "ACTIVE" }), { ...READY, status: "ACTIVE", ready: false, problems: ["Only a draft plan can be activated."] });
    renderApp("/performance/config/plans/5");
    expect(await screen.findByText(/cannot be edited by anyone/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Retire" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Retire" }));
    expect(within(dialog).getByText("Choose the last day.")).toBeInTheDocument();
    expect(within(dialog).getByText("A reason is required.")).toBeInTheDocument();
    expect(posts).toEqual([]);
    await userEvent.type(within(dialog).getByLabelText("Last day"), "2026-11-30");
    await userEvent.type(within(dialog).getByLabelText("Reason"), "December plan replaces it");
    await userEvent.click(within(dialog).getByRole("button", { name: "Retire" }));
    await waitFor(() => expect(posts).toEqual([{
      path: "/api/v1/performance/plans/5/retire/", body: { last_day: "2026-11-30", reason: "December plan replaces it" },
    }]));
  });

  it("shows the backend's retirement refusal (for example, overrides still running)", async () => {
    server.use(signedInAs("Admin"));
    serve(PLAN({ status: "ACTIVE" }), { ...READY, status: "ACTIVE", ready: false, problems: ["Only a draft plan can be activated."] }, [
      http.post("*/api/v1/performance/plans/:id/retire/", () => HttpResponse.json({
        code: "kpi_config_conflict", message: "Employee overrides of this version run past that day; end them first.", fields: {},
      }, { status: 409 })),
    ]);
    renderApp("/performance/config/plans/5");
    await userEvent.click(await screen.findByRole("button", { name: "Retire" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Last day"), "2026-11-30");
    await userEvent.type(within(dialog).getByLabelText("Reason"), "Replaced");
    await userEvent.click(within(dialog).getByRole("button", { name: "Retire" }));
    expect(await within(dialog).findByText(/end them first/)).toBeInTheDocument();
  });

  it("lets Admin retire an active legacy version, which is otherwise read-only (no readiness, no clone)", async () => {
    server.use(signedInAs("Admin"));
    serve(PLAN({ calculation_model: "LEGACY_WEIGHTED", status: "ACTIVE", configuration: "OPERATIONS" }), READY);
    renderApp("/performance/config/plans/5");
    expect(await screen.findByText(/Legacy \(0–100\) version: read-only here/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retire" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Activation readiness" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Clone as new draft" })).toBeNull();
  });

  it("gives HR no Activate or Retire, and no clone of a legacy version", async () => {
    server.use(signedInAs("HR"));
    serve(PLAN({ calculation_model: "LEGACY_WEIGHTED", status: "ACTIVE", configuration: "OPERATIONS" }), READY);
    renderApp("/performance/config/plans/5");
    expect(await screen.findByText(/Legacy \(0–100\) version/)).toBeInTheDocument();
    for (const name of ["Activate", "Retire", "Clone as new draft", "Edit details"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
  });
});
