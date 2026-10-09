import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { parseRealtimeMessage, realtimeUrl, reconnectDelay, RECONNECT, type RealtimeEvent, type RealtimeStatus } from "./realtime";

type Listener = (event: RealtimeEvent) => void;

interface RealtimeContextValue {
  status: RealtimeStatus;
  /** Register a listener for every valid server event; returns the unsubscribe function. */
  subscribe: (listener: Listener) => () => void;
}

const RealtimeContext = createContext<RealtimeContextValue>({ status: "closed", subscribe: () => () => undefined });

/** One WebSocket per signed-in session (mounted with the protected layout). The browser only
 * receives: the server decides who gets what from the session cookie. Reconnects with capped
 * backoff, stops after RECONNECT.maxAttempts failures, and closes cleanly on unmount (sign-out). */
export function RealtimeProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<RealtimeStatus>("connecting");
  const listeners = useRef(new Set<Listener>());

  useEffect(() => {
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    let stopped = false;

    const connect = () => {
      socket = new WebSocket(realtimeUrl());
      socket.onopen = () => {
        failures = 0;
        setStatus("open");
      };
      socket.onmessage = (message: MessageEvent) => {
        const event = parseRealtimeMessage(message.data);
        if (!event) return; // malformed frames are ignored
        listeners.current.forEach((listener) => {
          try {
            listener(event);
          } catch {
            // one faulty listener must not stop delivery to the others
          }
        });
      };
      socket.onclose = () => {
        socket = null;
        if (stopped) return;
        if (failures >= RECONNECT.maxAttempts) {
          setStatus("closed");
          return;
        }
        setStatus("reconnecting");
        timer = setTimeout(connect, reconnectDelay(failures));
        failures += 1;
      };
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      const current = socket;
      if (!current) return;
      current.onclose = null;
      current.onmessage = null;
      if (current.readyState === 0) {
        // Still CONNECTING (e.g. React StrictMode's dev re-mount): close once it opens, so the
        // browser does not log "WebSocket is closed before the connection is established".
        current.onopen = () => current.close();
      } else {
        current.close();
      }
    };
  }, []);

  const subscribe = useCallback((listener: Listener) => {
    listeners.current.add(listener);
    return () => {
      listeners.current.delete(listener);
    };
  }, []);
  const value = useMemo(() => ({ status, subscribe }), [status, subscribe]);
  return <RealtimeContext.Provider value={value}>{children}</RealtimeContext.Provider>;
}

export function useRealtime(): RealtimeContextValue {
  return useContext(RealtimeContext);
}

/** Run `handler` for each server event named `event` (e.g. to re-fetch REST data). */
export function useRealtimeEvent(event: string, handler: Listener): void {
  const { subscribe } = useRealtime();
  const latest = useRef(handler);
  latest.current = handler;
  useEffect(() => subscribe((e) => {
    if (e.event === event) latest.current(e);
  }), [subscribe, event]);
}
