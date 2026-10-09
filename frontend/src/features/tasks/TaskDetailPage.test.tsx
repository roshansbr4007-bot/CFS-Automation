import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { TaskDetail } from "../../api/types";
import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";
import { makeClock, makeTask } from "./testData";

function serve(task: TaskDetail) {
  server.use(
    signedInAs("Employee"),
    http.get("*/api/v1/tasks/1/", () => HttpResponse.json(task)),
    http.get("*/api/v1/tasks/1/comments/", () => HttpResponse.json([])),
    http.get("*/api/v1/tasks/1/attachments/", () => HttpResponse.json([])),
    http.get("*/api/v1/tasks/assignees/", () => HttpResponse.json([])),
  );
}

/** Waits for the loaded page's actions bar. This is the wait that spans the whole page load
 * (auth check, route, detail request, render), so it gets an explicit limit: under full-suite
 * parallel load that can exceed Testing Library's default 1 s even though every request is
 * mocked ("Loading…" then means the mocked request is still in flight, not failed). */
async function actions() {
  return within(await screen.findByLabelText("Task actions", {}, { timeout: 5000 }));
}

describe("Task detail page", () => {
  it("shows sections and only the actions the backend allows", async () => {
    serve(makeTask());
    renderApp("/tasks/1");
    const bar = await actions();
    expect(bar.getByRole("button", { name: "Start" })).toBeInTheDocument();
    expect(bar.getByRole("button", { name: "Put on hold" })).toBeInTheDocument();
    expect(bar.getByRole("button", { name: "Cancel task" })).toBeInTheDocument();
    expect(bar.queryByRole("button", { name: "Complete Task" })).not.toBeInTheDocument();
    for (const name of ["Assignment", "Timeline", "SLA", "Comments", "Attachments"]) {
      expect(screen.getByRole("region", { name })).toBeInTheDocument();
    }
    expect(within(screen.getByRole("region", { name: "SLA" })).getByText("No SLA configured")).toBeInTheDocument();
  });

  it("displays the backend SLA: badge, deadline and progress", async () => {
    serve(makeTask({ sla: { resolution: makeClock(), resolution_note: null, acknowledgment: null } }));
    renderApp("/tasks/1");
    const sla = within(await screen.findByRole("region", { name: "SLA" }));
    expect(sla.getByText("Feed upload · 2 hours")).toBeInTheDocument();
    expect(sla.getByText("Elapsed: 75%")).toBeInTheDocument();
    expect(sla.getByLabelText("75% of SLA used")).toBeInTheDocument();
    expect(screen.getAllByText("Critical").length).toBeGreaterThan(0);
  });

  it("explains a clock that is waiting for its trigger", async () => {
    const waiting = makeClock({ state: "NOT_STARTED", start_at: null, due_at: null, elapsed_pct: null, waiting_for: "Not started — waiting for upstream task" });
    serve(makeTask({ sla: { resolution: waiting, resolution_note: null, acknowledgment: null } }));
    renderApp("/tasks/1");
    expect(await screen.findByText("Not started — waiting for upstream task")).toBeInTheDocument();
  });

  it("labels a blocked task On hold and offers Resume", async () => {
    serve(makeTask({ status: "BLOCKED", blocked_reason: "Waiting for KYC", allowed_actions: ["unblock", "cancel"] }));
    renderApp("/tasks/1");
    expect((await actions()).getByRole("button", { name: "Resume" })).toBeInTheDocument();
    expect(screen.getByText("On hold")).toBeInTheDocument();
    expect(screen.getByText("Waiting for KYC")).toBeInTheDocument();
  });

  it("shows no actions or comment box to a read-only viewer", async () => {
    serve(makeTask({ allowed_actions: [] }));
    renderApp("/tasks/1");
    expect((await actions()).queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByLabelText("Add a comment")).not.toBeInTheDocument();
  });

  it("completes with the server time and shows it; no date can be chosen", async () => {
    let body: unknown = null;
    serve(makeTask({ status: "IN_PROGRESS", allowed_actions: ["complete", "block"] }));
    server.use(http.post("*/api/v1/tasks/1/complete/", async ({ request }) => {
      body = await request.json();
      return HttpResponse.json(makeTask({ status: "COMPLETED", completed_at: "2026-10-05T12:12:00Z", allowed_actions: [] }));
    }));
    renderApp("/tasks/1");
    await userEvent.click((await actions()).getByRole("button", { name: "Complete Task" }));
    expect(await screen.findByText(/Completed at .* IST \(recorded by the server\)\./)).toBeInTheDocument();
    expect(body).toEqual({ version: 1 });
    expect(screen.queryByLabelText(/completion (date|time)/i)).not.toBeInTheDocument();
  });

  it("explains a 409 version conflict and reloads the task", async () => {
    let loads = 0;
    server.use(
      signedInAs("Employee"),
      http.get("*/api/v1/tasks/1/", () => { loads += 1; return HttpResponse.json(makeTask()); }),
      http.get("*/api/v1/tasks/1/comments/", () => HttpResponse.json([])),
      http.get("*/api/v1/tasks/1/attachments/", () => HttpResponse.json([])),
      http.post("*/api/v1/tasks/1/start/", () => HttpResponse.json(
        { code: "version_conflict", message: "This task was changed by someone else.", fields: {} }, { status: 409 })),
    );
    renderApp("/tasks/1");
    await userEvent.click((await actions()).getByRole("button", { name: "Start" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("changed by someone else. It has been reloaded");
    await waitFor(() => expect(loads).toBeGreaterThan(1));
  });

  it("requires reason and remarks before rejecting verification", async () => {
    let body: Record<string, unknown> | null = null;
    serve(makeTask({ status: "COMPLETED", verification_status: "PENDING", completed_at: "2026-10-05T06:00:00Z", allowed_actions: ["verify", "reject_verification"] }));
    server.use(http.post("*/api/v1/tasks/1/reject-verification/", async ({ request }) => {
      body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(makeTask({ status: "IN_PROGRESS", verification_status: "REJECTED", rework_count: 1 }));
    }));
    renderApp("/tasks/1");
    await userEvent.click((await actions()).getByRole("button", { name: "Reject verification" }));
    const dialog = await screen.findByRole("dialog", { name: "Reject verification" });
    const confirm = within(dialog).getByRole("button", { name: "Confirm" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "Wrong folio");
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText(/Remarks/), "Use folio 1234");
    await userEvent.click(confirm);
    await waitFor(() => expect(body).toEqual({ version: 1, reason: "Wrong folio", remarks: "Use folio 1234" }));
  });

  it("deletes only when the backend allows it, sending the current version", async () => {
    let deleteUrl: URL | null = null;
    serve(makeTask({ allowed_actions: ["edit", "reassign", "delete"], version: 4 }));
    server.use(
      http.delete("*/api/v1/tasks/1/", ({ request }) => { deleteUrl = new URL(request.url); return new HttpResponse(null, { status: 204 }); }),
      http.get("*/api/v1/tasks/", () => HttpResponse.json({ count: 0, next: null, previous: null, results: [] })),
    );
    renderApp("/tasks/1");
    expect((await screen.findAllByText("Operations")).length).toBeGreaterThan(0); // category row
    await userEvent.click((await actions()).getByRole("button", { name: "Delete task" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete this task permanently?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete permanently" }));
    expect(await screen.findByText("Task T-000001 was deleted.")).toBeInTheDocument();
    expect(deleteUrl!.searchParams.get("version")).toBe("4");
  });

  it("offers no delete button without the backend's permission", async () => {
    serve(makeTask({ allowed_actions: ["start"] }));
    renderApp("/tasks/1");
    const bar = await actions();
    expect(bar.getByRole("button", { name: "Start" })).toBeInTheDocument();
    expect(bar.queryByRole("button", { name: "Delete task" })).not.toBeInTheDocument();
  });

  it("shows where a scheduled task came from (Phase 5)", async () => {
    serve(makeTask({
      source: "SCHEDULED", title: "Feed Upload — 05 Oct 2026",
      responsibility: { id: 1, code: "FEED_UPLOAD", name: "Feed Upload" },
      schedule: { id: 4, title: "Feed Upload", frequency: "DAILY" },
      occurrence_date: "2026-10-05", generated_at: "2026-10-05T04:30:00Z",
    }));
    renderApp("/tasks/1");
    expect(await screen.findByText("SCHEDULED")).toBeInTheDocument();
    expect(screen.getByText("Responsibility")).toBeInTheDocument();
    expect(screen.getByText("05 Oct 2026")).toBeInTheDocument();
    expect(screen.getByText("Generated at")).toBeInTheDocument();
  });

  it("marks a manual task and hides the schedule rows", async () => {
    serve(makeTask());
    renderApp("/tasks/1");
    expect(await screen.findByText("MANUAL")).toBeInTheDocument();
    expect(screen.queryByText("Occurrence date")).not.toBeInTheDocument();
  });
});
