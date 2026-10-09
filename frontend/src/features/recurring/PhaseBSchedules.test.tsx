import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { RecurringSchedule, Responsibility } from "../../api/types";
import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";
import { todayIST } from "./ScheduleFields";

const OPS = { id: 1, code: "OPS", name: "Operations" };

function responsibility(overrides: Partial<Responsibility> = {}): Responsibility {
  return {
    id: 4, code: "WEEKLY_REPORT", name: "Weekly report", description: "", department: OPS,
    category: { id: 3, code: "OPERATIONS", name: "Operations" }, template: null, priority: "LOW",
    is_active: true, current_owner: null, version: 1, created_at: "", updated_at: "",
    active_schedule_count: 0, can_manage: true, ...overrides,
  };
}

function schedule(overrides: Partial<RecurringSchedule> = {}): RecurringSchedule {
  return {
    id: 7, responsibility: { id: 4, code: "WEEKLY_REPORT", name: "Weekly report" }, title: "Weekly report",
    description: "", frequency: "DAILY", run_time: "10:00:00", day_of_month: null, non_working_day_policy: "SKIP",
    is_active: true, effective_from: "2026-10-05", effective_to: null, version: 1, created_at: "", updated_at: "",
    can_manage: true, weekdays: [], run_date: null, ...overrides,
  };
}

function handlers(posts: { url: string; body: unknown }[], options: { schedules?: RecurringSchedule[]; patch?: () => Response } = {}) {
  const record = async ({ request }: { request: Request }) => {
    posts.push({ url: new URL(request.url).pathname, body: await request.json() });
    return request.method === "PATCH" && options.patch ? options.patch() : HttpResponse.json(schedule(), { status: 201 });
  };
  return [
    http.get("*/api/v1/responsibilities/", () => HttpResponse.json([responsibility()])),
    http.get("*/api/v1/recurring-schedules/", () => HttpResponse.json(options.schedules ?? [])),
    http.get("*/api/v1/departments/", () => HttpResponse.json([{ ...OPS, is_live: true, created_at: "", updated_at: "" }])),
    http.get("*/api/v1/task-categories/", () => HttpResponse.json([
      { id: 3, code: "OPERATIONS", name: "Operations", is_active: true, created_at: "", updated_at: "" },
    ])),
    http.get("*/api/v1/task-templates/", () => HttpResponse.json([])),
    http.get("*/api/v1/employees/", () => HttpResponse.json({ count: 0, next: null, previous: null, results: [] })),
    http.post("*/api/v1/recurring-schedules/", record),
    http.patch("*/api/v1/recurring-schedules/:id/", record),
  ];
}

async function chooseRepeats(dialog: HTMLElement, option: string) {
  await userEvent.click(within(dialog).getByRole("combobox", { name: "Repeats" }));
  await userEvent.click(await screen.findByRole("option", { name: option }));
}

async function openAddSchedule() {
  const table = await screen.findByRole("table", { name: "Responsibilities" });
  await userEvent.click(await within(table).findByRole("button", { name: "Add schedule" }));
  return screen.findByRole("dialog", { name: "Add schedule — Weekly report" });
}

describe("Weekly and specific-date schedules (Phase B)", () => {
  it("offers the four recurrence options", async () => {
    server.use(signedInAs("HR"), ...handlers([]));
    renderApp("/responsibilities");
    const dialog = await openAddSchedule();
    await userEvent.click(within(dialog).getByRole("combobox", { name: "Repeats" }));
    const options = (await screen.findAllByRole("option")).map((o) => o.textContent);
    expect(options).toEqual(["Daily (working days)", "Monthly", "Weekly", "Specific date (once)"]);
  });

  it("requires at least one weekday and sends the sorted weekdays", async () => {
    const posts: { url: string; body: unknown }[] = [];
    server.use(signedInAs("Operations Manager"), ...handlers(posts));
    renderApp("/responsibilities");
    const dialog = await openAddSchedule();
    await chooseRepeats(dialog, "Weekly");
    fireEvent.change(within(dialog).getByLabelText("Time (IST)"), { target: { value: "10:00" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Add schedule" }));
    expect(await within(dialog).findByText("Choose at least one weekday.")).toBeInTheDocument();
    expect(posts).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Thu" }));
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Mon" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Add schedule" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toEqual({
      responsibility: 4, title: "Weekly report", frequency: "WEEKLY", run_time: "10:00", day_of_month: null,
      weekdays: [0, 3], non_working_day_policy: "SKIP", effective_from: todayIST(), effective_to: null,
    });
  });

  it("validates the one-off date and sends it without an effective window", async () => {
    const posts: { url: string; body: unknown }[] = [];
    server.use(signedInAs("Admin"), ...handlers(posts));
    renderApp("/responsibilities");
    const dialog = await openAddSchedule();
    await chooseRepeats(dialog, "Specific date (once)");
    expect(within(dialog).queryByLabelText("Starts on")).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Time (IST)"), { target: { value: "11:30" } });
    fireEvent.change(within(dialog).getByLabelText("Date"), { target: { value: "2020-01-01" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Add schedule" }));
    expect(await within(dialog).findByText("Choose today or a later date.")).toBeInTheDocument();
    expect(posts).toHaveLength(0);
    fireEvent.change(within(dialog).getByLabelText("Date"), { target: { value: "2030-01-15" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Add schedule" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toEqual({
      responsibility: 4, title: "Weekly report", frequency: "ONCE", run_time: "11:30", day_of_month: null,
      run_date: "2030-01-15", non_working_day_policy: "SKIP",
    });
  });

  it("describes each kind of schedule in the list", async () => {
    server.use(signedInAs("HR"), ...handlers([], { schedules: [
      schedule({ id: 1, title: "Daily duty" }),
      schedule({ id: 2, title: "Monthly duty", frequency: "MONTHLY", day_of_month: 20 }),
      schedule({ id: 3, title: "Weekly duty", frequency: "WEEKLY", weekdays: [0, 3] }),
      schedule({ id: 4, title: "One-off duty", frequency: "ONCE", run_date: "2026-11-14", effective_to: "2026-11-14" }),
    ] }));
    renderApp("/schedules");
    const table = await screen.findByRole("table", { name: "Recurring schedules" });
    for (const text of ["Every working day", "Monthly on day 20", "Weekly — Mon, Thu", "Once — 14 Nov 2026"]) {
      expect(await within(table).findByText(text)).toBeInTheDocument();
    }
  });

  it("edits weekdays of a weekly schedule; the frequency itself is not editable", async () => {
    const posts: { url: string; body: unknown }[] = [];
    const weekly = schedule({ frequency: "WEEKLY", weekdays: [0, 3], title: "Weekly duty" });
    server.use(signedInAs("Operations Manager"), ...handlers(posts, { schedules: [weekly], patch: () => HttpResponse.json(weekly) }));
    renderApp("/schedules");
    const table = await screen.findByRole("table", { name: "Recurring schedules" });
    await userEvent.click(await within(table).findByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("combobox", { name: "Repeats" })).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Mon" }));
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Thu" }));
    expect(within(dialog).getByText("Choose at least one weekday.")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Fri" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toMatchObject({ version: 1, weekdays: [4] });
  });

  it("shows the server's refusal to move an already generated one-off date", async () => {
    const once = schedule({ frequency: "ONCE", run_date: "2026-10-14", effective_to: "2026-10-14", title: "One-off duty" });
    server.use(signedInAs("Admin"), ...handlers([], { schedules: [once], patch: () => HttpResponse.json(
      { code: "validation_error", message: "Some fields are not valid.", fields: { run_date: ["This date has already been processed. Create a new schedule instead."] } },
      { status: 400 },
    ) }));
    renderApp("/schedules");
    const table = await screen.findByRole("table", { name: "Recurring schedules" });
    await userEvent.click(await within(table).findByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Date"), { target: { value: "2030-01-20" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByText("This date has already been processed. Create a new schedule instead.")).toBeInTheDocument();
  });
});
