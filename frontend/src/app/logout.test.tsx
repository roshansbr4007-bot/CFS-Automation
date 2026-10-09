import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { anonymous, makeMe, server } from "../test/server";
import { renderApp } from "../test/utils";

/** A server whose session ends when /auth/logout/ is called (like Django's logout). */
function sessionServer() {
  const state = { signedIn: true, logoutCalls: 0, csrfHeader: null as string | null };
  server.use(
    http.get("*/api/v1/auth/me/", () => (state.signedIn ? HttpResponse.json(makeMe("Employee")) : anonymous())),
    http.post("*/api/v1/auth/logout/", ({ request }) => {
      state.logoutCalls += 1;
      state.csrfHeader = request.headers.get("X-CSRFToken");
      state.signedIn = false;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  return state;
}

describe("Sign out", () => {
  it("ends the session, clears the user and lands on the sign-in page (regression)", async () => {
    const state = sessionServer();
    renderApp("/");
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    // Before the fix the old user stayed in context and the login page bounced back into the app.
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
    expect(state.logoutCalls).toBe(1);
    expect(state.csrfHeader).not.toBeNull(); // unsafe request carries the CSRF header
  });

  it("does not come back on a fresh load, and protected pages send you to sign in", async () => {
    const state = sessionServer();
    renderApp("/");
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    await screen.findByRole("button", { name: "Sign in" });
    expect(state.signedIn).toBe(false);
    // A browser refresh = the old app is gone and a new one (empty cache) asks the server who is
    // signed in. Opening a protected page directly must land on sign-in.
    cleanup();
    renderApp("/tasks");
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });
});
