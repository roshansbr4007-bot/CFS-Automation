import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";
import { SUBMITTED, overdueHandlers, page, type Seen } from "./testData";

const queued = { ...SUBMITTED, can_review: true };

describe("Overdue review queue", () => {
  it("lists the cases waiting for my review", async () => {
    const seen: Seen[] = [];
    server.use(signedInAs("Operations Manager"), ...overdueHandlers({ seen, list: () => HttpResponse.json(page([queued])) }));
    renderApp("/overdue-cases/review");
    const table = await screen.findByRole("table", { name: "Overdue review queue" });
    expect(await within(table).findByText("Rahul Verma")).toBeInTheDocument();
    expect(within(table).getByText("Dependency")).toBeInTheDocument(); // the employee's reason
    expect(within(table).getByText("Reason submitted")).toBeInTheDocument();
    expect(seen[0].url.searchParams.get("reviewable")).toBe("1");
  });

  it("sends only backend-supported filters", async () => {
    const seen: Seen[] = [];
    server.use(signedInAs("HR"), ...overdueHandlers({ seen, list: () => HttpResponse.json(page([queued])) }));
    renderApp("/overdue-cases/review");
    await screen.findByRole("table", { name: "Overdue review queue" });
    await userEvent.click(screen.getByRole("combobox", { name: "Department" }));
    await userEvent.click(await screen.findByRole("option", { name: "OPS" }));
    await userEvent.click(screen.getByRole("combobox", { name: "Priority" }));
    await userEvent.click(await screen.findByRole("option", { name: "High" }));
    await userEvent.click(screen.getByRole("combobox", { name: "Employee reason" }));
    await userEvent.click(await screen.findByRole("option", { name: "Dependency" }));
    await waitFor(() => {
      const last = Object.fromEntries(seen[seen.length - 1].url.searchParams.entries());
      expect(last).toEqual({ department: "1", priority: "HIGH", reason_category: "DEPENDENCY", page: "1", reviewable: "1" });
    });
  });

  it("opens the case detail", async () => {
    server.use(signedInAs("Admin"), ...overdueHandlers({ list: () => HttpResponse.json(page([queued])), detail: () => HttpResponse.json(queued) }));
    renderApp("/overdue-cases/review");
    const table = await screen.findByRole("table", { name: "Overdue review queue" });
    await userEvent.click(await within(table).findByRole("button", { name: "Open" }));
    expect(await screen.findByRole("form", { name: "Record your review" })).toBeInTheDocument();
  });

  it("is not available to an employee", async () => {
    server.use(signedInAs("Employee"), ...overdueHandlers({}));
    renderApp("/overdue-cases/review");
    expect(await screen.findByRole("heading", { name: /don't have access/i })).toBeInTheDocument();
  });
});
