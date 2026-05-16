/**
 * useApprovalsStream — WebSocket hook for /api/approvals/stream live events.
 *
 * Mirrors the shape of a future useSpendStream (§8.3 spec).
 * Connects to the server, replays any snapshot events on connect, then
 * delivers live events to consumers.
 *
 * On each event, calls the provided ``onEvent`` callback and, if the kind
 * matches a state transition (created|approved|rejected|deferred|executed|expired),
 * also invalidates the React Query cache for the approvals list and history.
 *
 * The hook reconnects automatically with exponential back-off (up to 30 s) when
 * the socket drops. Call ``disconnect()`` from the returned handle to stop it.
 */

import { useCallback, useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

export type ApprovalsEventKind =
  | "created"
  | "approved"
  | "rejected"
  | "deferred"
  | "executed"
  | "expired"
  | "ping";

export interface ApprovalsEvent {
  kind: ApprovalsEventKind;
  ts: number;
  approval_id?: string;
  butler?: string;
  tool_name?: string;
  status?: string;
  /** True for events replayed from the server ring buffer on connect. */
  snapshot?: boolean;
  [key: string]: unknown;
}

export interface UseApprovalsStreamOptions {
  /** Called for every incoming event, including snapshot events. */
  onEvent?: (event: ApprovalsEvent) => void;
  /** Optional DASHBOARD_API_KEY for query-param auth. Leave undefined when
   *  the server has no API key configured (dev mode). */
  apiKey?: string;
  /** Disable the hook (no-op when false). Defaults to true. */
  enabled?: boolean;
}

export interface ApprovalsStreamHandle {
  disconnect: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildWsUrl(apiKey?: string): string {
  const apiBase: string = (
    typeof import.meta !== "undefined" ? (import.meta.env?.VITE_API_URL ?? "/api") : "/api"
  ) as string;

  // Convert http(s) base URL to ws(s)
  let wsBase: string;
  if (apiBase.startsWith("http://")) {
    wsBase = "ws://" + apiBase.slice("http://".length);
  } else if (apiBase.startsWith("https://")) {
    wsBase = "wss://" + apiBase.slice("https://".length);
  } else {
    // Relative path — construct from window.location
    const proto = typeof window !== "undefined" && window.location.protocol === "https:" ? "wss" : "ws";
    const host = typeof window !== "undefined" ? window.location.host : "localhost";
    wsBase = `${proto}://${host}${apiBase}`;
  }

  const url = `${wsBase}/approvals/stream`;
  return apiKey ? `${url}?api_key=${encodeURIComponent(apiKey)}` : url;
}

const STATE_TRANSITION_KINDS: ReadonlySet<ApprovalsEventKind> = new Set([
  "created",
  "approved",
  "rejected",
  "deferred",
  "executed",
  "expired",
]);

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useApprovalsStream({
  onEvent,
  apiKey,
  enabled = true,
}: UseApprovalsStreamOptions = {}): ApprovalsStreamHandle {
  const qc = useQueryClient();
  const socketRef = useRef<WebSocket | null>(null);
  const retryDelayRef = useRef<number>(1000);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const disconnect = useCallback(() => {
    mountedRef.current = false;
    if (retryTimerRef.current !== null) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
    if (socketRef.current) {
      socketRef.current.onclose = null; // prevent reconnect loop
      socketRef.current.close();
      socketRef.current = null;
    }
  }, []);

  const connect = useCallback(() => {
    if (!mountedRef.current || !enabled) return;

    const ws = new WebSocket(buildWsUrl(apiKey));
    socketRef.current = ws;

    ws.onopen = () => {
      retryDelayRef.current = 1000; // reset back-off on successful connect
    };

    ws.onmessage = (ev) => {
      let event: ApprovalsEvent;
      try {
        event = JSON.parse(ev.data) as ApprovalsEvent;
      } catch {
        return;
      }

      onEventRef.current?.(event);

      // Invalidate cache on state transitions (excluding snapshot replays and pings)
      if (!event.snapshot && STATE_TRANSITION_KINDS.has(event.kind)) {
        qc.invalidateQueries({ queryKey: ["approvals", "flat"] });
        qc.invalidateQueries({ queryKey: ["approvals", "history"] });
        if (event.approval_id) {
          qc.invalidateQueries({ queryKey: ["approvals", "detail", event.approval_id] });
        }
      }
    };

    ws.onerror = () => {
      // onclose will fire next and handle reconnect
    };

    ws.onclose = () => {
      socketRef.current = null;
      if (!mountedRef.current) return;
      // Exponential back-off: 1 s → 2 s → 4 s → … capped at 30 s
      retryTimerRef.current = setTimeout(() => {
        if (mountedRef.current) connect();
      }, retryDelayRef.current);
      retryDelayRef.current = Math.min(retryDelayRef.current * 2, 30_000);
    };
  }, [enabled, apiKey, qc]);

  useEffect(() => {
    mountedRef.current = true;
    if (enabled) connect();
    return () => {
      disconnect();
      // Note: mountedRef.current is already set to false by disconnect().
      // The next effect re-run (StrictMode or dependency change) sets it back to
      // true on line 165 before calling connect(), so no extra reset is needed here.
    };
  }, [connect, disconnect, enabled]);

  return { disconnect };
}
