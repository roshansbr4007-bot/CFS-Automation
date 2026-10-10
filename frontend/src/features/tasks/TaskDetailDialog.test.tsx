import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { TaskDetail } from "../../api/types";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";
import { TaskDetailDialog } from "./TaskDetailDialog";
import { makeTask } from "./testData";

function serve(task: TaskDetail) {
  server.use(
    http.get("*/api/v1/tasks/1/", () => HttpResponse.json(task)),
    http.get("*/api/v1/tasks/1/comments/", () => HttpResponse.json([])),
    http.get("*/api/v1/tasks/1/attachments/", () => HttpResponse.json([])),
    http.get("*/api/v1/tasks/assignees/", () => HttpResponse.json([])),
  );
}

async function actions() {
  return within(await screen.findByLabelText("Task actions"));
}

describe("Task detail", () => {
  it("shows only the actions the backend allows", async () => {
    serve(makeTask());
    renderWithProviders(<TaskDetailDialog taskId={1} onClose={() => {}} />);
    const bar = await actions();
    expect(bar.getByRole("button", { name: "Start" })).toBeInTheDocument();
    expect(bar.getByRole("button", { name: "Put on hold" })).toBeInTheDocument();
    expect(bar.getByRole("button", { name: "Cancel task" })).toBeInTheDocument();
    expect(bar.queryByRole("button", { name: "Submit Response & Complete" })).not.toBeInTheDocument();
    expect(bar.queryByRole("button", { name: /^Complete/ })).not.toBeInTheDocument();
    expect(screen.getByText("Deadlines and SLA tracking arrive in a later phase.")).toBeInTheDocument();
  });

  it("labels a blocked task On hold and offers Resume", async () => {
    serve(makeTask({ status: "BLOCKED", blocked_reason: "Waiting for KYC", allowed_actions: ["unblock", "cancel"] }));
    renderWithProviders(<TaskDetailDialog taskId={1} onClose={() => {}} />);
    expect((await actions()).getByRole("button", { name: "Resume" })).toBeInTheDocument();
    expect(screen.getByText("On hold")).toBeInTheDocument();
    expect(screen.getByText("Waiting for KYC")).toBeInTheDocument();
  });

  it("shows no actions or comment box to a read-only viewer", async () => {
    serve(makeTask({ allowed_actions: [] }));
    renderWithProviders(<TaskDetailDialog taskId={1} onClose={() => {}} />);
    const bar = await actions();
    expect(bar.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByLabelText("Add a comment")).not.toBeInTheDocument();
  });

  it("explains a 409 version conflict and reloads the task", async () => {
    let loads = 0;
    server.use(
      http.get("*/api/v1/tasks/1/", () => { loads += 1; return HttpResponse.json(makeTask()); }),
      http.get("*/api/v1/tasks/1/comments/", () => HttpResponse.json([])),
      http.get("*/api/v1/tasks/1/attachments/", () => HttpResponse.json([])),
      http.post("*/api/v1/tasks/1/start/", () => HttpResponse.json(
        { code: "version_conflict", message: "This task was changed by someone else.", fields: {} }, { status: 409 })),
    );
    renderWithProviders(<TaskDetailDialog taskId={1} onClose={() => {}} />);
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
    renderWithProviders(<TaskDetailDialog taskId={1} onClose={() => {}} />);
    await userEvent.click((await actions()).getByRole("button", { name: "Reject verification" }));
    const confirm = screen.getByRole("button", { name: "Confirm" });
    expect(confirm).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Reason/), "Wrong folio");
    expect(confirm).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Remarks/), "Use folio 1234");
    await userEvent.click(confirm);
    await waitFor(() => expect(body).toEqual({ version: 1, reason: "Wrong folio", remarks: "Use folio 1234" }));
  });

  it("requires a work response before completing and shows it afterwards", async () => {
    let body: Record<string, unknown> | null = null;
    const response = { id: 9, author: { id: 3, email: "rahul@example.com", full_name: "Rahul Sharma" }, body: "Mapped the RM codes.", kind: "WORK_RESPONSE" as const, created_at: "2026-10-05T06:00:00Z" };
    let current = makeTask({ status: "IN_PROGRESS", allowed_actions: ["complete"] });
    server.use(
      http.get("*/api/v1/tasks/1/", () => HttpResponse.json(current)),
      http.get("*/api/v1/tasks/1/comments/", () => HttpResponse.json([])),
      http.get("*/api/v1/tasks/1/attachments/", () => HttpResponse.json([])),
      http.post("*/api/v1/tasks/1/complete/", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        current = makeTask({ status: "COMPLETED", completed_at: "2026-10-05T06:00:00Z", allowed_actions: [], work_response: response });
        return HttpResponse.json(current);
      }),
    );
    renderWithProviders(<TaskDetailDialog taskId={1} onClose={() => {}} />);
    await userEvent.click((await actions()).getByRole("button", { name: "Submit Response & Complete" }));
    const submit = screen.getByRole("button", { name: "Submit & complete" });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Work performed/), "  ");
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Work performed/), "Mapped the RM codes. ");
    await userEvent.click(submit);
    await waitFor(() => expect(body).toEqual({ version: 1, work_response: "Mapped the RM codes." }));
    const section = within(await screen.findByRole("region", { name: "Work response" }));
    expect(section.getByText("Mapped the RM codes.")).toBeInTheDocument();
  });

  it("shows the backend's validation error for the work response", async () => {
    serve(makeTask({ status: "IN_PROGRESS", allowed_actions: ["complete"] }));
    server.use(http.post("*/api/v1/tasks/1/complete/", () => HttpResponse.json(
      { code: "validation_error", message: "Some fields are not valid.", fields: { work_response: ["Describe the work you did before completing the task."] } },
      { status: 400 })));
    renderWithProviders(<TaskDetailDialog taskId={1} onClose={() => {}} />);
    await userEvent.click((await actions()).getByRole("button", { name: "Submit Response & Complete" }));
    await userEvent.type(screen.getByLabelText(/Work performed/), "Done");
    await userEvent.click(screen.getByRole("button", { name: "Submit & complete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Some fields are not valid.");
    expect(screen.getByText("Describe the work you did before completing the task.")).toBeInTheDocument();
    expect(screen.getByLabelText(/Work performed/)).toHaveValue("Done");
  });

  it("shows verification history", async () => {
    serve(makeTask({
      status: "IN_PROGRESS", verification_status: "REJECTED", rework_count: 1, allowed_actions: [],
      verifications: [{
        cycle_no: 1, submitted_at: "2026-10-05T06:00:00Z", decision: "REJECTED",
        rejection_reason: "Wrong folio", remarks: "Use folio 1234",
        decided_by: { id: 2, email: "manager@example.com", full_name: "Meera Manager" },
        decided_at: "2026-10-05T07:00:00Z", rework_seconds: null,
      }],
    }));
    renderWithProviders(<TaskDetailDialog taskId={1} onClose={() => {}} />);
    expect(await screen.findByText(/Cycle 1:/)).toHaveTextContent("Rejected by Meera Manager");
    expect(screen.getByText("Rework 1")).toBeInTheDocument();
  });
});
