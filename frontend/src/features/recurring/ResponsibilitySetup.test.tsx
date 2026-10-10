import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { RecurringSchedule, Responsibility } from "../../api/types";
import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";
import { todayIST } from "./ScheduleFields";

const OPS = { id: 1, code: "OPS", name: "Operations" };
const RAHUL = { id: 10, full_name: "Rahul Sharma", department: OPS, email: "", is_active: true };

function responsibility(overrides: Partial<Responsibility> = {}): Responsibility {
  return {
    id: 1, code: "FEED_UPLOAD", name: "Feed Upload", description: "", department: OPS,
    category: { id: 3, code: "OPERATIONS", name: "Operations" }, template: null, priority: "MEDIUM",
    is_active: true, current_owner: null, version: 1, created_at: "", updated_at: "",
    active_schedule_count: 1, can_manage: true, ...overrides,
  };
}

function schedule(overrides: Partial<RecurringSchedule> = {}): RecurringSchedule {
  return {
    id: 7, responsibility: { id: 1, code: "FEED_UPLOAD", name: "Feed Upload" }, title: "Feed Upload", description: "",
    frequency: "DAILY", run_time: "10:00:00", day_of_month: null, non_working_day_policy: "SKIP", is_active: true,
    effective_from: "2026-10-05", effective_to: null, version: 1, created_at: "", updated_at: "", can_manage: true,
    ...overrides,
  };
}

/** Lookups the setup form needs, plus a recorder for the write requests. */
function lookups(list: Responsibility[], posts: { url: string; body: unknown }[], reply?: () => Response) {
  const record = async ({ request }: { request: Request }) => {
    posts.push({ url: new URL(request.url).pathname, body: await request.json() });
    return reply ? reply() : HttpResponse.json(responsibility({ id: 99 }), { status: 201 });
  };
  return [
    http.get("*/api/v1/responsibilities/", () => HttpResponse.json(list)),
    http.get("*/api/v1/recurring-schedules/", () => HttpResponse.json([])),
    http.get("*/api/v1/departments/", () => HttpResponse.json([{ ...OPS, is_live: true, created_at: "", updated_at: "" }])),
    http.get("*/api/v1/task-categories/", () => HttpResponse.json([
      { id: 3, code: "OPERATIONS", name: "Operations", is_active: true, created_at: "", updated_at: "" },
    ])),
    http.get("*/api/v1/task-templates/", () => HttpResponse.json([
      { id: 5, code: "MAIL_CHECK", name: "Mail Checking", department: OPS, trigger: "LOGIN", fixed_time: null,
        resolution_rule_code: "WITHIN_2H", sla_rule_name: "2 hours", sla_note: null,
        acknowledgment_required: false, verification_required: false },
    ])),
    http.get("*/api/v1/employees/", () => HttpResponse.json({ count: 1, next: null, previous: null, results: [RAHUL] })),
    http.post("*/api/v1/responsibilities/setup/", record),
    http.post("*/api/v1/recurring-schedules/", record),
  ];
}

async function choose(scope: HTMLElement, label: string, option: string) {
  await userEvent.click(within(scope).getByRole("combobox", { name: label }));
  await userEvent.click(await screen.findByRole("option", { name: option }));
}

async function openSetup() {
  await userEvent.click(await screen.findByRole("button", { name: "Add responsibility" }));
  return screen.findByRole("dialog", { name: "Add responsibility" });
}

async function fillBasics(dialog: HTMLElement) {
  await userEvent.type(within(dialog).getByRole("textbox", { name: "Code" }), "birthday_wish");
  await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "Birthday wish");
  await choose(dialog, "Department", "OPS — Operations");
  await choose(dialog, "Category", "Operations");
}

describe("Responsibility setup (Phase A)", () => {
  // Locked rule A: HR / Admin set the owner (this flow was the Operations Manager's before).
  it("creates responsibility, owner and schedule in one request", async () => {
    const posts: { url: string; body: unknown }[] = [];
    server.use(signedInAs("HR"), ...lookups([], posts));
    renderApp("/responsibilities");
    const dialog = await openSetup();
    await fillBasics(dialog);
    await choose(dialog, "Task type", "Mail Checking");
    await choose(dialog, "Owner", "Rahul Sharma (OPS)");
    fireEvent.change(within(dialog).getByLabelText("Time (IST)"), { target: { value: "10:00" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].url).toBe("/api/v1/responsibilities/setup/");
    expect(posts[0].body).toEqual({
      code: "BIRTHDAY_WISH", name: "Birthday wish", description: "", department: 1, category: 3, priority: "MEDIUM",
      template: 5, owner: { employee: 10, effective_from: todayIST() },
      schedule: { frequency: "DAILY", run_time: "10:00", day_of_month: null, non_working_day_policy: "SKIP",
        effective_from: todayIST(), effective_to: null },
    });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Add responsibility" })).not.toBeInTheDocument());
  });

  it("checks the schedule before sending (no past start for non-Admin, monthly day)", async () => {
    const posts: { url: string; body: unknown }[] = [];
    server.use(signedInAs("HR"), ...lookups([], posts));
    renderApp("/responsibilities");
    const dialog = await openSetup();
    await fillBasics(dialog);
    await choose(dialog, "Repeats", "Monthly");
    fireEvent.change(within(dialog).getByLabelText("Starts on"), { target: { value: "2020-01-01" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByText("A schedule cannot start in the past.")).toBeInTheDocument();
    expect(within(dialog).getByText("Choose the time (IST).")).toBeInTheDocument();
    expect(within(dialog).getByText("Use a day from 1 to 28.")).toBeInTheDocument();
    expect(posts).toHaveLength(0);
  });

  it("shows a backend error on the field of its section", async () => {
    const posts: { url: string; body: unknown }[] = [];
    server.use(signedInAs("Admin"), ...lookups([], posts, () => HttpResponse.json(
      { code: "validation_error", message: "Some fields are not valid.", fields: { "schedule.run_time": ["Enter a valid time."] } },
      { status: 400 },
    )));
    renderApp("/responsibilities");
    const dialog = await openSetup();
    await fillBasics(dialog);
    fireEvent.change(within(dialog).getByLabelText("Time (IST)"), { target: { value: "10:00" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByText("Enter a valid time.")).toBeInTheDocument();
    expect(within(dialog).getByText("Some fields are not valid.")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Add responsibility" })).toBeInTheDocument(); // still open
  });
});

describe("Responsibility setup: owner rules", () => {
  it("offers an Operations Manager no owner, and still sets up the rest", async () => {
    const posts: { url: string; body: unknown }[] = [];
    server.use(signedInAs("Operations Manager"), ...lookups([], posts));
    renderApp("/responsibilities");
    const dialog = await openSetup();
    expect(within(dialog).getByText("Only HR or Admin can assign the owner.")).toBeInTheDocument();
    expect(within(dialog).queryByRole("combobox", { name: "Owner" })).not.toBeInTheDocument();
    await fillBasics(dialog);
    fireEvent.change(within(dialog).getByLabelText("Time (IST)"), { target: { value: "10:00" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect((posts[0].body as { owner: unknown }).owner).toBeNull();
  });

  it("lists owners of the chosen department only", async () => {
    const seen: URL[] = [];
    server.use(signedInAs("Admin"), ...lookups([], []));
    server.use(  // a later server.use() takes priority over the shared employees handler
      http.get("*/api/v1/employees/", ({ request }) => {
        seen.push(new URL(request.url));
        return HttpResponse.json({ count: 1, next: null, previous: null, results: [RAHUL] });
      }));
    renderApp("/responsibilities");
    const dialog = await openSetup();
    expect(within(dialog).getByText("Choose the department first.")).toBeInTheDocument();
    await choose(dialog, "Department", "OPS — Operations");
    await choose(dialog, "Owner", "Rahul Sharma (OPS)");
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every((u) => u.searchParams.get("department") === "1")).toBe(true);
  });
});

describe("Responsibility setup: today's generation", () => {
  it("reports a late setup's generated task for today", async () => {
    const posts: { url: string; body: unknown }[] = [];
    server.use(signedInAs("HR"), ...lookups([], posts, () => HttpResponse.json(
      { ...responsibility({ id: 99, name: "Birthday wish" }),
        today_generation: [{ schedule_id: 7, occurrence_date: "2026-10-05", result: "generated", detail: null }] },
      { status: 201 },
    )));
    renderApp("/responsibilities");
    const dialog = await openSetup();
    await fillBasics(dialog);
    await choose(dialog, "Owner", "Rahul Sharma (OPS)");
    fireEvent.change(within(dialog).getByLabelText("Time (IST)"), { target: { value: "10:00" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Birthday wish was set up. Today's task was generated for them now.")).toBeInTheDocument();
  });
});

describe("Existing responsibilities (Phase A)", () => {
  it("flags missing owner and schedule, and adds a schedule", async () => {
    const posts: { url: string; body: unknown }[] = [];
    const bare = responsibility({ id: 4, code: "BIRTHDAY_WISH", name: "Birthday wish", active_schedule_count: 0 });
    server.use(signedInAs("Admin"), ...lookups([bare, responsibility()], posts, () => HttpResponse.json(schedule(), { status: 201 })));
    renderApp("/responsibilities");
    const table = await screen.findByRole("table", { name: "Responsibilities" });
    const row = (await within(table).findByText("Birthday wish")).closest("tr") as HTMLElement;
    expect(within(row).getByText("No owner")).toBeInTheDocument();
    expect(within(row).getByText("No schedule")).toBeInTheDocument();
    const other = within(table).getByText("Feed Upload").closest("tr") as HTMLElement;
    expect(within(other).getByText("1 active")).toBeInTheDocument();

    await userEvent.click(within(row).getByRole("button", { name: "Add schedule" }));
    const dialog = await screen.findByRole("dialog", { name: "Add schedule — Birthday wish" });
    expect(within(dialog).getByRole("textbox", { name: "Schedule title" })).toHaveValue("Birthday wish");
    fireEvent.change(within(dialog).getByLabelText("Time (IST)"), { target: { value: "09:30" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Add schedule" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({
      url: "/api/v1/recurring-schedules/",
      body: { responsibility: 4, title: "Birthday wish", frequency: "DAILY", run_time: "09:30", day_of_month: null,
        non_working_day_policy: "SKIP", effective_from: todayIST(), effective_to: null },
    });
  });

  it("shows management actions only where the server allows them", async () => {
    server.use(signedInAs("Operations Manager"), ...lookups([responsibility({ can_manage: false })], []));
    renderApp("/responsibilities");
    const table = await screen.findByRole("table", { name: "Responsibilities" });
    await within(table).findByText("Feed Upload");
    for (const name of ["Edit", "Change owner", "Add schedule"]) {
      expect(within(table).queryByRole("button", { name })).not.toBeInTheDocument();
    }
    expect(within(table).getByRole("button", { name: "History" })).toBeInTheDocument();
  });
});

describe("Schedule editing (Phase A)", () => {
  it.each(["HR", "Operations Manager"] as const)("%s edits schedules the server marks as manageable", async (role) => {
    server.use(
      signedInAs(role),
      http.get("*/api/v1/recurring-schedules/", () => HttpResponse.json([
        schedule(), schedule({ id: 8, title: "Other department", can_manage: false }),
      ])),
    );
    renderApp("/schedules");
    const table = await screen.findByRole("table", { name: "Recurring schedules" });
    const mine = (await within(table).findByText("Feed Upload", { selector: "strong" })).closest("tr") as HTMLElement;
    expect(within(mine).getByRole("button", { name: "Edit" })).toBeInTheDocument();
    const other = within(table).getByText("Other department").closest("tr") as HTMLElement;
    expect(within(other).queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });
});
