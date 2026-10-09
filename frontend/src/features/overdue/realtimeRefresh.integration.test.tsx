import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { server, signedInAs } from "../../test/server";
import { renderApp } from "../../test/utils";
import { OVERDUE_EVENTS } from "./realtimeRefresh";
import { overdueHandlers, type Seen } from "./testData";

/** A controllable stand-in for the browser WebSocket; the app opens exactly one. */
class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { FakeSocket.instances.push(this); }
  send() {}
  close() { this.readyState = 3; }
}
const inert = globalThis.WebSocket; // the inert stub from src/test/setup.ts

beforeEach(() => { FakeSocket.instances = []; vi.stubGlobal("WebSocket", FakeSocket); });
afterEach(() => vi.stubGlobal("WebSocket", inert));

describe("overdue realtime refresh, mounted in the app layout (Stage 5.4)", () => {
  it.each(OVERDUE_EVENTS)("%s makes the visible overdue list refetch over REST", async (event) => {
    const seen: Seen[] = [];
    server.use(signedInAs("Employee"), ...overdueHandlers({ seen }));
    renderApp("/overdue-cases");
    const table = await screen.findByRole("table", { name: "My overdue cases" });
    expect(await within(table).findByText("T-000009")).toBeInTheDocument();
    const listCalls = () => seen.filter((s) => s.method === "GET" && s.url.pathname === "/api/v1/overdue-cases/").length;
    expect(listCalls()).toBe(1);
    expect(FakeSocket.instances).toHaveLength(1); // the existing single socket, no new one
    const socket = FakeSocket.instances[0];
    act(() => socket.onopen?.());
    act(() => socket.onmessage?.({
      data: JSON.stringify({ type: "event", event, timestamp: "2026-10-05T06:00:00+00:00", data: { case_id: 5, task_id: 9, status: "OPEN" } }),
    }));
    await waitFor(() => expect(listCalls()).toBe(2)); // refetched from the server
    expect(FakeSocket.instances).toHaveLength(1);
  });
});
