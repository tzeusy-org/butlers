import { describe, expect, it } from "vitest";

import type { DayVerdictResponse } from "@/api/types.ts";
import { msUntilVerdictBoundary } from "@/lib/day-verdict-boundary";

function verdict(over: Partial<DayVerdictResponse> = {}): DayVerdictResponse {
  return {
    available: true,
    date: "2026-11-26",
    zone: "Asia/Singapore",
    relative: "today",
    freshness: { state: "current", as_of: null, sources_unreadable: [], sources_total: 1 },
    current: null,
    next: null,
    committed_minutes: 0,
    committed_count: 0,
    tentative_count: 0,
    free_window: null,
    empty: true,
    ...over,
  };
}

const entry = (start: string, end: string) => ({
  ref: "x",
  start,
  end,
  title: "t",
  people_count: 0,
  door: "/calendar?entry=x",
});

describe("msUntilVerdictBoundary", () => {
  it("waits for the next meeting start when it is sooner than 18:00", () => {
    const now = new Date("2026-11-26T02:00:00Z"); // 10:00 SGT
    const next = entry("2026-11-26T14:00:00+08:00", "2026-11-26T15:00:00+08:00");
    expect(msUntilVerdictBoundary(verdict({ next }), now)).toBe(4 * 3600_000);
  });

  it("waits for the current meeting to end when that is soonest", () => {
    const now = new Date("2026-11-26T06:20:00Z"); // 14:20 SGT
    const current = entry("2026-11-26T14:00:00+08:00", "2026-11-26T15:00:00+08:00");
    expect(msUntilVerdictBoundary(verdict({ current }), now)).toBe(40 * 60_000);
  });

  it("waits for 18:00 owner-local with no meetings ahead", () => {
    const now = new Date("2026-11-26T07:00:00Z"); // 15:00 SGT
    expect(msUntilVerdictBoundary(verdict(), now)).toBe(3 * 3600_000);
  });

  it("after 18:00 waits for local midnight", () => {
    const now = new Date("2026-11-26T11:00:00Z"); // 19:00 SGT
    expect(msUntilVerdictBoundary(verdict(), now)).toBe(5 * 3600_000);
  });

  it("uses the owner's zone, not the browser's, across a daylight-saving date", () => {
    // 2026-03-08 spring-forward in New York: 18:00 EDT is 22:00Z.
    const now = new Date("2026-03-08T21:00:00Z"); // 17:00 EDT
    expect(msUntilVerdictBoundary(verdict({ zone: "America/New_York" }), now)).toBe(3600_000);
  });

  it("never returns under a second and has a default with no verdict", () => {
    const now = new Date("2026-11-26T09:59:59.900Z");
    expect(msUntilVerdictBoundary(verdict(), now)).toBe(1000);
    expect(msUntilVerdictBoundary(undefined, now)).toBe(60_000);
  });
});
