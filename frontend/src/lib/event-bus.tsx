/**
 * EventBusProvider — one shared fleet-event-stream connection with a
 * subscribe(type, cb) API (bu-qvnce.14 slice 1).
 *
 * Before this, `useEventStream()` was mounted exactly once in RootLayout
 * purely for its side effect (declarative cache invalidation via
 * event-cache-registry.ts's `applyFleetEvent`) and its connection-status
 * return value -- there was no way for another component to observe the RAW
 * events on that same socket without opening a second, redundant WebSocket.
 * Three call sites did exactly that:
 *   - useApprovalsStream (ApprovalsPage) -- deleted; its cache invalidation
 *     was already a strict subset of event-cache-registry.ts's
 *     approvalPatch, and it never used its own onEvent callback.
 *   - useSpendStream (SpendPage's live cost ticker) -- ported to
 *     use-spend-ticker.ts's useSpendTicker(), which subscribes here instead
 *     of opening /api/spend/stream itself.
 *   - useSettingsConsoleStream (SettingsConsolePage) -- NOT ported. Its wire
 *     protocol (header_delta/attention_add/attention_remove) has no
 *     equivalent on the unified bus today -- the backend never fans those
 *     deltas onto `/api/events/stream` (see src/butlers/api/routers/
 *     settings_console.py). Porting it needs a backend event type, which is
 *     out of scope for a frontend-only slice; left running its own socket
 *     with a follow-up filed to add the backend wiring.
 *
 * EventBusProvider wraps `useEventStream()` ONCE and re-exposes its per-type
 * events to any number of subscribers via `subscribe(type, cb)`. Cache
 * invalidation keeps happening exactly as before -- `useEventStream` still
 * calls `applyFleetEvent` on every message internally, independent of
 * whether anyone subscribes here. This layer only adds fan-out for
 * consumers that need the raw event (a ticker, a toast), not just the cache
 * side effect.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from "react";

import { useEventStream, type EventStreamStatus } from "@/hooks/use-event-stream";
import type { FleetEvent } from "@/hooks/event-cache-registry";

/** Metadata delivered alongside every event handed to a bus listener. */
export interface BusEventMeta {
  /** True when this event was replayed from the server's ring-buffer
   *  snapshot (sent on initial connect and on every reconnect), rather than
   *  observed live. Consumers building a monotonic "since mount" counter
   *  (e.g. the spend ticker) should ignore replayed events -- those costs
   *  are already reflected in whatever server-fetched baseline the consumer
   *  holds, so counting them again double-counts (see use-spend-ticker.ts).
   */
  replayed: boolean;
}

export type BusListener = (event: FleetEvent, meta: BusEventMeta) => void;

export interface EventBusContextValue {
  /** Actual socket health -- see useEventStream's EventStreamStatus. */
  status: EventStreamStatus;
  /** Wall-clock ms timestamp of the last message received, or null before
   *  the first one. */
  lastEventAt: number | null;
  /**
   * Subscribe to every event of a given fleet-bus `type` (see EVENT_TYPES in
   * src/butlers/api/routers/events.py -- "session" | "notification" |
   * "ingestion" | "issue" | "approval" | "spend" | "heartbeat"). Returns an
   * unsubscribe function; call it on cleanup.
   */
  subscribe: (type: string, listener: BusListener) => () => void;
}

const EventBusContext = createContext<EventBusContextValue | null>(null);

export interface EventBusProviderProps {
  /** Optional DASHBOARD_API_KEY for query-param auth. Leave undefined when
   *  the server has no API key configured (dev mode) -- mirrors
   *  useEventStream's own option. */
  apiKey?: string;
  children: ReactNode;
}

export function EventBusProvider({ apiKey, children }: EventBusProviderProps) {
  // One Set of listeners per event type, keyed in a ref rather than state --
  // dispatch must never itself trigger a re-render. Listeners are plain
  // side-effecting callbacks (cache patches, ticker updates), not rendered
  // values, so there is nothing here for React to re-render over.
  const listenersRef = useRef<Map<string, Set<BusListener>>>(new Map());

  const dispatch = useCallback((event: FleetEvent, meta: BusEventMeta) => {
    const listeners = listenersRef.current.get(event.type);
    if (!listeners || listeners.size === 0) return;
    for (const listener of listeners) listener(event, meta);
  }, []);

  const { status, lastEventAt } = useEventStream({
    apiKey,
    onEvent: dispatch,
  });

  const subscribe = useCallback((type: string, listener: BusListener) => {
    let listeners = listenersRef.current.get(type);
    if (!listeners) {
      listeners = new Set();
      listenersRef.current.set(type, listeners);
    }
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  const value = useMemo<EventBusContextValue>(
    () => ({ status, lastEventAt, subscribe }),
    [status, lastEventAt, subscribe],
  );

  return <EventBusContext.Provider value={value}>{children}</EventBusContext.Provider>;
}

/**
 * Read the shared event bus's connection status/lastEventAt and its
 * subscribe function. Throws outside an EventBusProvider -- there is
 * exactly one, mounted once by RootLayout, so a missing provider is a
 * wiring bug, not a runtime condition callers should handle.
 */
export function useEventBus(): EventBusContextValue {
  const ctx = useContext(EventBusContext);
  if (!ctx) {
    throw new Error("useEventBus must be used within an EventBusProvider");
  }
  return ctx;
}

/**
 * Subscribe to one fleet-bus event `type` for the lifetime of the calling
 * component, without opening a second WebSocket. `listener` may be an inline
 * arrow function -- the latest render's callback is kept in a ref so passing
 * a new function identity every render does not cause a resubscribe.
 *
 * Set `enabled=false` to skip subscribing (e.g. a disabled/inactive view
 * that wants to opt out of the dispatch cost).
 */
export function useBusEvent(type: string, listener: BusListener, enabled = true): void {
  const { subscribe } = useEventBus();
  const listenerRef = useRef(listener);
  useEffect(() => {
    listenerRef.current = listener;
  }, [listener]);

  useEffect(() => {
    if (!enabled) return;
    return subscribe(type, (event, meta) => listenerRef.current(event, meta));
  }, [type, enabled, subscribe]);
}
