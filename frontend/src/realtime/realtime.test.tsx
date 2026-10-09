import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { server, signedInAs } from "../test/server";
import { renderApp } from "../test/utils";
import { parseRealtimeMessage, realtimeUrl, reconnectDelay, RECONNECT } from "./realtime";
import { RealtimeProvider, useRealtime, useRealtimeEvent } from "./RealtimeProvider";

/** A controllable stand-in for the browser WebSocket. */
class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 0;
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  send() {}
  close() {
    this.closed = true;
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  receive(data: unknown) {
    this.onmessage?.({ data });
  }
  drop() {
    this.readyState = 3;
    this.onclose?.();
  }
}

const envelope = (event: string, data: Record<string, unknown> = {}) =>
  JSON.stringify({ type: "event", event, timestamp: "2026-10-05T04:30:00+00:00", data });
const latest = () => FakeSocket.instances[FakeSocket.instances.length - 1];
const inert = globalThis.WebSocket; // the inert stub installed by src/test/setup.ts

function Probe({ onEvent }: { onEvent: (data: Record<string, unknown>) => void }) {
  const { status } = useRealtime();
  useRealtimeEvent("system.test", (e) => onEvent(e.data));
  return <p>status: {status}</p>;
}

beforeEach(() => {
  FakeSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeSocket);
});
afterEach(() => {
  vi.useRealTimers();
  vi.stubGlobal("WebSocket", inert);
});

describe("realtime helpers", () => {
  it("accepts only well-formed envelopes", () => {
    expect(parseRealtimeMessage(envelope("system.test", { n: 1 }))).toEqual({
      type: "event", event: "system.test", timestamp: "2026-10-05T04:30:00+00:00", data: { n: 1 },
    });
    for (const bad of ["not json", "[1,2]", "null", JSON.stringify({ type: "event" }),
      JSON.stringify({ type: "other", event: "x.y", timestamp: "t", data: {} }),
      JSON.stringify({ type: "event", event: "x.y", timestamp: "t", data: [1] }), 42, undefined]) {
      expect(parseRealtimeMessage(bad)).toBeNull();
    }
  });

  it("builds the socket URL from the page and backs off with a cap", () => {
    expect(realtimeUrl({ protocol: "http:", host: "localhost:5173" })).toBe("ws://localhost:5173/ws/events/");
    expect(realtimeUrl({ protocol: "https:", host: "cfs.example" })).toBe("wss://cfs.example/ws/events/");
    expect([0, 1, 2, 3].map(reconnectDelay)).toEqual([1000, 2000, 4000, 8000]);
    expect(reconnectDelay(10)).toBe(RECONNECT.maxMs);
  });
});

describe("RealtimeProvider", () => {
  it("connects once, reports its state and delivers only valid matching events", () => {
    const seen = vi.fn();
    const failing = vi.fn(() => { throw new Error("listener bug"); });
    render(
      <RealtimeProvider>
        <Probe onEvent={failing} />
        <Probe onEvent={seen} />
      </RealtimeProvider>,
    );
    expect(FakeSocket.instances).toHaveLength(1);
    expect(latest().url).toBe(realtimeUrl());
    expect(screen.getAllByText("status: connecting")).toHaveLength(2);
    act(() => latest().open());
    expect(screen.getAllByText("status: open")).toHaveLength(2);
    act(() => {
      latest().receive("not json");
      latest().receive(envelope("task.updated", { id: 1 })); // another event name
      latest().receive(JSON.stringify({ type: "event", event: "system.test" })); // malformed
      latest().receive(envelope("system.test", { n: 1 }));
    });
    expect(failing).toHaveBeenCalledTimes(1); // its error did not stop delivery
    expect(seen).toHaveBeenCalledTimes(1);
    expect(seen).toHaveBeenCalledWith({ n: 1 });
  });

  it("reconnects with backoff and resets after a successful connection", () => {
    vi.useFakeTimers();
    render(<RealtimeProvider><Probe onEvent={() => undefined} /></RealtimeProvider>);
    act(() => latest().open());
    act(() => latest().drop());
    expect(screen.getByText("status: reconnecting")).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(999); });
    expect(FakeSocket.instances).toHaveLength(1);
    act(() => { vi.advanceTimersByTime(1); });
    expect(FakeSocket.instances).toHaveLength(2);
    act(() => latest().open());
    expect(screen.getByText("status: open")).toBeInTheDocument();
    act(() => latest().drop());
    act(() => { vi.advanceTimersByTime(1000); }); // back to the first delay after a success
    expect(FakeSocket.instances).toHaveLength(3);
  });

  it("gives up after the maximum number of failed attempts", () => {
    vi.useFakeTimers();
    render(<RealtimeProvider><Probe onEvent={() => undefined} /></RealtimeProvider>);
    for (let attempt = 0; attempt < RECONNECT.maxAttempts; attempt += 1) {
      act(() => latest().drop());
      act(() => { vi.advanceTimersByTime(reconnectDelay(attempt)); });
    }
    expect(FakeSocket.instances).toHaveLength(RECONNECT.maxAttempts + 1);
    act(() => latest().drop());
    expect(screen.getByText("status: closed")).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(10 * 60_000); });
    expect(FakeSocket.instances).toHaveLength(RECONNECT.maxAttempts + 1); // no loop
  });

  it("closes the socket on unmount and does not reconnect", () => {
    vi.useFakeTimers();
    const { unmount } = render(<RealtimeProvider><Probe onEvent={() => undefined} /></RealtimeProvider>);
    act(() => latest().open());
    const socket = latest();
    unmount();
    expect(socket.closed).toBe(true);
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(FakeSocket.instances).toHaveLength(1);
  });
});

describe("RealtimeProvider unmount while connecting", () => {
  it("closes a still-connecting socket as soon as it opens, without state updates", () => {
    const seen = vi.fn();
    const { unmount } = render(<RealtimeProvider><Probe onEvent={seen} /></RealtimeProvider>);
    const socket = latest();
    unmount(); // still CONNECTING
    expect(socket.closed).toBe(false);
    socket.open(); // the server answers after unmount
    expect(socket.closed).toBe(true);
    socket.receive(envelope("system.test", { n: 1 }));
    expect(seen).not.toHaveBeenCalled();
    expect(FakeSocket.instances).toHaveLength(1);
  });
});

describe("app wiring", () => {
  it("opens one socket for a signed-in user", async () => {
    server.use(signedInAs("Employee"));
    renderApp("/");
    await screen.findByRole("button", { name: "Sign out" });
    expect(FakeSocket.instances).toHaveLength(1);
    expect(latest().url.endsWith("/ws/events/")).toBe(true);
  });

  it("never connects on the sign-in page", async () => {
    renderApp("/login");
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(FakeSocket.instances).toHaveLength(0);
  });
});
