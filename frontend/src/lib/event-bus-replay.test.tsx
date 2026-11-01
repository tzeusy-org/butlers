// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider, QueryObserver } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { EventBusProvider, useEventBus } from "./event-bus";
import { EVENT_CACHE_COVERAGE_MANIFEST } from "@/hooks/event-cache-manifest";
import { EVENT_CACHE_REGISTRY, type FleetEvent } from "@/hooks/event-cache-registry";

// Real provider -> transport -> registry -> QueryClient; only the wire is replaced.
class Wire {
  static sockets: Wire[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
  url: string;
  constructor(url: string) { this.url = url; Wire.sockets.push(this); }
  send(payload: unknown) { this.onmessage?.({ data: JSON.stringify(payload) }); }
}

const clients: QueryClient[] = [];
function mountBus() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  clients.push(qc);
  function wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}><EventBusProvider>{children}</EventBusProvider></QueryClientProvider>;
  }
  const hook = renderHook(() => useEventBus(), { wrapper });
  const wire = Wire.sockets.at(-1)!;
  act(() => wire.onopen?.());
  return { qc, wire, ...hook };
}

beforeEach(() => {
  vi.useFakeTimers();
  Wire.sockets = [];
  vi.stubGlobal("WebSocket", Wire);
});
afterEach(() => {
  cleanup();
  for (const qc of clients.splice(0)) qc.clear();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("production fleet snapshot replay", () => {
  it("replays 199 ingestion plus chronicles with at most five invalidations and all six cache effects", () => {
    const { qc, wire } = mountBus();
    const keys = [
      ["ingestion", "events", "list", { limit: 20 }],
      ["ingestion", "window-rollup"],
      ["ingestion", "events-histogram"],
      ["chronicles", "episodes"],
      ["entity-activity", "entity-one"],
      ["entity-activity-bins", "entity-one", "90d"],
    ];
    for (const key of keys) qc.setQueryData(key, "cached");
    const fetches = keys.map(() => vi.fn(async () => "refreshed"));
    const subscriptions = keys.map((queryKey, index) => new QueryObserver(qc, {
      queryKey, queryFn: fetches[index],
    }).subscribe(() => {}));
    qc.setQueryData(["unrelated"], "untouched");
    const invalidate = vi.spyOn(qc, "invalidateQueries");
    act(() => {
      wire.send({ type: "snapshot", ts: 1, events: [
        ...Array.from({ length: 199 }, () => ({ type: "ingestion", ts: 1, data: {} })),
        { type: "chronicles", ts: 1, data: {} },
      ] });
      vi.advanceTimersByTime(250);
    });
    expect(invalidate.mock.calls.length).toBeLessThanOrEqual(5);
    for (const key of keys) expect(qc.getQueryState(key)?.isInvalidated, JSON.stringify(key)).toBe(true);
    for (const fetch of fetches) expect(fetch).toHaveBeenCalledTimes(1);
    expect(qc.getQueryState(["unrelated"])?.isInvalidated).toBe(false);
    for (const unsubscribe of subscriptions) unsubscribe();
  });

  it("covers the complete current registry manifest, dynamic IDs and session predicates without widening unrelated reads", () => {
    const { qc, wire, result } = mountBus();
    const events: FleetEvent[] = Object.keys(EVENT_CACHE_REGISTRY).map((type) => ({ type, ts: 1, data: {} }));
    events.push(
      { type: "approval", ts: 2, data: { approval_id: "abc-1" } },
      { type: "approval", ts: 3, data: { approval_id: "second" } },
      { type: "session", ts: 4, data: { butler: "home", session_id: "one" } },
      { type: "session", ts: 5, data: { butler: "health", session_id: "two" } },
      { type: "entity.rebound.v1", ts: 6, data: { source_entity_id: "source", target_entity_id: "target" } },
    );
    const delivered: FleetEvent[] = [];
    for (const type of Object.keys(EVENT_CACHE_REGISTRY)) {
      result.current.subscribe(type, (event, meta) => {
        expect(meta.replayed).toBe(true);
        delivered.push(event);
      });
    }
    const covered = [
      ...EVENT_CACHE_COVERAGE_MANIFEST.map(({ queryKey }) => queryKey),
      ["approvals", "detail", "second"], ["approvals", "stalled-route-verification", "second"],
      ["session-detail", "home", "one"], ["session-detail", "health", "two"],
      ["relationship-entity", "source"], ["relationship-entity", "target"],
      ...["detail", "sessions", "rollup"].map((kind) => ["ingestion", "events", "request", kind]),
    ];
    const untouched = [
      ["unrelated"], ["settings-console"], ["approvals", "detail", "unseen"],
      ["session-detail", "other", "unseen"], ["ingestion", "events", "request", "payload"],
      ["ingestion", "events", "request", "replays"],
    ];
    for (const key of [...covered, ...untouched]) qc.setQueryData(key, "cached");
    act(() => { wire.send({ type: "snapshot", ts: 7, events }); vi.advanceTimersByTime(250); });
    for (const key of covered) expect(qc.getQueryState(key)?.isInvalidated, JSON.stringify(key)).toBe(true);
    for (const key of untouched) expect(qc.getQueryState(key)?.isInvalidated, JSON.stringify(key)).toBe(false);
    expect(delivered).toEqual(events);
  });

  it("isolates malformed entries while rejecting malformed outer frames and keeping future events and heartbeats", () => {
    const { qc, wire, result } = mountBus();
    const seen: string[] = [];
    for (const type of ["issue", "future-event", "__proto__", "heartbeat"]) {
      result.current.subscribe(type, (event, meta) => seen.push(`${event.type}:${meta.replayed}`));
    }
    const bad = [null, [], { type: "snapshot", ts: 1, data: {} }, { type: "snapshot", ts: "1", events: [] },
      { type: "snapshot", ts: 1, events: {} }, { type: "issue", ts: null, data: {} }, { type: "issue", ts: 1, data: [] }];
    act(() => {
      wire.onmessage?.({ data: "not JSON" });
      for (const frame of bad) wire.send(frame);
    });
    expect(result.current.health).toBe("late");
    expect(result.current.lastEventAt).toBeNull();
    qc.setQueryData(["issues"], "cached");
    act(() => wire.send({ type: "snapshot", ts: 2, events: [
      null, { type: "issue", ts: 1, data: {} }, { type: "issue", ts: 1, data: [] },
      { type: "future-event", ts: 1, data: {} }, { type: "__proto__", ts: 1, data: {} },
    ] }));
    expect(result.current.health).toBe("healthy");
    expect(qc.getQueryState(["issues"])?.isInvalidated).toBe(true);
    const at = result.current.lastEventAt;
    act(() => { vi.advanceTimersByTime(1_000); for (const frame of bad) wire.send(frame); });
    expect(result.current.lastEventAt).toBe(at);
    act(() => wire.send({ type: "heartbeat", ts: 3, data: {} }));
    expect(seen).toEqual(["issue:true", "future-event:true", "__proto__:true", "heartbeat:false"]);
  });

  it("delivers the entire replay before reentrant live frames in FIFO order, exactly once", () => {
    const { qc, wire, result } = mountBus();
    const seen: string[] = [];
    qc.setQueryData(["issues"], "cached");
    result.current.subscribe("spend", (event, meta) => {
      seen.push(`${event.ts}:${meta.replayed}`);
      if (event.ts === 1) {
        wire.send({ type: "issue", ts: 3, data: {} });
        wire.send({ type: "spend", ts: 4, data: {} });
        expect(qc.getQueryState(["issues"])?.isInvalidated).toBe(false);
      }
    });
    result.current.subscribe("issue", (event, meta) => seen.push(`${event.ts}:${meta.replayed}`));
    act(() => wire.send({ type: "snapshot", ts: 2, events: [
      { type: "spend", ts: 1, data: {} }, { type: "spend", ts: 2, data: {} },
    ] }));
    expect(seen).toEqual(["1:true", "2:true", "3:false", "4:false"]);
    expect(qc.getQueryState(["issues"])?.isInvalidated).toBe(true);
  });

  it("keeps live ingestion on a fixed window and never cancels an in-flight ingestion read", () => {
    const { qc, wire } = mountBus();
    let signal: AbortSignal | undefined;
    qc.setQueryData(["ingestion", "events", "list"], "cached");
    const fetch = vi.fn(({ signal: current }: { signal: AbortSignal }) => {
      signal = current;
      return new Promise<string>(() => {});
    });
    const observer = new QueryObserver(qc, { queryKey: ["ingestion", "events", "list"], queryFn: fetch });
    const unsubscribe = observer.subscribe(() => {});
    const invalidate = vi.spyOn(qc, "invalidateQueries");
    const event = { type: "ingestion", ts: 1, data: {} };
    act(() => { wire.send(event); vi.advanceTimersByTime(200); wire.send(event); });
    expect(invalidate).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(50));
    expect(invalidate).toHaveBeenCalledTimes(3);
    expect(fetch).toHaveBeenCalledTimes(1);
    act(() => { wire.send(event); vi.advanceTimersByTime(200); wire.send(event); vi.advanceTimersByTime(50); });
    expect(invalidate).toHaveBeenCalledTimes(6);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(signal?.aborted).toBe(false);
    // Snapshot batching has the same no-cancel policy while that read is pending.
    act(() => wire.send({ type: "snapshot", ts: 2, events: [event, event] }));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(signal?.aborted).toBe(false);
    unsubscribe();
  });

  it("discards old-socket work through reconnect, session loss and unmount", () => {
    const { qc, wire, result, unmount } = mountBus();
    qc.setQueryData(["ingestion", "events", "list"], "cached");
    const invalidate = vi.spyOn(qc, "invalidateQueries");
    const saved = wire.onmessage!;
    act(() => {
      wire.send({ type: "ingestion", ts: 1, data: {} });
      wire.onclose?.({ code: 1006 });
      saved({ data: JSON.stringify({ type: "issue", ts: 2, data: {} }) });
      vi.advanceTimersByTime(250);
    });
    expect(invalidate).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(750));
    const next = Wire.sockets.at(-1)!;
    expect(next).not.toBe(wire);
    act(() => { next.onopen?.(); next.send({ type: "heartbeat", ts: 3, data: {} }); });
    expect(result.current.health).toBe("healthy");
    act(() => { next.send({ type: "ingestion", ts: 4, data: {} }); next.onclose?.({ code: 4401 }); vi.advanceTimersByTime(60_000); });
    expect(result.current.status).toBe("closed");
    expect(Wire.sockets).toHaveLength(2);
    expect(invalidate).not.toHaveBeenCalled();
    unmount();
    act(() => { saved({ data: JSON.stringify({ type: "issue", ts: 5, data: {} }) }); vi.advanceTimersByTime(60_000); });
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("refuses an event stream outside the dashboard origin", () => {
    vi.stubEnv("VITE_API_URL", "https://untrusted.invalid/api");
    expect(() => mountBus()).toThrow("Event stream must use the dashboard origin");
    expect(Wire.sockets).toHaveLength(0);
  });
});
