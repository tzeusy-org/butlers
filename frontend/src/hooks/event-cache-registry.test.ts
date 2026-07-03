// @vitest-environment jsdom
/**
 * Tests for the declarative event -> cache-patch registry
 * (bu-86c4c.8, §JARVIS audit move 5).
 */

import { describe, it, expect, vi } from "vitest";
import type { QueryClient } from "@tanstack/react-query";

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
      ["approval", "heartbeat", "ingestion", "issue", "notification", "session", "spend"].sort(),
    );
  });

  it("approval: invalidates flat, history, and detail(approval_id) when present", () => {
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

  it("spend: invalidates cost-summary, daily-costs, top-sessions", () => {
    const { qc, invalidateQueries } = makeQc();
    applyFleetEvent(qc, { type: "spend", ts: 1, data: { butler: "atlas" } });
    expect(keys(invalidateQueries)).toEqual(
      expect.arrayContaining([["cost-summary"], ["daily-costs"], ["top-sessions"]]),
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
        ["timeline"],
      ]),
    );
  });

  it("session: omits session-detail invalidation when butler or session_id is missing", () => {
    const { qc, invalidateQueries } = makeQc();
    applyFleetEvent(qc, { type: "session", ts: 1, data: { phase: "started" } });
    const called = keys(invalidateQueries);
    expect(called.some((k) => Array.isArray(k) && k[0] === "session-detail")).toBe(false);
  });

  it("notification: invalidates messenger delivery stats, queue depth, and the timeline", () => {
    const { qc, invalidateQueries } = makeQc();
    applyFleetEvent(qc, { type: "notification", ts: 1, data: {} });
    expect(keys(invalidateQueries)).toEqual(
      expect.arrayContaining([
        ["messenger-delivery-stats"],
        ["messenger-queue-depth"],
        ["timeline"],
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
        ["ingestion", "events"],
        ["ingestion", "window-rollup"],
        ["ingestion", "events-histogram"],
      ]),
    );
  });

  it("heartbeat: is a no-op (no cache invalidation)", () => {
    const { qc, invalidateQueries } = makeQc();
    applyFleetEvent(qc, { type: "heartbeat", ts: 1, data: {} });
    expect(invalidateQueries).not.toHaveBeenCalled();
  });

  it("unknown event type: is a no-op rather than throwing", () => {
    const { qc, invalidateQueries } = makeQc();
    expect(() => applyFleetEvent(qc, { type: "totally-unknown", ts: 1, data: {} })).not.toThrow();
    expect(invalidateQueries).not.toHaveBeenCalled();
  });
});
