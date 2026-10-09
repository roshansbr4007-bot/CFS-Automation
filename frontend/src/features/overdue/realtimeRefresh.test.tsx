import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RealtimeProvider } from "../../realtime/RealtimeProvider";
import { overdueKeys } from "./queries";
import { OVERDUE_EVENTS, OverdueRealtimeRefresh } from "./realtimeRefresh";
import { reportKey } from "./reportApi";
import { makeCase, page } from "./testData";

/** A controllable stand-in for the browser WebSocket (same approach as the Phase 8 tests). */
class FakeSocket {
  static last: FakeSocket | null = null;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { FakeSocket.last = this; }
  send() {}
  close() { this.readyState = 3; }
}
const inert = globalThis.WebSocket; // the inert stub from src/test/setup.ts

const deliver = (event: string, data: Record<string, unknown>) => act(() => {
  FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type: "event", event, timestamp: "2026-10-05T06:00:00+00:00", data }) });
});

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(overdueKeys.mine({}), page([makeCase()]));
  qc.setQueryData(overdueKeys.reviewQueue({}), page([]));
  qc.setQueryData(reportKey({ page: 1 }), page([makeCase()]));
  qc.setQueryData(overdueKeys.detail(5), makeCase());
  qc.setQueryData(overdueKeys.detail(6), makeCase({ id: 6 }));
  qc.setQueryData(["tasks", {}], page([]));
  render(<QueryClientProvider client={qc}><RealtimeProvider><OverdueRealtimeRefresh /></RealtimeProvider></QueryClientProvider>);
  act(() => FakeSocket.last?.onopen?.());
  return { qc, invalidated: (key: readonly unknown[]) => qc.getQueryState(key)?.isInvalidated };
}

beforeEach(() => { FakeSocket.last = null; vi.stubGlobal("WebSocket", FakeSocket); });
afterEach(() => vi.stubGlobal("WebSocket", inert));

describe("overdue realtime refresh (Stage 5.4)", () => {
  it.each(OVERDUE_EVENTS)("%s refreshes the overdue lists, the report and that case only", (event) => {
    const { invalidated } = mount();
    deliver(event, { case_id: 5, task_id: 9, status: "REASON_SUBMITTED" });
    expect(invalidated(overdueKeys.mine({}))).toBe(true);
    expect(invalidated(overdueKeys.reviewQueue({}))).toBe(true);
    expect(invalidated(reportKey({ page: 1 }))).toBe(true);
    expect(invalidated(overdueKeys.detail(5))).toBe(true);
    expect(invalidated(overdueKeys.detail(6))).toBe(false); // another case is untouched
    expect(invalidated(["tasks", {}])).toBe(false); // unrelated data is untouched
  });

  it("never writes the payload into the cache (REST stays the source of truth)", () => {
    const { qc } = mount();
    deliver("overdue_case.reviewed", { case_id: 5, task_id: 9, status: "REVIEWED" });
    expect(qc.getQueryData(overdueKeys.detail(5))).toEqual(makeCase()); // still the REST copy
  });

  it("ignores other events and refreshes only the lists without a usable case id", () => {
    const { invalidated } = mount();
    deliver("system.test", { message: "hello" });
    deliver("task.updated", { case_id: 5 });
    expect(invalidated(overdueKeys.mine({}))).toBe(false);
    expect(invalidated(overdueKeys.detail(5))).toBe(false);
    deliver("overdue_case.opened", { case_id: "5" });
    expect(invalidated(overdueKeys.mine({}))).toBe(true);
    expect(invalidated(overdueKeys.detail(5))).toBe(false); // a non-numeric id selects nothing
  });

  it("uses the existing single socket", () => {
    mount();
    expect(FakeSocket.last?.url.endsWith("/ws/events/")).toBe(true);
  });
});
