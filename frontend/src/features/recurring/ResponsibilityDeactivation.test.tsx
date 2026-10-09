import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Responsibility } from "../../api/types";
import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";

const OPS = { id: 1, code: "OPS", name: "Operations" };

function responsibility(overrides: Partial<Responsibility> = {}): Responsibility {
  return {
    id: 1, code: "FEED_UPLOAD", name: "Feed Upload", description: "", department: OPS,
    category: { id: 3, code: "OPERATIONS", name: "Operations" }, template: null, priority: "MEDIUM",
    is_active: true, current_owner: null, version: 4, created_at: "", updated_at: "",
    active_schedule_count: 1, can_manage: true, ...overrides,
  };
}

function handlers(list: Responsibility[], patches: unknown[], reply?: () => Response) {
  return [
    http.get("*/api/v1/responsibilities/", () => HttpResponse.json(list)),
    http.get("*/api/v1/departments/", () => HttpResponse.json([{ ...OPS, is_live: true, created_at: "", updated_at: "" }])),
    http.get("*/api/v1/task-categories/", () => HttpResponse.json([
      { id: 3, code: "OPERATIONS", name: "Operations", is_active: true, created_at: "", updated_at: "" },
    ])),
    http.get("*/api/v1/task-templates/", () => HttpResponse.json([])),
    http.patch("*/api/v1/responsibilities/:id/", async ({ request }) => {
      patches.push(await request.json());
      return reply ? reply() : HttpResponse.json(responsibility({ is_active: false, version: 5 }));
    }),
  ];
}

async function rowOf(name: string) {
  const table = await screen.findByRole("table", { name: "Responsibilities" });
  return (await within(table).findByText(name)).closest("tr") as HTMLElement;
}

describe("Responsibility deactivation (Change Set 1)", () => {
  it("asks for confirmation, then deactivates with the current version", async () => {
    const patches: unknown[] = [];
    server.use(signedInAs("Operations Manager"), ...handlers([responsibility()], patches));
    renderApp("/responsibilities");
    const row = await rowOf("Feed Upload");
    await userEvent.click(within(row).getByRole("button", { name: "Deactivate" }));
    const dialog = await screen.findByRole("dialog", { name: "Deactivate Feed Upload?" });
    expect(within(dialog).getByText(/owners, schedules, generated tasks and history are kept/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(patches).toHaveLength(0); // cancelling changes nothing

    await userEvent.click(within(row).getByRole("button", { name: "Deactivate" }));
    const confirm = await screen.findByRole("dialog", { name: "Deactivate Feed Upload?" });
    await userEvent.click(within(confirm).getByRole("button", { name: "Deactivate" }));
    await waitFor(() => expect(patches).toEqual([{ version: 4, is_active: false }]));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("shows an archived responsibility read-only, without reactivation", async () => {
    server.use(signedInAs("Admin"), ...handlers([responsibility({ is_active: false })], []));
    renderApp("/responsibilities");
    const row = await rowOf("Feed Upload");
    expect(within(row).getByText("Archived")).toBeInTheDocument();
    for (const name of ["Edit", "Change owner", "Add schedule", "Deactivate", "Activate", "Reactivate"]) {
      expect(within(row).queryByRole("button", { name })).not.toBeInTheDocument();
    }
    expect(within(row).getByRole("button", { name: "History" })).toBeInTheDocument();
  });

  it("no longer has an Active switch in the Edit dialog", async () => {
    const patches: unknown[] = [];
    server.use(signedInAs("HR"), ...handlers([responsibility()], patches, () => HttpResponse.json(responsibility({ version: 5 }))));
    renderApp("/responsibilities");
    const row = await rowOf("Feed Upload");
    await userEvent.click(within(row).getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit responsibility" });
    expect(within(dialog).queryByRole("checkbox", { name: "Active" })).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0]).not.toHaveProperty("is_active");
  });

  it("shows the backend's refusal in the confirmation dialog", async () => {
    server.use(signedInAs("Admin"), ...handlers([responsibility()], [], () => HttpResponse.json(
      { code: "version_conflict", message: "This record was changed by someone else. Reload and try again.", fields: {} },
      { status: 409 },
    )));
    renderApp("/responsibilities");
    const row = await rowOf("Feed Upload");
    await userEvent.click(within(row).getByRole("button", { name: "Deactivate" }));
    const dialog = await screen.findByRole("dialog", { name: "Deactivate Feed Upload?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Deactivate" }));
    expect(await within(dialog).findByText("This record was changed by someone else. Reload and try again.")).toBeInTheDocument();
  });
});
