import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";
import { REVIEWED, makeCase, overdueHandlers, page, type Seen } from "./testData";

describe("My overdue cases", () => {
  it("lists my own cases (filtered by my employee) with their state", async () => {
    const seen: Seen[] = [];
    server.use(signedInAs("Employee"), ...overdueHandlers({ seen, list: () => HttpResponse.json(page([makeCase(), { ...REVIEWED, id: 6, task_reference: "T-000010" }])) }));
    renderApp("/overdue-cases");
    expect(await screen.findByRole("heading", { name: "My overdue cases" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "My overdue cases" });
    expect(await within(table).findByText("T-000009")).toBeInTheDocument();
    expect(within(table).getByText("Reason needed")).toBeInTheDocument();
    expect(within(table).getByText("Not submitted")).toBeInTheDocument();
    expect(within(table).getAllByText("1 h 15 min")).toHaveLength(2); // both returned cases are 75 min overdue
    expect(within(table).getByText("Reviewed")).toBeInTheDocument(); // the reviewed case
    expect(within(table).getByText("System")).toBeInTheDocument(); // reviewer's cause shown separately
    expect(screen.getByText("1 case needs your reason.")).toBeInTheDocument();
    expect(seen[0].url.searchParams.get("employee")).toBe("42");
  });

  it("shows the empty state", async () => {
    server.use(signedInAs("Employee"), ...overdueHandlers({ list: () => HttpResponse.json(page([])) }));
    renderApp("/overdue-cases");
    expect(await screen.findByText("You have no overdue cases.")).toBeInTheDocument();
  });

  it("shows an API error and can retry", async () => {
    let fail = true;
    server.use(signedInAs("Employee"), ...overdueHandlers({
      list: () => (fail ? HttpResponse.json({ code: "error", message: "Server is unavailable.", fields: {} }, { status: 500 }) : HttpResponse.json(page([makeCase()]))),
    }));
    renderApp("/overdue-cases");
    expect(await screen.findByText("Server is unavailable.")).toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("T-000009")).toBeInTheDocument();
  });

  it("opens the case detail", async () => {
    server.use(signedInAs("Employee"), ...overdueHandlers({}));
    renderApp("/overdue-cases");
    const table = await screen.findByRole("table", { name: "My overdue cases" });
    await userEvent.click(await within(table).findByRole("button", { name: "Open" }));
    expect(await screen.findByRole("heading", { name: "Map RM codes", level: 1 })).toBeInTheDocument();
    expect(screen.getByText(/Overdue case #5/)).toBeInTheDocument();
  });
});
