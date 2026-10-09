import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Responsibility } from "../../api/types";
import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";

const OPS = { id: 1, code: "OPS", name: "Operations" };

function responsibility(overrides: Partial<Responsibility> = {}): Responsibility {
  return {
    id: 4, code: "BIRTHDAY_WISHES", name: "Birthday Wishes", description: "", department: OPS,
    category: { id: 3, code: "OPERATIONS", name: "Operations" }, template: null, priority: "LOW",
    is_active: true, current_owner: null, version: 2, created_at: "", updated_at: "",
    active_schedule_count: 1, can_manage: true, deadline_minutes: null, can_manage_deadline: true, ...overrides,
  };
}

/** Lookups the dialogs need plus a recorder for writes (PATCH = edit, POST = setup). */
function handlers(list: Responsibility[], writes: { method: string; body: unknown }[]) {
  const record = async ({ request }: { request: Request }) => {
    writes.push({ method: request.method, body: await request.json() });
    return HttpResponse.json(responsibility({ version: 3 }), { status: request.method === "POST" ? 201 : 200 });
  };
  return [
    http.get("*/api/v1/responsibilities/", () => HttpResponse.json(list)),
    http.get("*/api/v1/departments/", () => HttpResponse.json([{ ...OPS, is_live: true, created_at: "", updated_at: "" }])),
    http.get("*/api/v1/task-categories/", () => HttpResponse.json([
      { id: 3, code: "OPERATIONS", name: "Operations", is_active: true, created_at: "", updated_at: "" },
    ])),
    http.get("*/api/v1/task-templates/", () => HttpResponse.json([])),
    http.get("*/api/v1/employees/", () => HttpResponse.json({ count: 0, next: null, previous: null, results: [] })),
    http.patch("*/api/v1/responsibilities/:id/", record),
    http.post("*/api/v1/responsibilities/setup/", record),
  ];
}

async function openEdit() {
  const table = await screen.findByRole("table", { name: "Responsibilities" });
  const row = (await within(table).findByText("Birthday Wishes")).closest("tr") as HTMLElement;
  await userEvent.click(within(row).getByRole("button", { name: "Edit" }));
  return screen.findByRole("dialog", { name: "Edit responsibility" });
}

const hours = (dialog: HTMLElement) => within(dialog).getByRole("spinbutton", { name: "Deadline hours" });
const minutes = (dialog: HTMLElement) => within(dialog).getByRole("spinbutton", { name: "Deadline minutes" });

describe("Responsibility deadline (SLA)", () => {
  it("shows the deadline in the list", async () => {
    server.use(signedInAs("HR"), ...handlers([
      responsibility({ deadline_minutes: 120 }),
      responsibility({ id: 5, code: "BROKERAGE", name: "Brokerage Calculation", deadline_minutes: 90 }),
      responsibility({ id: 6, code: "MAIL", name: "Mail Checking", deadline_minutes: null }),
    ], []));
    renderApp("/responsibilities");
    const table = await screen.findByRole("table", { name: "Responsibilities" });
    const cell = (name: string) => (within(table).getByText(name).closest("tr") as HTMLElement);
    await within(table).findByText("Birthday Wishes");
    expect(within(cell("Birthday Wishes")).getByText("2 h")).toBeInTheDocument();
    expect(within(cell("Brokerage Calculation")).getByText("1 h 30 min")).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Deadline (SLA)" })).toBeInTheDocument();
  });

  it.each(["HR", "Admin"] as const)("%s changes and clears the deadline", async (role) => {
    const writes: { method: string; body: unknown }[] = [];
    server.use(signedInAs(role), ...handlers([responsibility({ deadline_minutes: 120 })], writes));
    renderApp("/responsibilities");
    let dialog = await openEdit();
    expect(hours(dialog)).toHaveValue(2);
    expect(minutes(dialog)).toHaveValue(0);
    fireEvent.change(hours(dialog), { target: { value: "1" } });
    fireEvent.change(minutes(dialog), { target: { value: "30" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toMatchObject({ method: "PATCH", body: { version: 2, deadline_minutes: 90 } });

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    dialog = await openEdit();
    fireEvent.change(hours(dialog), { target: { value: "" } });
    fireEvent.change(minutes(dialog), { target: { value: "" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes).toHaveLength(2));
    expect(writes[1].body).toMatchObject({ deadline_minutes: null });
  });

  it("does not send the deadline when it is unchanged, and rejects an invalid value", async () => {
    const writes: { method: string; body: unknown }[] = [];
    server.use(signedInAs("HR"), ...handlers([responsibility({ deadline_minutes: 120 })], writes));
    renderApp("/responsibilities");
    const dialog = await openEdit();
    fireEvent.change(minutes(dialog), { target: { value: "75" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(within(dialog).getByText("Enter whole hours and 0–59 minutes, more than 0 in total.")).toBeInTheDocument();
    expect(writes).toHaveLength(0);
    fireEvent.change(minutes(dialog), { target: { value: "0" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].body).not.toHaveProperty("deadline_minutes");
  });

  it("is read-only for an Operations Manager and never sent", async () => {
    const writes: { method: string; body: unknown }[] = [];
    server.use(signedInAs("Operations Manager"),
      ...handlers([responsibility({ deadline_minutes: 120, can_manage_deadline: false })], writes));
    renderApp("/responsibilities");
    const dialog = await openEdit();
    expect(hours(dialog)).toBeDisabled();
    expect(minutes(dialog)).toBeDisabled();
    expect(hours(dialog)).toHaveValue(2);
    expect(within(dialog).getByText("Only HR or Admin can configure the responsibility deadline.")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].body).not.toHaveProperty("deadline_minutes");
  });

  it("Admin can set it while setting up a responsibility; an Operations Manager cannot", async () => {
    const writes: { method: string; body: unknown }[] = [];
    server.use(signedInAs("Admin"), ...handlers([], writes));
    renderApp("/responsibilities");
    await userEvent.click(await screen.findByRole("button", { name: "Add responsibility" }));
    const dialog = await screen.findByRole("dialog", { name: "Add responsibility" });
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Code" }), "birthday_wishes");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "Birthday Wishes");
    await userEvent.click(within(dialog).getByRole("combobox", { name: "Department" }));
    await userEvent.click(await screen.findByRole("option", { name: "OPS — Operations" }));
    await userEvent.click(within(dialog).getByRole("combobox", { name: "Category" }));
    await userEvent.click(await screen.findByRole("option", { name: "Operations" }));
    fireEvent.change(within(dialog).getByLabelText("Time (IST)"), { target: { value: "10:00" } });
    fireEvent.change(hours(dialog), { target: { value: "2" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toMatchObject({ method: "POST", body: { code: "BIRTHDAY_WISHES", deadline_minutes: 120 } });
  });

  it("is disabled in the setup form for an Operations Manager", async () => {
    server.use(signedInAs("Operations Manager"), ...handlers([], []));
    renderApp("/responsibilities");
    await userEvent.click(await screen.findByRole("button", { name: "Add responsibility" }));
    const dialog = await screen.findByRole("dialog", { name: "Add responsibility" });
    expect(hours(dialog)).toBeDisabled();
    expect(minutes(dialog)).toBeDisabled();
  });

  it("is not available to an employee", async () => {
    server.use(signedInAs("Employee"));
    renderApp("/responsibilities");
    expect(await screen.findByRole("heading", { name: /don't have access/i })).toBeInTheDocument();
  });
});
