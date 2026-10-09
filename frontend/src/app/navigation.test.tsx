import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RoleName } from "../api/types";
import { server, signedInAs } from "../test/server";
import { renderApp } from "../test/utils";

const EXPECTED: Record<RoleName, string[]> = {
  Employee: ["Home", "My profile", "Tasks", "My overdue cases"],
  "Operations Manager": ["Home", "My profile", "Tasks", "My overdue cases", "Overdue review queue", "Responsibilities", "Schedules", "Employees", "Operations monitor"],
  HR: ["Home", "My profile", "Tasks", "My overdue cases", "Overdue review queue", "Responsibilities", "Schedules", "Employees", "Audit log"],
  Admin: ["Home", "My profile", "Tasks", "My overdue cases", "Overdue review queue", "Responsibilities", "Schedules", "Employees", "Departments", "Users", "Audit log", "Company calendar", "Operations monitor", "Command center"],
};

describe("Navigation per role (F3)", () => {
  for (const [role, items] of Object.entries(EXPECTED) as [RoleName, string[]][]) {
    it(`${role} sees ${items.join(", ")}`, async () => {
      server.use(signedInAs(role));
      renderApp("/");
      const nav = await screen.findByRole("navigation", { name: "Main" });
      const labels = within(nav).getAllByRole("link").map((link) => link.textContent);
      expect(labels).toEqual(items);
    });
  }
});
