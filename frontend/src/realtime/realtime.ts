/** Phase 8 real-time foundation: the server event envelope and connection helpers.
 * Events are update SIGNALS only; REST stays the source of truth. */

export interface RealtimeEvent {
  type: "event";
  event: string; // dotted lower-case name, e.g. "system.test"
  timestamp: string; // ISO-8601 with offset
  data: Record<string, unknown>;
}

export type RealtimeStatus = "connecting" | "open" | "reconnecting" | "closed";

/** Capped exponential backoff: 1 s, 2 s, 4 s ... up to 30 s; stop after 10 failed attempts. */
export const RECONNECT = { baseMs: 1_000, maxMs: 30_000, maxAttempts: 10 } as const;

export function reconnectDelay(attempt: number): number {
  return Math.min(RECONNECT.maxMs, RECONNECT.baseMs * 2 ** attempt);
}

export function realtimeUrl(location: Pick<Location, "protocol" | "host"> = window.location): string {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/events/`;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A validated envelope, or null for anything malformed (never throws). */
export function parseRealtimeMessage(raw: unknown): RealtimeEvent | null {
  if (typeof raw !== "string") return null;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isObject(value) || value.type !== "event") return null;
  if (typeof value.event !== "string" || typeof value.timestamp !== "string" || !isObject(value.data)) return null;
  return { type: "event", event: value.event, timestamp: value.timestamp, data: value.data };
}
