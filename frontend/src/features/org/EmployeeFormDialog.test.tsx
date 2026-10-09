import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Employee } from "../../api/types";
import { server, signedInAs } from "../../test/server";
import { renderWithProviders } from "../../test/utils";
import { EmployeeFormDialog } from "./EmployeeFormDialog";

const employee: Employee = {
  id: 7, employee_code: "CFS-007", full_name: "Ravi Sharma", email: "ravi@example.com",
  department: { id: 1, code: "OPS", name: "Operations" }, reporting_manager: null,
  designation: "Operations Executive", date_of_joining: null, is_active: true, user: null,
  version: 3, created_at: "2026-10-01T10:00:00+05:30", updated_at: "2026-10-01T10:00:00+05:30",
};

function lists() {
  return [
    http.get("*/api/v1/departments/", () =>
      HttpResponse.json([{ id: 1, code: "OPS", name: "Operations", is_live: true,
        created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }]),
    ),
    http.get("*/api/v1/employees/", () =>
      HttpResponse.json({ count: 0, next: null, previous: null, results: [] }),
    ),
  ];
}

describe("Employee dialog: date of joining (Phase 7.1)", () => {
  it("sends the joining date and clears it again", async () => {
    const bodies: unknown[] = [];
    server.use(
      signedInAs("HR"),
      ...lists(),
      http.patch("*/api/v1/employees/7/", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ ...employee, version: 4 });
      }),
    );
    const { unmount } = renderWithProviders(
      <EmployeeFormDialog open employee={employee} canEdit onClose={() => {}} />,
    );
    fireEvent.change(screen.getByLabelText("Date of joining"), { target: { value: "2026-04-15" } });
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ version: 3, date_of_joining: "2026-04-15" });
    unmount();

    renderWithProviders(
      <EmployeeFormDialog open employee={{ ...employee, date_of_joining: "2026-04-15" }} canEdit
        onClose={() => {}} />,
    );
    const field = screen.getByLabelText("Date of joining");
    expect(field).toHaveValue("2026-04-15");
    fireEvent.change(field, { target: { value: "" } });
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1]).toMatchObject({ date_of_joining: null });
  });

  it("shows the joining date to viewers who cannot edit", async () => {
    server.use(signedInAs("Operations Manager"), ...lists());
    renderWithProviders(
      <EmployeeFormDialog open employee={{ ...employee, date_of_joining: "2025-07-01" }}
        canEdit={false} onClose={() => {}} />,
    );
    expect(await screen.findByText("Joined: 2025-07-01")).toBeInTheDocument();
  });
});
