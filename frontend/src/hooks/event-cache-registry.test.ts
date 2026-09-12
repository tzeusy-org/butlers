// @vitest-environment jsdom
/**
 * Tests for the declarative event -> cache-patch registry
 * (bu-86c4c.8, §JARVIS audit move 5).
 */

import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryObserver } from "@tanstack/react-query";

import { applyFleetEvent, EVENT_CACHE_REGISTRY, type FleetEvent } from "./event-cache-registry";

function makeQc(): { qc: QueryClient; invalidateQueries: ReturnType<typeof vi.fn> } {
  const invalidateQueries = vi.fn();
  return { qc: { invalidateQueries } as unknown as QueryClient, invalidateQueries };
}

function keys(invalidateQueries: ReturnType<typeof vi.fn>): unknown[] {
  return invalidateQueries.mock.calls.map((call) => call[0].queryKey);
}

describe("EVENT_CACHE_REGISTRY", () => {
  it("has one entry per canonical event type", () => {
    expect(Object.keys(EVENT_CACHE_REGISTRY).sort()).toEqual(
      [
        "approval",
        "attention_add",
        "attention_remove",
        "calendar",
        "chronicles",
        "header_delta",
        "heartbeat",
        "ingestion",
        "issue",
        "notification",
        "session",
        "spend",
      ].sort(),
    );
  });

  it("approval: invalidates flat, history, detail, and stalled verifier families when approval_id is present", () => {
    const { qc, invalidateQueries } = makeQc();
    const event: FleetEvent = {
      type: "approval",
      ts: 1,
      data: { kind: "approved", approval_id: "abc-1" },
    };
    applyFleetEvent(qc, event);
    expect(keys(invalidateQueries)).toEqual(
      expect.arrayContaining([
        ["approvals", "flat"],
        ["approvals", "history"],
        ["approvals", "metrics"],
        ["approvals", "detail", "abc-1"],
        ["approvals", "stalled-route-verification", "abc-1"],
      ]),
    );
  });

  it("approval: omits detail invalidation when approval_id is absent", () => {
    const { qc, invalidateQueries } = makeQc();
    applyFleetEvent(qc, { type: "approval", ts: 1, data: {} });
    const called = keys(invalidateQueries);
    expect(called).toContainEqual(["approvals", "flat"]);
    expect(called).toContainEqual(["approvals", "history"]);
    expect(called.some((k) => Array.isArray(k) && k[1] === "detail")).toBe(false);
  });

  it("spend: invalidates cost-summary, daily-costs, top-sessions, costs-by-schedule", () => {
    const { qc, invalidateQueries } = makeQc();
    applyFleetEvent(qc, { type: "spend", ts: 1, data: { butler: "atlas" } });
    expect(keys(invalidateQueries)).toEqual(
      expect.arrayContaining([
        ["cost-summary"],
        ["daily-costs"],
        ["top-sessions"],
        ["costs-by-schedule"],
      ]),
    );
  });

  it("session: invalidates sessions family + butlers board, and session-detail when butler+session_id present", () => {
    const { qc, invalidateQueries } = makeQc();
    applyFleetEvent(qc, {
      type: "session",
      ts: 1,
      data: { phase: "ended", butler: "home", session_id: "sess-1" },
    });
    expect(keys(invalidateQueries)).toEqual(
      expect.arrayContaining([
        ["sessions"],
        ["session-aggregate"],
        ["butler-sessions"],
        ["butlers", "board"],
        ["session-detail", "home", "sess-1"],
        ["session-detail-global"],
        ["timeline"],
      ]),
    );
  });

  it("session: omits butler-scoped session-detail invalidation when butler or session_id is missing", () => {
    const { qc, invalidateQueries } = makeQc();
    applyFleetEvent(qc, { type: "session", ts: 1, data: { phase: "started" } });
    const called = keys(invalidateQueries);
    expect(called.some((k) => Array.isArray(k) && k[0] === "session-detail")).toBe(false);
  });

  // bu-qvnce.5 (pursuit move 5, slice 2): SessionDetailPage's global fetch
  // must stay live even when the event carries no butler/session_id (the
  // butler-scoped key is conditional; the global one is not — see
  // sessionPatch's doc comment for why).
  it("session: always invalidates the global session-detail prefix, even without butler/session_id", () => {
    const { qc, invalidateQueries } = makeQc();
    applyFleetEvent(qc, { type: "session", ts: 1, data: { phase: "started" } });
    expect(keys(invalidateQueries)).toEqual(
      expect.arrayContaining([["session-detail-global"]]),
    );
  });

  it("notification: invalidates the timeline and notifications feed", () => {
    const { qc, invalidateQueries } = makeQc();
    applyFleetEvent(qc, { type: "notification", ts: 1, data: {} });
    expect(keys(invalidateQueries)).toEqual(
      expect.arrayContaining([
        ["timeline"],
        ["notifications"],
        ["butler-notifications"],
        ["notification-stats"],
      ]),
    );
  });

  it("issue: invalidates the issues feed (covers both active and dismissed views)", () => {
    const { qc, invalidateQueries } = makeQc();
    applyFleetEvent(qc, { type: "issue", ts: 1, data: {} });
    expect(keys(invalidateQueries)).toEqual(expect.arrayContaining([["issues"]]));
  });

  it("ingestion: invalidates the ingestion events feed, window-rollup, and histogram", () => {
    const { qc, invalidateQueries } = makeQc();
    applyFleetEvent(qc, { type: "ingestion", ts: 1, data: {} });
    expect(keys(invalidateQueries)).toEqual(
      expect.arrayContaining([
        ["ingestion", "events", "list"],
        ["ingestion", "window-rollup"],
        ["ingestion", "events-histogram"],
      ]),
    );
  });

  it("keeps historical detail and payload fresh on unrelated ingestion, but refreshes lifecycle on sessions", () => {
    const qc = new QueryClient();
    const categories = ["detail", "sessions", "rollup", "payload", "replays", "sender-contact"];
    for (const category of categories) qc.setQueryData(["ingestion", "events", "old", category], {});
    applyFleetEvent(qc, { type: "ingestion", ts: 1, data: {} });
    for (const category of categories) {
      expect(qc.getQueryState(["ingestion", "events", "old", category])?.isInvalidated).toBe(false);
    }
    applyFleetEvent(qc, { type: "session", ts: 2, data: {} });
    for (const category of categories) {
      expect(qc.getQueryState(["ingestion", "events", "old", category])?.isInvalidated)
        .toBe(["detail", "sessions", "rollup"].includes(category));
    }
    qc.clear();
  });

  it("lets slow cached Timeline reads finish through continuous events, then refreshes on the next event", async () => {
    vi.useFakeTimers();
    const qc = new QueryClient();
    const queryKeys = [
      ["ingestion", "events", "list", {}],
      ["ingestion", "window-rollup", {}],
      ["ingestion", "events-histogram", {}],
      ["ingestion", "events", "old", "detail"],
      ["ingestion", "events", "old", "sessions"],
      ["ingestion", "events", "old", "rollup"],
    ];
    const aborted = vi.fn();
    const fetchers = queryKeys.map(() => vi.fn(({ signal }: { signal: AbortSignal }) =>
      new Promise<number>((resolve, reject) => {
        const timer = setTimeout(() => resolve(1), 1_000);
        signal.addEventListener("abort", () => {
          clearTimeout(timer);
          aborted();
          reject(new Error("aborted"));
        }, { once: true });
      }),
    ));
    const unsubscribes = queryKeys.map((queryKey, index) => {
      qc.setQueryData(queryKey, 0);
      return new QueryObserver(qc, {
        queryKey, queryFn: fetchers[index], staleTime: Infinity, retry: false,
      }).subscribe(() => undefined);
    });
    const notify = () => {
      applyFleetEvent(qc, { type: "ingestion", ts: 1, data: {} });
      applyFleetEvent(qc, { type: "session", ts: 1, data: {} });
    };
    try {
      notify();
      for (let i = 0; i < 3; i++) {
        await vi.advanceTimersByTimeAsync(250);
        notify();
      }
      await vi.advanceTimersByTimeAsync(250);
      expect(aborted).not.toHaveBeenCalled();
      for (const [index, queryKey] of queryKeys.entries()) {
        expect(fetchers[index]).toHaveBeenCalledTimes(1);
        expect(qc.getQueryData(queryKey)).toBe(1);
      }
      notify();
      for (const fetcher of fetchers) expect(fetcher).toHaveBeenCalledTimes(2);
      await vi.advanceTimersByTimeAsync(1_000);
      expect(aborted).not.toHaveBeenCalled();
    } finally {
      unsubscribes.forEach((unsubscribe) => unsubscribe());
      qc.clear();
      vi.useRealTimers();
    }
  });

  it("calendar: invalidates the projected workspace and its derived views", () => {
    const { qc, invalidateQueries } = makeQc();
    applyFleetEvent(qc, { type: "calendar", ts: 1, data: { kind: "provider_projection" } });
    expect(keys(invalidateQueries)).toEqual(
      expect.arrayContaining([
        ["calendar-workspace"],
        ["calendar-overlays"],
        ["calendar-day-briefing"],
        ["calendar-proposals"],
        ["calendar-duplicates"],
        ["calendar-conflicts"],
        ["calendar-workspace-entry"],
        ["calendar-workspace-search"],
        ["calendar-workspace-meta"],
        ["calendar-workspace-audit"],
      ]),
    );
  });

  it("chronicles: invalidates projection-backed chronicler data", () => {
    const { qc, invalidateQueries } = makeQc();
    applyFleetEvent(qc, { type: "chronicles", ts: 1, data: { kind: "projection" } });
    expect(keys(invalidateQueries)).toEqual(expect.arrayContaining([["chronicles"]]));
  });

  it("heartbeat: is a no-op (no cache invalidation)", () => {
    const { qc, invalidateQueries } = makeQc();
    applyFleetEvent(qc, { type: "heartbeat", ts: 1, data: {} });
    expect(invalidateQueries).not.toHaveBeenCalled();
  });

  it.each(["header_delta", "attention_add", "attention_remove"])(
    "%s: is a no-op (Settings Console applies these via its own local reducer, not react-query)",
    (type) => {
      const { qc, invalidateQueries } = makeQc();
      applyFleetEvent(qc, { type, ts: 1, data: {} });
      expect(invalidateQueries).not.toHaveBeenCalled();
    },
  );

  it("unknown event type: is a no-op rather than throwing", () => {
    const { qc, invalidateQueries } = makeQc();
    expect(() => applyFleetEvent(qc, { type: "totally-unknown", ts: 1, data: {} })).not.toThrow();
    expect(invalidateQueries).not.toHaveBeenCalled();
  });
});
