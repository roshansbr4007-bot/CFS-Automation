import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { server, signedInAs } from "../test/server";
import { renderApp } from "../test/utils";

describe("ProtectedRoute (F2)", () => {
  it("sends anonymous visitors to sign in", async () => {
    renderApp("/admin/users");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("sends users without the permission to the access page", async () => {
    server.use(signedInAs("Employee"));
    renderApp("/admin/users");
    expect(
      await screen.findByRole("heading", { name: "You don't have access to this page" }),
    ).toBeInTheDocument();
  });

  it("lets an Admin open Users", async () => {
    server.use(signedInAs("Admin"));
    renderApp("/admin/users");
    expect(await screen.findByRole("heading", { name: "Users" })).toBeInTheDocument();
  });
});
