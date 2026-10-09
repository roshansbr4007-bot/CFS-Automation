import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";
import { makeCase, overdueHandlers, page, type Seen } from "./testData";

const lastParams = (seen: Seen[]) => Object.fromEntries(seen[seen.length - 1].url.searchParams.entries());

async function choose(label: string, option: string) {
  await userEvent.click(screen.getByRole("combobox", { name: label }));
  await userEvent.click(await screen.findByRole("option", { name: option }));
}

describe("My overdue cases — filters and pagination (Stage 5.3)", () => {
  it("offers only the supported filters and no export or employee picker", async () => {
    server.use(signedInAs("Employee"), ...overdueHandlers({}));
    renderApp("/overdue-cases");
    const filters = await screen.findByRole("region", { name: "Filters" });
    for (const name of ["Status", "My reason", "Reviewer's cause", "Priority"]) {
      expect(within(filters).getByRole("combobox", { name })).toBeInTheDocument();
    }
    expect(within(filters).getByLabelText("Opened from")).toBeInTheDocument();
    expect(within(filters).getByLabelText("Opened to")).toBeInTheDocument();
    expect(within(filters).queryByRole("combobox", { name: /employee/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /export/i })).not.toBeInTheDocument();
    expect(within(filters).getByRole("button", { name: "Clear filters" })).toBeDisabled();
  });

  it("sends the chosen filters with my employee scope, and clears them", async () => {
    const seen: Seen[] = [];
    server.use(signedInAs("Employee"), ...overdueHandlers({ seen }));
    renderApp("/overdue-cases");
    await screen.findByRole("table", { name: "My overdue cases" });
    await choose("Status", "Reviewed");
    await choose("My reason", "Dependency");
    await choose("Reviewer's cause", "System");
    await choose("Priority", "High");
    fireEvent.change(screen.getByLabelText("Opened from"), { target: { value: "2026-10-01" } });
    fireEvent.change(screen.getByLabelText("Opened to"), { target: { value: "2026-10-31" } });
    await waitFor(() => expect(lastParams(seen)).toEqual({
      status: "REVIEWED", reason_category: "DEPENDENCY", cause: "SYSTEM", priority: "HIGH",
      date_from: "2026-10-01", date_to: "2026-10-31", page: "1", employee: "42",
    }));
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(lastParams(seen)).toEqual({ page: "1", employee: "42" }));
  });

  it("pages through results and returns to page 1 when a filter changes", async () => {
    const errors = vi.spyOn(console, "error");
    const seen: Seen[] = [];
    const many = { count: 30, next: "http://x/?page=2", previous: null, results: [makeCase()] };
    server.use(signedInAs("Employee"), ...overdueHandlers({ seen, list: () => HttpResponse.json(many) }));
    renderApp("/overdue-cases");
    await screen.findByRole("table", { name: "My overdue cases" });
    await userEvent.click(await screen.findByRole("button", { name: "Go to next page" }));
    await waitFor(() => expect(lastParams(seen).page).toBe("2"));
    await choose("Priority", "Low");
    await waitFor(() => expect(lastParams(seen)).toMatchObject({ page: "1", priority: "LOW" }));
    expect(errors.mock.calls.flat().join(" ")).not.toMatch(/out of range/); // page stays within the known total
    errors.mockRestore();
  });

  it("shows loading, filtered-empty and error states", async () => {
    let mode: "wait" | "empty" | "error" = "wait";
    server.use(
      signedInAs("Employee"),
      http.get("*/api/v1/overdue-cases/", async () => {
        if (mode === "wait") { await delay("infinite"); }
        if (mode === "error") return HttpResponse.json({ code: "error", message: "Server is unavailable.", fields: {} }, { status: 500 });
        return HttpResponse.json(page([]));
      }),
      ...overdueHandlers({}),
    );
    renderApp("/overdue-cases");
    // Wait for the page itself: before auth resolves, ProtectedRoute shows its own "Loading" spinner.
    await screen.findByRole("region", { name: "Filters" });
    expect(screen.getByRole("progressbar")).toBeInTheDocument(); // the table's loading bar
    expect(screen.queryByText("You have no overdue cases.")).not.toBeInTheDocument(); // no false empty state
    mode = "empty";
    await choose("Status", "Reason needed");
    expect(await screen.findByText("No overdue cases match the selected filters.")).toBeInTheDocument();
    mode = "error";
    await choose("Status", "Reviewed");
    expect(await screen.findByText("Server is unavailable.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
