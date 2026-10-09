import {
  screen,
  waitFor,
  waitForElementToBeRemoved,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Responsibility } from "../../api/types";
import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";

const OPS = { id: 1, code: "OPS", name: "Operations" };
const RAHUL = { id: 10, full_name: "Rahul Sharma", department: OPS };

function responsibility(overrides: Partial<Responsibility> = {}): Responsibility {
  return {
    id: 1, code: "FEED_UPLOAD", name: "Feed Upload", description: "", department: OPS,
    category: { id: 3, code: "OPERATIONS", name: "Operations" }, template: null, priority: "MEDIUM",
    is_active: true, current_owner: null, version: 1, created_at: "", updated_at: "",
    active_schedule_count: 1, can_manage: true, ...overrides,
  };
}

describe("Responsibilities", () => {
  it("lists responsibilities, assigns an owner and shows the history", async () => {
    let assigned: Record<string, unknown> | null = null;
    server.use(
      signedInAs("Operations Manager"),
      http.get("*/api/v1/responsibilities/", () => HttpResponse.json([responsibility()])),
      http.get("*/api/v1/employees/", () => HttpResponse.json({ count: 1, next: null, previous: null, results: [{ ...RAHUL, email: "", is_active: true }] })),
      http.post("*/api/v1/responsibilities/1/owners/", async ({ request }) => {
        assigned = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({}, { status: 201 });
      }),
      http.get("*/api/v1/responsibilities/1/owners/", () => HttpResponse.json([
        { id: 1, employee: RAHUL, effective_from: "2026-10-01", effective_to: "2026-10-09", note: "", assigned_by: { id: 1, email: "", full_name: "" }, created_at: "" },
      ])),
    );
    renderApp("/responsibilities");
    const table = await screen.findByRole("table", { name: "Responsibilities" });
    expect(await within(table).findByText("Feed Upload")).toBeInTheDocument();
    expect(within(table).getByText("No owner")).toBeInTheDocument();

    await userEvent.click(within(table).getByRole("button", { name: "Change owner" }));
    const dialog = await screen.findByRole("dialog", { name: "Owner of Feed Upload" });
    await userEvent.click(within(dialog).getByRole("combobox", { name: "New owner" }));
    await userEvent.click(await screen.findByRole("option", { name: "Rahul Sharma (OPS)" }));
    await userEvent.type(within(dialog).getByLabelText("Owner from"), "2026-10-10");
    await userEvent.click(within(dialog).getByRole("button", { name: "Assign owner" }));
    await waitFor(() => expect(assigned).toEqual({ employee: 10, effective_from: "2026-10-10", note: "" }));
    await waitForElementToBeRemoved(dialog);

    await userEvent.click(within(table).getByRole("button", { name: "History" }));
    const history = await screen.findByRole("dialog", { name: "Ownership history — Feed Upload" });
    expect(await within(history).findByText("09 Oct 2026")).toBeInTheDocument();
  });

  it("lets HR manage responsibilities organisation-wide (Phase A)", async () => {
    server.use(
      signedInAs("HR"),
      http.get("*/api/v1/responsibilities/", () => HttpResponse.json([responsibility({
        current_owner: { id: 1, employee: RAHUL, effective_from: "2026-10-01", effective_to: null, note: "", assigned_by: { id: 1, email: "", full_name: "" }, created_at: "" },
      })])),
    );
    renderApp("/responsibilities");
    const table = await screen.findByRole("table", { name: "Responsibilities" });
    expect(await within(table).findByText("Rahul Sharma")).toBeInTheDocument();
    expect(within(table).getByRole("button", { name: "Change owner" })).toBeInTheDocument();
    expect(within(table).getByRole("button", { name: "Add schedule" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add responsibility" })).toBeInTheDocument();
  });

  it("is not available to an employee", async () => {
    server.use(signedInAs("Employee"));
    renderApp("/responsibilities");
    expect(await screen.findByRole("heading", { name: /don't have access/i })).toBeInTheDocument();
  });
});

describe("Schedules", () => {
  it("shows the occurrence ledger with a result filter", async () => {
    const seen: URL[] = [];
    server.use(
      signedInAs("Admin"),
      http.get("*/api/v1/recurring-schedules/", () => HttpResponse.json([])),
      http.get("*/api/v1/schedule-occurrences/", ({ request }) => {
        seen.push(new URL(request.url));
        return HttpResponse.json({ count: 1, next: null, previous: null, results: [{
          id: 1, schedule: 1, schedule_title: "Feed Upload", responsibility: { id: 1, code: "FEED_UPLOAD", name: "Feed Upload" },
          occurrence_date: "2026-10-05", status: "SKIPPED", task: null, assignee: null, generated_at: null,
          detail: "No responsible employee is configured for this date.", created_at: "",
        }] });
      }),
    );
    renderApp("/schedules");
    await userEvent.click(await screen.findByRole("tab", { name: "Occurrences" }));
    const table = await screen.findByRole("table", { name: "Occurrences" });
    expect(await within(table).findByText("SKIPPED")).toBeInTheDocument();
    expect(within(table).getByText("No responsible employee is configured for this date.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("combobox", { name: "Result" }));
    await userEvent.click(await screen.findByRole("option", { name: "MISSED" }));
    await waitFor(() => expect(seen.at(-1)?.searchParams.get("status")).toBe("MISSED"));
  });
});

describe("Company calendar", () => {
  it("lets Admin add and remove holidays", async () => {
    let added: Record<string, unknown> | null = null;
    let removed = false;
    server.use(
      signedInAs("Admin"),
      http.get("*/api/v1/calendars/company/", () => HttpResponse.json({
        code: "COMPANY", name: "Company Calendar", weekly_off_weekdays: [6], saturday_working_occurrences: [1, 3],
        days: [{ id: 7, date: "2026-10-20", kind: "HOLIDAY", name: "Diwali", created_at: "" }],
      })),
      http.post("*/api/v1/calendars/company/days/", async ({ request }) => {
        added = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({}, { status: 201 });
      }),
      http.delete("*/api/v1/calendars/company/days/7/", () => { removed = true; return new HttpResponse(null, { status: 204 }); }),
    );
    renderApp("/admin/calendar");
    const table = await screen.findByRole("table", { name: "Holidays and special working days" });
    expect(await within(table).findByText("Diwali")).toBeInTheDocument();
    await userEvent.click(within(table).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(removed).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "Add day" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Date"), "2026-11-09");
    await userEvent.type(within(dialog).getByLabelText("Name"), "Govardhan Puja");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(added).toEqual({ date: "2026-11-09", kind: "HOLIDAY", name: "Govardhan Puja" }));
  });
});
