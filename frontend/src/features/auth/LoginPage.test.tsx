import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { makeMe, server } from "../../test/server";
import { renderApp } from "../../test/utils";

describe("Login (F1)", () => {
  it("shows the API error message on failure", async () => {
    server.use(
      http.post("*/api/v1/auth/login/", () =>
        HttpResponse.json(
          { code: "invalid_credentials", message: "Email or password is incorrect.", fields: {} },
          { status: 400 },
        ),
      ),
    );
    renderApp("/login");
    await userEvent.type(await screen.findByLabelText(/email/i), "asha@example.com");
    await userEvent.type(screen.getByLabelText(/password/i), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");
  });

  it("signs in and lands on the home page", async () => {
    server.use(
      http.post("*/api/v1/auth/login/", () => HttpResponse.json(makeMe("Employee"))),
    );
    renderApp("/login");
    await userEvent.type(await screen.findByLabelText(/email/i), "asha@example.com");
    await userEvent.type(screen.getByLabelText(/password/i), "Ledger-Sandstone-2026");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Welcome, Asha" })).toBeInTheDocument();
  });
});
