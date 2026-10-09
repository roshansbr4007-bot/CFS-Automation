import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";
import { REVIEWED, SUBMITTED, makeCase, overdueHandlers, type Seen } from "./testData";

const posts = (seen: Seen[]) => seen.filter((s) => s.method === "POST");

async function choose(label: string, option: string) {
  await userEvent.click(screen.getByRole("combobox", { name: label }));
  await userEvent.click(await screen.findByRole("option", { name: option }));
}

describe("Overdue case detail", () => {
  it("shows a loading state", async () => {
    server.use(
      signedInAs("Employee"),
      http.get("*/api/v1/overdue-cases/:id/", async () => { await delay("infinite"); return HttpResponse.json(makeCase()); }),
      ...overdueHandlers({}),
    );
    renderApp("/overdue-cases/5");
    expect(await screen.findByText("Loading…")).toBeInTheDocument();
  });

  it("shows an error state (e.g. not visible to me)", async () => {
    server.use(signedInAs("Employee"), ...overdueHandlers({ detail: () => HttpResponse.json({ code: "not_found", message: "Not found.", fields: {} }, { status: 404 }) }));
    renderApp("/overdue-cases/5");
    expect(await screen.findByText("Not found.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to my overdue cases" })).toBeInTheDocument();
  });

  it("renders every fact, with the employee's reason and the reviewer's decision kept apart", async () => {
    server.use(signedInAs("HR"), ...overdueHandlers({ detail: () => HttpResponse.json(REVIEWED) }));
    renderApp("/overdue-cases/5");
    expect(await screen.findByRole("heading", { name: "Map RM codes", level: 1 })).toBeInTheDocument();
    const task = screen.getByRole("region", { name: "Task" });
    expect(within(task).getByText("Rahul Verma (CFS-042)")).toBeInTheDocument();
    expect(within(task).getByText("OPS — Operations")).toBeInTheDocument();
    expect(within(task).getByText("ops.manager@example.com")).toBeInTheDocument();
    expect(within(task).getByRole("link", { name: "T-000009" })).toHaveAttribute("href", "/tasks/9");
    const overdue = screen.getByRole("region", { name: "Overdue" });
    expect(within(overdue).getByText("One hour (ONE_HOUR)")).toBeInTheDocument();
    expect(within(overdue).getByText("Not applicable")).toBeInTheDocument();
    expect(within(overdue).getByText(/1 h 15 min/)).toBeInTheDocument();
    const reason = screen.getByRole("region", { name: "Employee's reason" });
    expect(within(reason).getByText("Dependency")).toBeInTheDocument();
    expect(within(reason).getByText("Waiting for the RTA file.")).toBeInTheDocument();
    const decision = screen.getByRole("region", { name: "Reviewer's decision" });
    expect(within(decision).getByText("System")).toBeInTheDocument();
    expect(within(decision).getByText("RTA portal outage confirmed.")).toBeInTheDocument();
    expect(screen.getByText("Reviewed. This case is closed and can no longer change.")).toBeInTheDocument();
  });

  // --- employee reason ---------------------------------------------------------------------------

  it("offers the reason form only when the server allows it, and validates it", async () => {
    const seen: Seen[] = [];
    server.use(signedInAs("Employee"), ...overdueHandlers({ seen }));
    renderApp("/overdue-cases/5");
    const form = await screen.findByRole("form", { name: "Submit your reason" });
    expect(screen.queryByRole("form", { name: "Record your review" })).not.toBeInTheDocument();
    await userEvent.click(within(form).getByRole("button", { name: "Submit reason" }));
    expect(await within(form).findByText("Choose a reason category.")).toBeInTheDocument();
    expect(within(form).getByText("An explanation is required.")).toBeInTheDocument();
    await userEvent.type(within(form).getByRole("textbox", { name: "Explanation" }), "   ");
    await userEvent.click(within(form).getByRole("button", { name: "Submit reason" }));
    expect(within(form).getByText("An explanation is required.")).toBeInTheDocument(); // blank is not enough
    expect(posts(seen)).toHaveLength(0);
  });

  it("submits the reason and then shows the server's submitted state, read-only", async () => {
    const seen: Seen[] = [];
    server.use(signedInAs("Employee"), ...overdueHandlers({ seen }));
    renderApp("/overdue-cases/5");
    await screen.findByRole("form", { name: "Submit your reason" });
    await choose("Reason category", "Dependency");
    await userEvent.type(screen.getByRole("textbox", { name: "Explanation" }), "Waiting for the RTA file.");
    await userEvent.click(screen.getByRole("button", { name: "Submit reason" }));
    expect(await screen.findByText("Your reason was submitted.")).toBeInTheDocument();
    expect(posts(seen)[0].url.pathname).toBe("/api/v1/overdue-cases/5/submit/");
    expect(posts(seen)[0].body).toEqual({ version: 1, reason_category: "DEPENDENCY", explanation: "Waiting for the RTA file." });
    const reason = screen.getByRole("region", { name: "Employee's reason" });
    expect(within(reason).getByText("Waiting for the RTA file.")).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Submit your reason" })).not.toBeInTheDocument(); // no edits
    expect(screen.getByText("The reason has been submitted and is waiting for review.")).toBeInTheDocument();
  });

  it("shows a backend validation error on its field", async () => {
    server.use(signedInAs("Employee"), ...overdueHandlers({
      submit: () => HttpResponse.json({ code: "validation_error", message: "Invalid input.", fields: { explanation: ["This field may not be blank."] } }, { status: 400 }),
    }));
    renderApp("/overdue-cases/5");
    await screen.findByRole("form", { name: "Submit your reason" });
    await choose("Reason category", "Other");
    await userEvent.type(screen.getByRole("textbox", { name: "Explanation" }), "x");
    await userEvent.click(screen.getByRole("button", { name: "Submit reason" }));
    expect(await screen.findByText("This field may not be blank.")).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Submit your reason" })).toBeInTheDocument(); // still editable
  });

  // --- review ------------------------------------------------------------------------------------

  it("offers the review form only when the server allows it", async () => {
    server.use(signedInAs("Operations Manager"), ...overdueHandlers({ detail: () => HttpResponse.json(SUBMITTED) }));
    renderApp("/overdue-cases/5");
    expect(await screen.findByText("The reason has been submitted and is waiting for review.")).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Record your review" })).not.toBeInTheDocument(); // can_review false
    expect(screen.queryByRole("form", { name: "Submit your reason" })).not.toBeInTheDocument();
  });

  it("records the authoritative cause and remark and then shows the reviewed, read-only case", async () => {
    const seen: Seen[] = [];
    server.use(signedInAs("Operations Manager"), ...overdueHandlers({ seen, detail: () => HttpResponse.json({ ...SUBMITTED, can_review: true }) }));
    renderApp("/overdue-cases/5");
    const form = await screen.findByRole("form", { name: "Record your review" });
    await userEvent.click(within(form).getByRole("button", { name: "Record review" }));
    expect(await within(form).findByText("Choose the authoritative cause.")).toBeInTheDocument();
    expect(within(form).getByText("A review remark is required.")).toBeInTheDocument();
    expect(posts(seen)).toHaveLength(0);
    await choose("Authoritative cause", "System");
    await userEvent.type(within(form).getByRole("textbox", { name: "Review remark" }), "RTA portal outage confirmed.");
    await userEvent.click(within(form).getByRole("button", { name: "Record review" }));
    expect(await screen.findByText("Your review was recorded. The case is now closed.")).toBeInTheDocument();
    expect(posts(seen)[0].url.pathname).toBe("/api/v1/overdue-cases/5/review/");
    expect(posts(seen)[0].body).toEqual({ version: 2, cause: "SYSTEM", remark: "RTA portal outage confirmed." });
    const decision = screen.getByRole("region", { name: "Reviewer's decision" });
    expect(within(decision).getByText("System")).toBeInTheDocument();
    expect(within(decision).getByText("RTA portal outage confirmed.")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Employee's reason" })).getByText("Dependency")).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Record your review" })).not.toBeInTheDocument();
  });

  it("a reviewed case has no editing controls at all", async () => {
    server.use(signedInAs("Admin"), ...overdueHandlers({ detail: () => HttpResponse.json(REVIEWED) }));
    renderApp("/overdue-cases/5");
    await screen.findByText("Reviewed. This case is closed and can no longer change.");
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /submit reason|record review/i })).not.toBeInTheDocument();
  });

  it("surfaces a backend permission error", async () => {
    server.use(signedInAs("Operations Manager"), ...overdueHandlers({
      detail: () => HttpResponse.json({ ...SUBMITTED, can_review: true }),
      review: () => HttpResponse.json({ code: "permission_denied", message: "You cannot review your own overdue case.", fields: {} }, { status: 403 }),
    }));
    renderApp("/overdue-cases/5");
    await screen.findByRole("form", { name: "Record your review" });
    await choose("Authoritative cause", "Employee");
    await userEvent.type(screen.getByRole("textbox", { name: "Review remark" }), "Self check");
    await userEvent.click(screen.getByRole("button", { name: "Record review" }));
    expect(await screen.findByText("You cannot review your own overdue case.")).toBeInTheDocument();
    expect(screen.queryByText("Your review was recorded. The case is now closed.")).not.toBeInTheDocument();
  });
});
