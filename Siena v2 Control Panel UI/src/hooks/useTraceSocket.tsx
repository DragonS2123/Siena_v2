import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { sienaClient, TRACE_WS_URL } from "../api/sienaClient";
import type { TraceEvent } from "../api/types";

const MAX_EVENTS = 200;
const RECONNECT_DELAY_MS = 2000;

interface TraceSocketContextValue {
  events: TraceEvent[];
  connected: boolean;
}

const TraceSocketContext = createContext<TraceSocketContextValue | null>(null);

/**
 * Single app-wide GET /api/trace/recent + single /ws/trace WebSocket for the
 * whole app. Every screen that needs the trace stream (Inspector, Tool
 * Trace, Debug) reads this same context instead of opening its own socket —
 * previously three call sites each opened an independent WS connection.
 */
export function TraceSocketProvider({ children }: { children: ReactNode }) {
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    sienaClient
      .getRecentTrace(100)
      .then((data) => {
        if (!cancelled) setEvents(data.events);
      })
      .catch(() => {
        // Non-fatal — live socket events will still arrive once connected.
      });

    const connect = () => {
      if (cancelled) return;
      ws = new WebSocket(TRACE_WS_URL);

      ws.onopen = () => setConnected(true);

      ws.onclose = () => {
        setConnected(false);
        if (!cancelled) reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS);
      };

      ws.onerror = () => ws?.close();

      ws.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data) as TraceEvent;
          setEvents((prev) => [...prev.slice(-(MAX_EVENTS - 1)), event]);
        } catch {
          // Ignore malformed frames — the trace stream is best-effort UI data.
        }
      };
    };
    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, []);

  return (
    <TraceSocketContext.Provider value={{ events, connected }}>{children}</TraceSocketContext.Provider>
  );
}

export function useTraceSocket(): TraceSocketContextValue {
  const ctx = useContext(TraceSocketContext);
  if (!ctx) throw new Error("useTraceSocket() must be used within a TraceSocketProvider");
  return ctx;
}
