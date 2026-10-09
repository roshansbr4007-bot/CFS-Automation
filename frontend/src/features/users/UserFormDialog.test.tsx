import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { server, signedInAs } from "../../test/server";
import { renderWithProviders } from "../../test/utils";
import { UserFormDialog } from "./UserFormDialog";

describe("User dialog (F4)", () => {
  it("shows API field errors next to the right fields", async () => {
    server.use(
      signedInAs("Admin"),
      http.post("*/api/v1/users/", () =>
        HttpResponse.json(
          {
            code: "validation_error",
            message: "Some fields are not valid.",
            fields: { password: ["This password is too common."] },
          },
          { status: 400 },
        ),
      ),
    );
    renderWithProviders(<UserFormDialog open onClose={() => {}} />);
    await userEvent.type(screen.getByLabelText(/email/i), "new@example.com");
    await userEvent.type(screen.getByLabelText(/^password/i), "password");
    await userEvent.click(screen.getByRole("button", { name: "Add user" }));
    expect(await screen.findByText("This password is too common.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a conflict as a general message", async () => {
    server.use(
      signedInAs("Admin"),
      http.post("*/api/v1/users/", () =>
        HttpResponse.json(
          { code: "email_taken", message: "A user with this email already exists.", fields: {} },
          { status: 409 },
        ),
      ),
    );
    renderWithProviders(<UserFormDialog open onClose={() => {}} />);
    await userEvent.type(screen.getByLabelText(/email/i), "dup@example.com");
    await userEvent.type(screen.getByLabelText(/^password/i), "Ledger-Sandstone-2026");
    await userEvent.click(screen.getByRole("button", { name: "Add user" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already exists");
  });
});
