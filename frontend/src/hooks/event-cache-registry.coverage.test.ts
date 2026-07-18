// @vitest-environment jsdom
/**
 * Registry↔hook coverage test (bu-qvnce.14 slice 5).
 *
 * event-cache-registry.test.ts asserts the registry's OWN internal shape
 * (e.g. "the approval patch invalidates these four keys"). This file asserts
 * the OTHER direction: that every hook which claims bus coverage (tracked in
 * event-cache-manifest.ts) actually gets invalidated by the event type it
 * claims to be covered by. Without this, a hook's doc comment can silently
 * rot -- exactly what happened to use-notifications.ts's three query keys,
 * which claimed no bus coverage explicitly but were assumed live-updating
 * once event-cache-registry.ts existed, while notificationPatch never
 * touched them (see event-cache-manifest.ts's header comment).
 *
 * Adding a new bus-covered surface without a matching manifest entry is
 * invisible to this test by design -- the manifest is the opt-in list, not a
 * static-analysis sweep of every hook in the app. The second test below
 * guards the coarser regression (a whole event type losing manifest
 * coverage entirely).
 */

import { describe, it, expect, vi } from "vitest";
import type { QueryClient } from "@tanstack/react-query";

import { applyFleetEvent, type FleetEvent } from "./event-cache-registry";
import { EVENT_CACHE_COVERAGE_MANIFEST, CACHE_AFFECTING_EVENT_TYPES } from "./event-cache-manifest";

function makeQc(): { qc: QueryClient; invalidateQueries: ReturnType<typeof vi.fn> } {
  const invalidateQueries = vi.fn();
  return { qc: { invalidateQueries } as unknown as QueryClient, invalidateQueries };
}

function invalidatedKeys(invalidateQueries: ReturnType<typeof vi.fn>): unknown[][] {
  return invalidateQueries.mock.calls.map((call) => call[0].queryKey as unknown[]);
}

/** invalidateQueries prefix-matches: an invalidation of `prefix` also
 *  invalidates any cached query whose key starts with `prefix`. */
function isPrefixOf(prefix: unknown[], key: unknown[]): boolean {
  if (prefix.length > key.length) return false;
  return prefix.every((segment, i) => JSON.stringify(segment) === JSON.stringify(key[i]));
}

describe("event-cache-registry <-> hook coverage manifest", () => {
  it.each(EVENT_CACHE_COVERAGE_MANIFEST)(
    '"$eventType" event invalidates $source\'s key ($queryKey)',
    ({ eventType, queryKey, source }) => {
      const { qc, invalidateQueries } = makeQc();
      const event: FleetEvent = { type: eventType, ts: 1, data: { approval_id: "abc-1" } };
      applyFleetEvent(qc, event);

      const called = invalidatedKeys(invalidateQueries);
      const covered = called.some((invalidated) => isPrefixOf(invalidated, queryKey));

      expect(
        covered,
        `expected a "${eventType}" event to invalidate ${source}'s query key ` +
          `${JSON.stringify(queryKey)}, but the registry only invalidated ` +
          `${JSON.stringify(called)}. If this key is no longer meant to be ` +
          `bus-covered, remove its manifest entry in event-cache-manifest.ts; ` +
          `otherwise the corresponding patch in event-cache-registry.ts is missing it.`,
      ).toBe(true);
    },
  );

  it("every cache-affecting event type still has at least one manifest entry", () => {
    // Guards the coarser regression: a whole event type quietly losing ALL
    // its manifest coverage (e.g. a careless manifest edit), not just one key.
    const coveredTypes = new Set(EVENT_CACHE_COVERAGE_MANIFEST.map((entry) => entry.eventType));
    for (const eventType of CACHE_AFFECTING_EVENT_TYPES) {
      expect(coveredTypes.has(eventType), `no manifest entry for event type "${eventType}"`).toBe(
        true,
      );
    }
  });

  it("calendar manifest declares the workspace metadata and audit views", () => {
    const calendarKeys = EVENT_CACHE_COVERAGE_MANIFEST.filter(
      (entry) => entry.eventType === "calendar",
    ).map((entry) => entry.queryKey);

    expect(calendarKeys).toEqual(
      expect.arrayContaining([
        ["calendar-workspace-meta"],
        ["calendar-workspace-audit"],
      ]),
    );
  });
});
