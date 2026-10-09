import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { RoleName } from "../../api/types";
import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";
import { makeActivity, makeClock, makeTask, page } from "./testData";

function taskHandlers(seen: URL[], role: RoleName = "Employee") {
  return [
    signedInAs(role),
    http.get("*/api/v1/tasks/", ({ request }) => {
      seen.push(new URL(request.url));
      return HttpResponse.json(page([makeTask()]));
    }),
    // Phase 5.1: generated daily activities come from their own read-only endpoint.
    http.get("*/api/v1/tasks/daily-activities/", () => HttpResponse.json({
      date: "2026-10-05", server_time: "2026-10-05T05:30:00Z", activities: [makeActivity()],
    })),
    http.get("*/api/v1/tasks/assignees/", () =>
      HttpResponse.json([{ id: 10, full_name: "Rahul Sharma", department: { id: 1, code: "OPS", name: "Operations" } }])),
    http.get("*/api/v1/departments/", () => HttpResponse.json([
      { id: 1, code: "OPS", name: "Operations", is_live: true, created_at: "", updated_at: "" },
      { id: 5, code: "HR", name: "Human Resources", is_live: false, created_at: "", updated_at: "" },
    ])),
    http.get("*/api/v1/task-categories/", () => HttpResponse.json([
      { id: 3, code: "OPERATIONS", name: "Operations", is_active: true, created_at: "", updated_at: "" },
      { id: 4, code: "COMPLIANCE", name: "Compliance", is_active: true, created_at: "", updated_at: "" },
    ])),
    http.get("*/api/v1/employees/", () => HttpResponse.json(page([]))),
    http.get("*/api/v1/task-templates/", () => HttpResponse.json([])),
    http.post("*/api/v1/tasks/sla-preview/", () =>
      HttpResponse.json({ resolution: null, resolution_note: "No SLA configured", acknowledgment: null })),
  ];
}

/** Department and Category are required task-level choices (Phase 4). */
async function classify(dialog: HTMLElement, department = "HR — Human Resources", category = "Compliance") {
  await userEvent.click(within(dialog).getByRole("combobox", { name: /Department/ }));
  await userEvent.click(await screen.findByRole("option", { name: department }));
  await userEvent.click(within(dialog).getByRole("combobox", { name: /Category/ }));
  await userEvent.click(await screen.findByRole("option", { name: category }));
}

describe("Tasks page", () => {
  it("shows received tasks by default and separates sent tasks", async () => {
    const seen: URL[] = [];
    server.use(...taskHandlers(seen));
    renderApp("/tasks");
    expect(
      await screen.findByText("Process SIP mandate", {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(seen.every((u) => u.searchParams.get("view") === "received")).toBe(true);
    // Phase 5/5.1: generated daily activities first (with start, deadline, SLA and countdown),
    // then manually assigned work.
    const daily = screen.getByRole("table", { name: "Daily / scheduled responsibilities" });
    expect(await within(daily).findByText("Feed Upload")).toBeInTheDocument();
    expect(within(daily).getByText("10:00")).toBeInTheDocument();
    expect(within(daily).getByText("12:00")).toBeInTheDocument();
    expect(within(daily).getByText("1h 00m remaining")).toBeInTheDocument();
    expect(within(daily).getByText("Warning")).toBeInTheDocument();
    const assigned = screen.getByRole("table", { name: "Assigned tasks" });
    expect(within(assigned).getByText("Process SIP mandate")).toBeInTheDocument();
    expect(within(assigned).getByText("MANUAL")).toBeInTheDocument();
    expect(within(assigned).getByText("Pending")).toBeInTheDocument();
    expect(within(assigned).getByText("High")).toBeInTheDocument();
    expect(new Set(seen.map((u) => u.searchParams.get("source")))).toEqual(new Set(["manual"]));
    await userEvent.click(screen.getByRole("tab", { name: "Sent tasks" }));
    await waitFor(() => expect(seen.at(-1)?.searchParams.get("view")).toBe("sent"));
    expect(screen.getByRole("columnheader", { name: "Sent to" })).toBeInTheDocument();
  });

  it("lets employees create tasks but hides the All tab", async () => {
    server.use(...taskHandlers([]));
    renderApp("/tasks");
    expect(await screen.findByRole("button", { name: "New task" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "All permitted tasks" })).not.toBeInTheDocument();
  });

  it("gives HR access to all permitted tasks and task creation", async () => {
    server.use(...taskHandlers([], "HR"));
    renderApp("/tasks");
    expect(await screen.findByRole("tab", { name: "All permitted tasks" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New task" })).toBeInTheDocument();
  });

  it("sends the status filter to the backend", async () => {
    const seen: URL[] = [];
    server.use(...taskHandlers(seen));
    renderApp("/tasks");
    await screen.findByText("Process SIP mandate");
    await userEvent.click(screen.getByRole("combobox", { name: "Status" }));
    await userEvent.click(await screen.findByRole("option", { name: "On hold" }));
    await waitFor(() => expect(seen.at(-1)?.searchParams.get("status")).toBe("BLOCKED"));
  });

  it("creates a task, confirms it and opens its page", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(...taskHandlers([]));
    server.use(
      http.post("*/api/v1/tasks/", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeTask({ id: 7, reference: "T-000007", title: "Upload NAV feed" }), { status: 201 });
      }),
      http.get("*/api/v1/tasks/7/", () => HttpResponse.json(makeTask({ id: 7, reference: "T-000007", title: "Upload NAV feed" }))),
      http.get("*/api/v1/tasks/7/comments/", () => HttpResponse.json([])),
      http.get("*/api/v1/tasks/7/attachments/", () => HttpResponse.json([])),
    );
    renderApp("/tasks");
    await userEvent.click(await screen.findByRole("button", { name: "New task" }));
    const dialog = await screen.findByRole("dialog", { name: "New task" });
    expect(within(dialog).getByRole("region", { name: "Basic information" })).toBeInTheDocument();
    expect(within(dialog).getByRole("region", { name: "Assignment" })).toBeInTheDocument();
    expect(within(dialog).getByRole("region", { name: "Configuration" })).toBeInTheDocument();
    expect(within(dialog).getByText("No SLA configured")).toBeInTheDocument();
    expect(within(dialog).queryByLabelText(/completion/i)).not.toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Title"), "Upload NAV feed");
    expect(within(dialog).getByRole("button", { name: "Create Task" })).toBeDisabled(); // needs department + category
    await classify(dialog);
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Create Task" })).toBeEnabled());
    await userEvent.click(within(dialog).getByRole("button", { name: "Create Task" }));
    expect(await screen.findByText("Task T-000007 created.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Upload NAV feed" })).toBeInTheDocument();
    // Department and category are what the creator chose, not the assignee's department.
    expect(body).toMatchObject({ title: "Upload NAV feed", assigned_to: 10, department: 5, category: 4, template: null, received_at: null });
  });

  it("has no task type: the SLA preview follows the priority and creation sends template null (Change Set 1)", async () => {
    const previews: Record<string, unknown>[] = [];
    let created: Record<string, unknown> | null = null;
    server.use(...taskHandlers([]));
    server.use(
      http.post("*/api/v1/tasks/sla-preview/", async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        previews.push(body);
        const critical = body.priority === "URGENT";
        return HttpResponse.json({
          resolution: makeClock({
            rule_name: critical ? "Critical priority — 8 hours" : "Medium priority — 48 hours",
            duration_minutes: critical ? 480 : 2880, state: "ON_TRACK", elapsed_pct: 0,
          }),
          resolution_note: null,
          acknowledgment: null,
        });
      }),
      http.post("*/api/v1/tasks/", async ({ request }) => {
        created = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeTask({ id: 9, reference: "T-000009" }), { status: 201 });
      }),
      http.get("*/api/v1/tasks/9/", () => HttpResponse.json(makeTask({ id: 9, reference: "T-000009" }))),
      http.get("*/api/v1/tasks/9/comments/", () => HttpResponse.json([])),
      http.get("*/api/v1/tasks/9/attachments/", () => HttpResponse.json([])),
    );
    renderApp("/tasks");
    await userEvent.click(await screen.findByRole("button", { name: "New task" }));
    const dialog = await screen.findByRole("dialog", { name: "New task" });
    expect(within(dialog).queryByRole("combobox", { name: "Task type" })).not.toBeInTheDocument();
    expect(within(dialog).queryByLabelText("Event time (IST)")).not.toBeInTheDocument();
    expect(await within(dialog).findByText("Medium priority — 48 hours")).toBeInTheDocument();
    await waitFor(() => expect(previews.at(-1)).toMatchObject({ template: null, priority: "MEDIUM", trigger_at: null }));

    await userEvent.click(within(dialog).getByRole("combobox", { name: "Priority" }));
    await userEvent.click(await screen.findByRole("option", { name: "Critical" }));
    expect(await within(dialog).findByText("Critical priority — 8 hours")).toBeInTheDocument();
    await waitFor(() => expect(previews.at(-1)).toMatchObject({ template: null, priority: "URGENT" }));

    await userEvent.type(within(dialog).getByLabelText("Title"), "Escalated mandate");
    await userEvent.click(within(dialog).getByRole("checkbox", { name: /Acknowledgement required/ }));
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Verification required" }));
    await classify(dialog, "OPS — Operations", "Operations");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create Task" }));
    await waitFor(() => expect(created).toMatchObject({
      template: null, trigger_at: null, priority: "URGENT", department: 1, category: 3,
      acknowledgment_required: true, verification_required: true,
    }));
  });

  it("keeps the values and shows each API error on its field", async () => {
    server.use(...taskHandlers([]));
    server.use(
      http.post("*/api/v1/tasks/", () => HttpResponse.json(
        { code: "validation_error", message: "Some fields are not valid.", fields: { priority: ["Not a valid choice."], received_at: ["Cannot be in the future."] } },
        { status: 400 })),
    );
    renderApp("/tasks");
    await userEvent.click(await screen.findByRole("button", { name: "New task" }));
    const dialog = await screen.findByRole("dialog", { name: "New task" });
    await userEvent.type(within(dialog).getByLabelText("Title"), "Keep me");
    await classify(dialog);
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Create Task" })).toBeEnabled());
    await userEvent.click(within(dialog).getByRole("button", { name: "Create Task" }));
    expect(await within(dialog).findByText("Not a valid choice.")).toBeInTheDocument();
    expect(within(dialog).getByText("Cannot be in the future.")).toBeVisible(); // Advanced details opened
    expect(within(dialog).getByLabelText("Title")).toHaveValue("Keep me");
  });

  it("shows the API error when the list cannot load", async () => {
    server.use(...taskHandlers([]));
    // A later server.use() takes priority over the default list handler.
    server.use(
      http.get("*/api/v1/tasks/", () =>
        HttpResponse.json({ code: "validation_error", message: "Some fields are not valid.", fields: {} }, { status: 400 })),
    );
    renderApp("/tasks");
    expect(await screen.findByRole("alert")).toHaveTextContent("Some fields are not valid.");
  });

  it("shows overdue, completed-late and no-deadline daily activities (Phase 5.1)", async () => {
    server.use(...taskHandlers([]));
    server.use(http.get("*/api/v1/tasks/daily-activities/", () => HttpResponse.json({
      date: "2026-10-05", server_time: "2026-10-05T06:31:00Z", activities: [
        makeActivity({ sla_state: "OVERDUE", remaining_seconds: -60, is_overdue: true }),
        makeActivity({
          task_id: 3, reference: "T-000003", responsibility: { id: 2, code: "MAIL_CHECKING", name: "Mail Checking" },
          status: "COMPLETED", sla_state: "OVERDUE", remaining_seconds: null,
          completed_at: "2026-10-05T06:50:00Z", completion_result: "LATE",
        }),
        makeActivity({
          task_id: 4, reference: "T-000004", responsibility: { id: 4, code: "BROKERAGE_CALCULATION", name: "Brokerage Calculation" },
          deadline: null, sla_state: null, sla_note: "No SLA configured", remaining_seconds: null,
        }),
      ],
    })));
    renderApp("/tasks");
    const daily = await screen.findByRole("table", { name: "Daily / scheduled responsibilities" });
    expect(await within(daily).findByText("00m remaining")).toBeInTheDocument();
    expect(within(daily).getAllByText("Overdue").length).toBeGreaterThan(0);
    expect(within(daily).getByText("Completed late")).toBeInTheDocument();
    expect(within(daily).getByText("No deadline")).toBeInTheDocument();
    expect(within(daily).getByText("No SLA")).toBeInTheDocument();
  });
});
