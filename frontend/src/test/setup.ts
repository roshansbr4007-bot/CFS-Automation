import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";

import { server } from "./server";

// jsdom has no matchMedia; report a desktop-width screen so the sidebar is rendered.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: query.includes("min-width"),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// Phase 8: jsdom ships a real WebSocket; tests must never open network connections. This inert
// stand-in never connects (realtime tests install their own controllable fake).
class InertWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {}
  send() {}
  close() { this.readyState = 3; }
  addEventListener() {}
  removeEventListener() {}
}
vi.stubGlobal("WebSocket", InertWebSocket);

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
  vi.stubGlobal("WebSocket", InertWebSocket);
});
afterEach(() => {
  server.resetHandlers();
  cleanup();
});
afterAll(() => server.close());