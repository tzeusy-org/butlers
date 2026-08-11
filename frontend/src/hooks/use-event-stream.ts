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
export type EventBusHealth = "healthy" | "late" | "down";

/** The API emits a heartbeat every 20 seconds; allow one delayed beat. */
export const EVENT_HEARTBEAT_DEADLINE_MS = 45_000;

export interface UseEventStreamOptions {
  /** Optional DASHBOARD_API_KEY for query-param auth. Leave undefined when
   *  the server has no API key configured (dev mode). */
  apiKey?: string;
  /** Disable the hook (no-op when false). Defaults to true. */
  enabled?: boolean;
  /** Called for every valid incoming event, including snapshot-replayed ones
   *  and heartbeats. Cache patching happens regardless of whether this is set.
   *  `meta.replayed` is true for events replayed from the server's
   *  ring-buffer snapshot on (re)connect rather than observed live -- see
   *  EventBusProvider (src/lib/event-bus.tsx), the primary consumer of this
   *  distinction. */
  onEvent?: (event: FleetEvent, meta: { replayed: boolean }) => void;
}

export interface UseEventStreamResult {
  /** Actual socket health: "connecting" (first attempt), "open" (connected),
   *  "reconnecting" (was connected, currently retrying), or "closed"
   *  (intentionally disabled/unmounted). */
  status: EventStreamStatus;
  /** Wall-clock ms timestamp of the last valid envelope received (including
   *  heartbeats), or null before the first valid envelope. Useful for
   *  staleness checks that must decay on a clock rather than freeze on
   *  last-fetch. */
  lastEventAt: number | null;
  /** One clock-driven health value for every event-bus consumer. */
  health: EventBusHealth;
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFleetEvent(payload: unknown): payload is FleetEvent {
  // `snapshot` is a reserved control envelope and cannot fall through as an
  // otherwise unknown future event when its snapshot shape is malformed.
  return (
    isRecord(payload) &&
    typeof payload.type === "string" &&
    payload.type !== "snapshot" &&
    typeof payload.ts === "number" &&
    isRecord(payload.data)
  );
}

function isSnapshot(payload: unknown): payload is SnapshotMessage {
  return (
    isRecord(payload) &&
    payload.type === "snapshot" &&
    typeof payload.ts === "number" &&
    Array.isArray(payload.events) &&
    payload.events.every(isFleetEvent)
  );
}

function isFleetEnvelope(payload: unknown): payload is FleetEvent | SnapshotMessage {
  return isSnapshot(payload) || isFleetEvent(payload);
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
  const [health, setHealth] = useState<EventBusHealth>("down");

  const socketRef = useRef<WebSocket | null>(null);
  const retryDelayRef = useRef<number>(1000);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  // True once the socket has ever reached onopen — distinguishes the very
  // first connection attempt ("connecting") from a retry after a drop
  // ("reconnecting"), matching the shell's connected/reconnecting/down states.
  const everConnectedRef = useRef(false);
  const connectedAtRef = useRef<number | null>(null);
  const currentSocketMessageAtRef = useRef<number | null>(null);
  const onEventRef = useRef(onEvent);
  const connectRef = useRef<() => void>(() => undefined);
  const heartbeatTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const disconnect = useCallback(() => {
    mountedRef.current = false;
    if (retryTimerRef.current !== null) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
    if (heartbeatTimerRef.current !== null) {
      clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = null;
    }
    if (socketRef.current) {
      socketRef.current.onclose = null; // prevent reconnect loop
      socketRef.current.close();
      socketRef.current = null;
    }
    setStatus("closed");
    setHealth("down");
  }, []);

  const reconnectStaleSocket = useCallback(() => {
    const staleSocket = socketRef.current;
    if (!mountedRef.current || !staleSocket) return;

    // A half-open socket can ignore close() and never emit onclose. Detach
    // it before creating its successor so a delayed old callback cannot
    // overwrite the new connection's state or schedule a second retry.
    staleSocket.onopen = null;
    staleSocket.onmessage = null;
    staleSocket.onerror = null;
    staleSocket.onclose = null;
    socketRef.current = null;
    staleSocket.close();

    setStatus("reconnecting");
    connectRef.current();
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
      if (socketRef.current !== ws) return;
      retryDelayRef.current = 1000; // reset back-off on successful connect
      everConnectedRef.current = true;
      connectedAtRef.current = Date.now();
      currentSocketMessageAtRef.current = null;
      setStatus("open");
      // An open transport alone does not prove that this socket is receiving
      // data. It becomes healthy only after the first valid envelope.
      setHealth("late");
    };

    ws.onmessage = (ev) => {
      if (socketRef.current !== ws) return;
      let payload: unknown;
      try {
        payload = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (!isFleetEnvelope(payload)) return;

      // The backend emits heartbeats only after an idle queue timeout, so a
      // snapshot or ordinary event is equally valid evidence of freshness.
      const receivedAt = Date.now();
      currentSocketMessageAtRef.current = receivedAt;
      setLastEventAt(receivedAt);
      setHealth("healthy");

      if (isSnapshot(payload)) {
        // Replay each buffered event through the registry too — a reconnect's
        // snapshot may carry state changes missed while the socket was down.
        for (const replayed of payload.events) {
          applyFleetEvent(qc, replayed);
          onEventRef.current?.(replayed, { replayed: true });
        }
        return;
      }

      applyFleetEvent(qc, payload);
      onEventRef.current?.(payload, { replayed: false });
    };

    ws.onerror = () => {
      // onclose will fire next and handle reconnect.
    };

    ws.onclose = () => {
      if (socketRef.current !== ws) return;
      socketRef.current = null;
      if (!mountedRef.current) return;
      setStatus(everConnectedRef.current ? "reconnecting" : "connecting");
      setHealth("down");
      // Exponential back-off: 1 s → 2 s → 4 s → … capped at 30 s
      retryTimerRef.current = setTimeout(() => {
        retryTimerRef.current = null;
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

  // Health must decay while the socket remains open. An interval is used
  // instead of deriving from render time so a half-open socket cannot freeze
  // at a falsely healthy value. The first valid envelope is required
  // explicitly, whether it is a heartbeat, snapshot, or ordinary event.
  useEffect(() => {
    if (!enabled || status !== "open") return;
    const refreshHealth = () => {
      const currentSocketMessageAt = currentSocketMessageAtRef.current;
      const freshnessAt = currentSocketMessageAt ?? connectedAtRef.current;
      const freshnessAge = freshnessAt === null ? Infinity : Date.now() - freshnessAt;
      if (freshnessAge >= EVENT_HEARTBEAT_DEADLINE_MS) {
        setHealth("late");
        reconnectStaleSocket();
      } else {
        setHealth(currentSocketMessageAt === null ? "late" : "healthy");
      }
    };
    heartbeatTimerRef.current = setInterval(refreshHealth, 1_000);
    return () => {
      if (heartbeatTimerRef.current !== null) {
        clearInterval(heartbeatTimerRef.current);
        heartbeatTimerRef.current = null;
      }
    };
  }, [enabled, reconnectStaleSocket, status]);

  return { status, lastEventAt, health, disconnect };
}
