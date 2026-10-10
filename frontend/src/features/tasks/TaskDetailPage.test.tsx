import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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
    expect(bar.queryByRole("button", { name: "Submit Response & Complete" })).not.toBeInTheDocument();
    expect(bar.queryByRole("button", { name: /^Complete/ })).not.toBeInTheDocument();
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

  const RESPONSE = { id: 9, author: { id: 3, email: "rahul@example.com", full_name: "Rahul Sharma" }, body: "Uploaded the feed and checked the totals.", kind: "WORK_RESPONSE" as const, created_at: "2026-10-05T12:12:00Z" };

  async function openCompletion() {
    await userEvent.click((await actions()).getByRole("button", { name: "Submit Response & Complete" }));
    const dialog = await screen.findByRole("dialog", { name: "Submit work response" });
    return { dialog: within(dialog), field: within(dialog).getByLabelText(/Work performed/), submit: within(dialog).getByRole("button", { name: "Submit & complete" }) };
  }

  it("requires a work response, sends it trimmed and completes with the server time", async () => {
    let body: unknown = null;
    let current = makeTask({ status: "IN_PROGRESS", allowed_actions: ["complete", "block"] });
    serve(current);
    server.use(http.get("*/api/v1/tasks/1/", () => HttpResponse.json(current))); // reload after completing
    server.use(http.post("*/api/v1/tasks/1/complete/", async ({ request }) => {
      body = await request.json();
      current = makeTask({ status: "COMPLETED", completed_at: "2026-10-05T12:12:00Z", allowed_actions: [], work_response: RESPONSE });
      return HttpResponse.json(current);
    }));
    renderApp("/tasks/1");
    const { field, submit } = await openCompletion();
    expect(submit).toBeDisabled(); // empty
    await userEvent.type(field, "   ");
    expect(submit).toBeDisabled(); // whitespace only
    await userEvent.type(field, "Uploaded the feed and checked the totals.  ");
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    expect(await screen.findByText(/Completed at .* IST \(recorded by the server\)\./)).toBeInTheDocument();
    expect(body).toEqual({ version: 1, work_response: "Uploaded the feed and checked the totals." });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const panel = within(screen.getByRole("region", { name: "Work response" }));
    expect(panel.getByText("Uploaded the feed and checked the totals.")).toBeInTheDocument();
    expect(panel.getByText(/Submitted by Rahul Sharma/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/completion (date|time)/i)).not.toBeInTheDocument();
  });

  it("shows the backend's validation error and keeps the response for another try", async () => {
    serve(makeTask({ status: "IN_PROGRESS", allowed_actions: ["complete"] }));
    server.use(http.post("*/api/v1/tasks/1/complete/", () => HttpResponse.json(
      { code: "validation_error", message: "Some fields are not valid.", fields: { work_response: ["Describe the work you did before completing the task."] } },
      { status: 400 })));
    renderApp("/tasks/1");
    const { dialog, field, submit } = await openCompletion();
    await userEvent.type(field, "Done");
    await userEvent.click(submit);
    expect(await dialog.findByRole("alert")).toHaveTextContent("Some fields are not valid.");
    expect(dialog.getByText("Describe the work you did before completing the task.")).toBeInTheDocument();
    expect(field).toHaveValue("Done");
    expect(screen.queryByText(/recorded by the server/)).not.toBeInTheDocument();
  });

  it("shows a refused completion (for example a 409) inside the dialog", async () => {
    serve(makeTask({ status: "IN_PROGRESS", allowed_actions: ["complete"] }));
    server.use(http.post("*/api/v1/tasks/1/complete/", () => HttpResponse.json(
      { code: "acknowledgment_required", message: "Acknowledge the task before starting or completing it.", fields: {} },
      { status: 409 })));
    renderApp("/tasks/1");
    const { dialog, field, submit } = await openCompletion();
    await userEvent.type(field, "Done");
    await userEvent.click(submit);
    expect(await dialog.findByRole("alert")).toHaveTextContent("Acknowledge the task before starting or completing it.");
  });

  it("disables submission while the request is in flight", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    let calls = 0;
    serve(makeTask({ status: "IN_PROGRESS", allowed_actions: ["complete"] }));
    server.use(http.post("*/api/v1/tasks/1/complete/", async () => {
      calls += 1;
      await gate;
      return HttpResponse.json(makeTask({ status: "COMPLETED", completed_at: "2026-10-05T12:12:00Z", allowed_actions: [], work_response: RESPONSE }));
    }));
    renderApp("/tasks/1");
    const { dialog, field, submit } = await openCompletion();
    await userEvent.type(field, "Done");
    await userEvent.click(submit);
    const pending = await dialog.findByRole("button", { name: "Submitting…" });
    expect(pending).toBeDisabled();
    expect(field).toBeDisabled();
    expect(dialog.getByRole("button", { name: "Back" })).toBeDisabled(); // its result is always seen
    fireEvent.click(pending); // a second click while pending sends nothing
    release();
    expect(await screen.findByText(/Completed at .* IST/)).toBeInTheDocument();
    expect(calls).toBe(1);
  });

  it("keeps the typed response after a version conflict reload and clears the old error", async () => {
    let loads = 0;
    serve(makeTask({ status: "IN_PROGRESS", allowed_actions: ["complete"] }));
    server.use(
      http.get("*/api/v1/tasks/1/", () => { loads += 1; return HttpResponse.json(makeTask({ status: "IN_PROGRESS", allowed_actions: ["complete"], version: loads })); }),
      http.post("*/api/v1/tasks/1/complete/", () => HttpResponse.json(
        { code: "version_conflict", message: "This task was changed by someone else.", fields: {} }, { status: 409 })),
    );
    renderApp("/tasks/1");
    const first = await openCompletion();
    await userEvent.type(first.field, "Checked the KYC documents.");
    await userEvent.click(first.submit);
    expect(await screen.findByRole("alert")).toHaveTextContent("It has been reloaded");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(loads).toBeGreaterThan(1));
    const again = await openCompletion();
    expect(again.field).toHaveValue("Checked the KYC documents.");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(again.submit).toBeEnabled();
  });

  it("shows a reopened task's earlier response as a previous one", async () => {
    serve(makeTask({ status: "IN_PROGRESS", verification_status: "REJECTED", rework_count: 1, allowed_actions: ["complete"], work_response: RESPONSE }));
    renderApp("/tasks/1");
    const panel = within(await screen.findByRole("region", { name: "Work response" }));
    expect(panel.getByText(/Previous response/)).toBeInTheDocument();
    const { field } = await openCompletion();
    expect(field).toHaveValue(""); // a new response is required
  });

  it("shows a manager the submitted response and marks it in the comments", async () => {
    serve(makeTask({ status: "COMPLETED", completed_at: "2026-10-05T12:12:00Z", allowed_actions: ["comment"], work_response: RESPONSE }));
    server.use(http.get("*/api/v1/tasks/1/comments/", () => HttpResponse.json([RESPONSE])));
    renderApp("/tasks/1");
    const panel = within(await screen.findByRole("region", { name: "Work response" }));
    expect(panel.getByText("Uploaded the feed and checked the totals.")).toBeInTheDocument();
    const comments = within(screen.getByRole("region", { name: "Comments" }));
    expect(await comments.findByText("Work response")).toBeInTheDocument();
    expect((await actions()).queryByRole("button", { name: "Submit Response & Complete" })).not.toBeInTheDocument();
  });

  it("shows no work response panel before completion", async () => {
    serve(makeTask({ status: "IN_PROGRESS", allowed_actions: ["complete"], work_response: null }));
    renderApp("/tasks/1");
    await actions();
    expect(screen.queryByRole("region", { name: "Work response" })).not.toBeInTheDocument();
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
    expect(screen.queryByText("Scheduled time")).not.toBeInTheDocument();
    expect(screen.queryByText("Arrived overdue")).not.toBeInTheDocument();
  });

  const scheduled = {
    source: "SCHEDULED" as const, title: "Feed Upload — 05 Oct 2026",
    responsibility: { id: 1, code: "FEED_UPLOAD", name: "Feed Upload" },
    schedule: { id: 4, title: "Feed Upload", frequency: "DAILY" },
    occurrence_date: "2026-10-05", generated_at: "2026-10-05T07:00:00Z", // 12:30 IST, late
    scheduled_at: "2026-10-05T10:00:00+05:30",
  };

  function rowValue(label: string) {
    return (screen.getByText(label).parentElement as HTMLElement).textContent ?? "";
  }

  it("shows the scheduled time and a system-caused overdue arrival (read-only)", async () => {
    serve(makeTask({
      ...scheduled, arrived_overdue: true, ack_arrived_overdue: true,
      sla: { resolution: makeClock({ start_at: "2026-10-05T04:30:00Z", state: "OVERDUE" }), resolution_note: null, acknowledgment: null },
    }));
    renderApp("/tasks/1");
    expect(await screen.findByText("Arrived overdue")).toBeInTheDocument();
    expect(rowValue("Scheduled time")).toMatch(/10:00/);
    expect(rowValue("Generated at")).toMatch(/12:30/);
    const note = screen.getByText(/generated after its SLA deadline had already passed \(system-caused\)/);
    expect(note).toHaveTextContent("The acknowledgement SLA was already overdue when this task was generated.");
    const sla = within(screen.getByRole("region", { name: "SLA" }));
    expect(sla.getByText(/SLA start:/)).toBeInTheDocument();
    expect(sla.queryByText(/^Starts:/)).not.toBeInTheDocument();
    // Informational only: the actions still come from the backend's allowed_actions.
    expect((await actions()).getByRole("button", { name: "Start" })).toBeInTheDocument();
  });

  it("notes an acknowledgement-only late arrival without the overdue badge", async () => {
    serve(makeTask({ ...scheduled, arrived_overdue: false, ack_arrived_overdue: true }));
    renderApp("/tasks/1");
    expect(await screen.findByText("The acknowledgement SLA was already overdue when this task was generated.")).toBeInTheDocument();
    expect(screen.queryByText("Arrived overdue")).not.toBeInTheDocument();
  });

  it("shows no arrival facts when they were not recorded", async () => {
    serve(makeTask({ ...scheduled, arrived_overdue: null, ack_arrived_overdue: null }));
    renderApp("/tasks/1");
    expect(await screen.findByText("SCHEDULED")).toBeInTheDocument();
    expect(rowValue("Scheduled time")).toMatch(/10:00/);
    expect(screen.queryByText("Arrived overdue")).not.toBeInTheDocument();
    expect(screen.queryByText(/acknowledgement SLA was already overdue/)).not.toBeInTheDocument();
  });
});
