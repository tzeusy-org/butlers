/**
 * useEventStream — WebSocket hook for the multiplexed /api/events/stream.
 *
 * Generalizes the WS→targeted-invalidation pattern first built for
 * useApprovalsStream (approvals), then copied for useSpendStream and
 * useSettingsConsoleStream — see use-approvals-stream.ts:146-152 for the
 * pattern this replicates once instead of a fourth time. A single
 * connection carries every event type (session, notification, ingestion,
 * issue, approval, spend, heartbeat); each event is routed through the
 * declarative event-cache-registry (see event-cache-registry.ts) so adding a
 * new live-updating surface means adding one registry entry, not a bespoke
 * WS hook.
 *
 * Connection state is exposed as "connecting" | "open" | "reconnecting" |
 * "closed" so the shell's Live indicator (see Shell.tsx) can render actual
 * socket health rather than just "the hook is mounted".
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { applyFleetEvent, type FleetEvent } from "@/hooks/event-cache-registry";

export type EventStreamStatus = "connecting" | "open" | "reconnecting" | "closed";

export interface UseEventStreamOptions {
  /** Optional DASHBOARD_API_KEY for query-param auth. Leave undefined when
   *  the server has no API key configured (dev mode). */
  apiKey?: string;
  /** Disable the hook (no-op when false). Defaults to true. */
  enabled?: boolean;
  /** Called for every incoming event, including snapshot-replayed ones and
   *  heartbeats. Cache patching happens regardless of whether this is set. */
  onEvent?: (event: FleetEvent) => void;
}

export interface UseEventStreamResult {
  /** Actual socket health: "connecting" (first attempt), "open" (connected),
   *  "reconnecting" (was connected, currently retrying), or "closed"
   *  (intentionally disabled/unmounted). */
  status: EventStreamStatus;
  /** Wall-clock ms timestamp of the last message received (including
   *  heartbeats), or null before the first message. Useful for staleness
   *  checks that must decay on a clock rather than freeze on last-fetch. */
  lastEventAt: number | null;
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

  const url = `${wsBase}/events/stream`;
  return apiKey ? `${url}?api_key=${encodeURIComponent(apiKey)}` : url;
}

interface SnapshotMessage {
  type: "snapshot";
  ts: number;
  events: FleetEvent[];
}

function isSnapshot(payload: FleetEvent | SnapshotMessage): payload is SnapshotMessage {
  return payload.type === "snapshot";
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useEventStream({
  apiKey,
  enabled = true,
  onEvent,
}: UseEventStreamOptions = {}): UseEventStreamResult {
  const qc = useQueryClient();

  const [status, setStatus] = useState<EventStreamStatus>("connecting");
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const retryDelayRef = useRef<number>(1000);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  // True once the socket has ever reached onopen — distinguishes the very
  // first connection attempt ("connecting") from a retry after a drop
  // ("reconnecting"), matching the shell's connected/reconnecting/down states.
  const everConnectedRef = useRef(false);
  const onEventRef = useRef(onEvent);
  const connectRef = useRef<() => void>(() => undefined);

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
    setStatus("closed");
  }, []);

  const connect = useCallback(() => {
    if (!mountedRef.current || !enabled) return;

    // Note: status is intentionally NOT set here. The initial "connecting"
    // state comes from useState's default; every subsequent status change is
    // driven from the ws.onopen/ws.onclose event callbacks below (an external
    // system notifying us of a state change), not synchronously from this
    // function being invoked — calling setState directly inside an effect
    // body (see the mount effect further down) trips
    // react-hooks/set-state-in-effect otherwise.
    const ws = new WebSocket(buildWsUrl(apiKey));
    socketRef.current = ws;

    ws.onopen = () => {
      retryDelayRef.current = 1000; // reset back-off on successful connect
      everConnectedRef.current = true;
      setStatus("open");
    };

    ws.onmessage = (ev) => {
      let payload: FleetEvent | SnapshotMessage;
      try {
        payload = JSON.parse(ev.data);
      } catch {
        return;
      }

      setLastEventAt(Date.now());

      if (isSnapshot(payload)) {
        // Replay each buffered event through the registry too — a reconnect's
        // snapshot may carry state changes missed while the socket was down.
        for (const replayed of payload.events) {
          applyFleetEvent(qc, replayed);
          onEventRef.current?.(replayed);
        }
        return;
      }

      applyFleetEvent(qc, payload);
      onEventRef.current?.(payload);
    };

    ws.onerror = () => {
      // onclose will fire next and handle reconnect.
    };

    ws.onclose = () => {
      socketRef.current = null;
      if (!mountedRef.current) return;
      setStatus(everConnectedRef.current ? "reconnecting" : "connecting");
      // Exponential back-off: 1 s → 2 s → 4 s → … capped at 30 s
      retryTimerRef.current = setTimeout(() => {
        if (mountedRef.current) connectRef.current();
      }, retryDelayRef.current);
      retryDelayRef.current = Math.min(retryDelayRef.current * 2, 30_000);
    };
  }, [enabled, apiKey, qc]);

  // Keep connectRef and onEventRef pointing at the latest callbacks.
  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    mountedRef.current = true;
    everConnectedRef.current = false;
    if (enabled) connect();
    return () => {
      disconnect();
    };
  }, [connect, disconnect, enabled]);

  return { status, lastEventAt, disconnect };
}
