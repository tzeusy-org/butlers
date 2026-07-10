// ---------------------------------------------------------------------------
// bucketDaysAgo tests [bu-5fwbh]
//
// Regression guard for the UTC-vs-local day-bucketing bug: the previous inline
// implementation in ButlerActivityTab parsed a UTC-anchored `YYYY-MM-DD` bucket
// key as `…T00:00:00Z` but then read it back with local Date getters. For any
// viewer west of UTC that rolled the bucket onto the previous calendar day,
// shifting the whole daily histogram by one slot. vitest pins TZ=UTC
// (vite.config.ts), so the bug was invisible to the suite — these tests vary
// process.env.TZ explicitly to close that gap.
// ---------------------------------------------------------------------------

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { bucketDaysAgo } from "./daily-buckets";

const TIMEZONES = ["UTC", "Asia/Singapore", "America/Los_Angeles", "Pacific/Kiritimati"];

describe("bucketDaysAgo", () => {
  it("returns null for an unparseable key", () => {
    expect(bucketDaysAgo("not-a-date", new Date("2026-06-10T00:00:00Z"))).toBeNull();
  });

  it("is 0 for today (UTC) and 1 for yesterday", () => {
    const now = new Date("2026-06-10T12:00:00Z");
    expect(bucketDaysAgo("2026-06-10", now)).toBe(0);
    expect(bucketDaysAgo("2026-06-09", now)).toBe(1);
    expect(bucketDaysAgo("2026-06-03", now)).toBe(7);
  });

  describe("host-timezone invariance", () => {
    const originalTZ = process.env.TZ;

    beforeEach(() => {
      vi.useFakeTimers();
      // 2026-06-10 late-UTC evening: in western zones this instant is still
      // 2026-06-10, and in far-eastern zones it has already rolled to 06-11 —
      // exactly the skew that shifted the histogram pre-fix.
      vi.setSystemTime(new Date("2026-06-10T22:30:00.000Z"));
    });

    afterEach(() => {
      vi.useRealTimers();
      process.env.TZ = originalTZ;
    });

    it("computes the same offset in every viewer timezone", () => {
      // A bucket keyed 3 days before the UTC "today" must read as 3 in every
      // zone — the whole point is that the result cannot depend on host TZ.
      for (const key of ["2026-06-10", "2026-06-09", "2026-06-07", "2026-05-31"]) {
        const results = new Set<number | null>();
        for (const tz of TIMEZONES) {
          process.env.TZ = tz;
          results.add(bucketDaysAgo(key, new Date()));
        }
        expect(results.size).toBe(1);
      }
    });

    it("places today's bucket at offset 0 regardless of host timezone", () => {
      for (const tz of TIMEZONES) {
        process.env.TZ = tz;
        // now = 2026-06-10T22:30Z, so the UTC-today bucket is 2026-06-10.
        expect(bucketDaysAgo("2026-06-10", new Date())).toBe(0);
      }
    });
  });
});
