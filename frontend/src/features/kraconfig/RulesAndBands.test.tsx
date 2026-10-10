import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { KpiCfgBandScheme, KpiCfgScoringRule } from "../../api/types";
import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";

const RULE = (overrides: Partial<KpiCfgScoringRule> = {}): KpiCfgScoringRule => ({
  id: 2, code: "KRA_BENCHMARK", version: 1, name: "KRA benchmark", status: "DRAFT", effective_from: "2026-11-01",
  effective_to: null, below_min_score_pct: "0.00",
  steps: [{ min_achievement_pct: "100.00", score_pct: "100.00" }, { min_achievement_pct: "85.00", score_pct: "80.00" }],
  activated_at: null, retired_at: null, retire_reason: "", ...overrides,
});
const SCHEME = (overrides: Partial<KpiCfgBandScheme> = {}): KpiCfgBandScheme => ({
  id: 3, code: "KRA_BANDS", version: 1, name: "KRA bands", status: "DRAFT", effective_from: "2026-11-01", effective_to: null,
  bands: [
    { id: 91, name: "High Performer", min_points: "9.00", position: 1 },
    { id: 92, name: "Consistent Performer", min_points: "7.50", position: 2 },
  ],
  activated_at: null, retired_at: null, retire_reason: "", ...overrides,
});

function serve(rule: KpiCfgScoringRule, scheme: KpiCfgBandScheme) {
  const writes: { method: string; path: string; body: unknown }[] = [];
  const record = async (request: Request) => {
    const text = await request.text();
    writes.push({ method: request.method, path: new URL(request.url).pathname, body: text ? JSON.parse(text) : null });
  };
  server.use(
    http.get("*/api/v1/performance/scoring-rules/", () => HttpResponse.json([rule])),
    http.get("*/api/v1/performance/scoring-rules/:id/", ({ params }) =>
      HttpResponse.json(params.id === "8" ? RULE({ id: 8, version: 2 }) : rule)),
    http.get("*/api/v1/performance/band-schemes/", () => HttpResponse.json([scheme])),
    http.get("*/api/v1/performance/band-schemes/:id/", () => HttpResponse.json(scheme)),
    http.post("*/api/v1/performance/*", async ({ request }) => { await record(request); return HttpResponse.json(RULE({ id: 8, version: 2 }), { status: 201 }); }),
    http.patch("*/api/v1/performance/*", async ({ request }) => { await record(request); return HttpResponse.json({}); }),
  );
  return writes;
}

describe("Benchmark rules and band schemes (Phase 7.5A)", () => {
  it("lists benchmark rules and opens one with its steps", async () => {
    server.use(signedInAs("HR"));
    serve(RULE(), SCHEME());
    renderApp("/performance/config?tab=scoring-rules");
    const table = await screen.findByRole("table", { name: "Benchmark rules" });
    await userEvent.click(await within(table).findByRole("link", { name: "Open KRA_BENCHMARK v1" }));
    const steps = await screen.findByRole("table", { name: "Benchmark steps table" });
    expect(within(steps).getByText("85 %")).toBeInTheDocument();
    expect(within(steps).getByText("80 %")).toBeInTheDocument();
  });

  it("sends a draft rule's steps as a whole list", async () => {
    server.use(signedInAs("HR"));
    const writes = serve(RULE(), SCHEME());
    renderApp("/performance/config/scoring-rules/2");
    await userEvent.click(await screen.findByRole("button", { name: "Edit draft" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove step 2" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Add step" }));
    await userEvent.type(within(dialog).getByLabelText("Step 2: achievement at least %"), "70");
    await userEvent.type(within(dialog).getByLabelText("Step 2: score %"), "60");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toEqual({ // only what changed: the steps, as a whole list
      method: "PATCH", path: "/api/v1/performance/scoring-rules/2/",
      body: { steps: [{ min_achievement_pct: "100.00", score_pct: "100.00" }, { min_achievement_pct: "70", score_pct: "60" }] },
    });
  });

  it("does not resend the steps when only the name changes, and needs a change to save", async () => {
    server.use(signedInAs("HR"));
    const writes = serve(RULE(), SCHEME());
    renderApp("/performance/config/scoring-rules/2");
    await userEvent.click(await screen.findByRole("button", { name: "Edit draft" }));
    const dialog = await screen.findByRole("dialog");
    const save = within(dialog).getByRole("button", { name: "Save" });
    expect(save).toBeDisabled(); // nothing changed yet
    await userEvent.type(within(dialog).getByLabelText("Name"), " 2026");
    await userEvent.click(save);
    await waitFor(() => expect(writes).toEqual([{
      method: "PATCH", path: "/api/v1/performance/scoring-rules/2/", body: { name: "KRA benchmark 2026" },
    }]));
  });

  it("shows DRF's nested step errors on the step they belong to", async () => {
    server.use(signedInAs("HR"));
    serve(RULE(), SCHEME());
    server.use(http.patch("*/api/v1/performance/scoring-rules/:id/", () => HttpResponse.json({
      code: "validation_error", message: "Some fields are not valid.",
      fields: { steps: [{}, { score_pct: ["A valid number is required."] }] },
    }, { status: 400 })));
    renderApp("/performance/config/scoring-rules/2");
    await userEvent.click(await screen.findByRole("button", { name: "Edit draft" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Step 2: score %"), "x");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByText("A valid number is required.")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Step 2: score %")).toHaveAttribute("aria-invalid", "true");
    expect(within(dialog).getByLabelText("Step 1: score %")).toHaveAttribute("aria-invalid", "false");
    expect(within(dialog).queryByText(/object Object/)).toBeNull();
  });

  it("shows an ACTIVE rule read-only; Admin may retire it, HR may only clone it", async () => {
    server.use(signedInAs("Admin"));
    serve(RULE({ status: "ACTIVE" }), SCHEME());
    const { unmount } = renderApp("/performance/config/scoring-rules/2");
    expect(await screen.findByText(/cannot be edited by anyone/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retire" })).toBeInTheDocument();
    for (const name of ["Edit draft", "Activate", "Clone as new draft"]) expect(screen.queryByRole("button", { name })).toBeNull();
    unmount();

    server.use(signedInAs("HR"));
    renderApp("/performance/config/scoring-rules/2");
    expect(await screen.findByRole("button", { name: "Clone as new draft" })).toBeInTheDocument();
    for (const name of ["Edit draft", "Activate", "Retire"]) expect(screen.queryByRole("button", { name })).toBeNull();
  });

  it("lets Admin activate a draft rule after confirmation", async () => {
    server.use(signedInAs("Admin"));
    const writes = serve(RULE(), SCHEME());
    renderApp("/performance/config/scoring-rules/2");
    await userEvent.click(await screen.findByRole("button", { name: "Activate" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Activate" }));
    await waitFor(() => expect(writes).toEqual([{ method: "POST", path: "/api/v1/performance/scoring-rules/2/activate/", body: null }]));
  });

  it("clones a rule into a new draft and opens it", async () => {
    server.use(signedInAs("HR"));
    const writes = serve(RULE({ status: "ACTIVE" }), SCHEME());
    renderApp("/performance/config/scoring-rules/2");
    await userEvent.click(await screen.findByRole("button", { name: "Clone as new draft" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Clone" }));
    await waitFor(() => expect(writes).toEqual([{ method: "POST", path: "/api/v1/performance/scoring-rules/2/clone/", body: null }]));
    expect(await screen.findByText("Benchmark rule KRA_BENCHMARK v2")).toBeInTheDocument();
  });

  it("sends a draft band scheme's bands as a whole list", async () => {
    server.use(signedInAs("HR"));
    const writes = serve(RULE(), SCHEME());
    renderApp("/performance/config/band-schemes/3");
    const bands = await screen.findByRole("table", { name: "Bands table" });
    expect(within(bands).getByText("7.5")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Edit draft" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add band" }));
    await userEvent.type(within(dialog).getByLabelText("Band 3: name"), "Needs Improvement");
    await userEvent.type(within(dialog).getByLabelText("Band 3: from points"), "6");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toEqual({ // only the bands changed: sent as a whole list, in order, with positions
      method: "PATCH", path: "/api/v1/performance/band-schemes/3/",
      body: {
        bands: [
          { name: "High Performer", min_points: "9.00", position: 1 },
          { name: "Consistent Performer", min_points: "7.50", position: 2 },
          { name: "Needs Improvement", min_points: "6", position: 3 },
        ],
      },
    });
  });

  it("never resends the bands for a name or date change (bands used as a ceiling stay untouched)", async () => {
    server.use(signedInAs("HR"));
    const writes = serve(RULE(), SCHEME());
    renderApp("/performance/config/band-schemes/3");
    await userEvent.click(await screen.findByRole("button", { name: "Edit draft" }));
    const dialog = await screen.findByRole("dialog");
    const from = within(dialog).getByLabelText("Effective from");
    await userEvent.clear(from);
    await userEvent.type(from, "2026-12-01");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes).toEqual([{
      method: "PATCH", path: "/api/v1/performance/band-schemes/3/", body: { effective_from: "2026-12-01" },
    }]));
  });

  it("places a band error returned by the backend on its band", async () => {
    server.use(signedInAs("HR"));
    serve(RULE(), SCHEME());
    server.use(http.patch("*/api/v1/performance/band-schemes/:id/", () => HttpResponse.json({
      code: "validation_error", message: "Check the highlighted fields.",
      fields: { "bands.1": ["Band names and minimum points must be unique."] },
    }, { status: 400 })));
    renderApp("/performance/config/band-schemes/3");
    await userEvent.click(await screen.findByRole("button", { name: "Edit draft" }));
    const dialog = await screen.findByRole("dialog");
    const second = within(dialog).getByLabelText("Band 2: from points");
    await userEvent.clear(second);
    await userEvent.type(second, "9.00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByText("Band names and minimum points must be unique.")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Band 2: name")).toHaveAttribute("aria-invalid", "true");
    expect(within(dialog).getByLabelText("Band 1: name")).toHaveAttribute("aria-invalid", "false");
  });
});
