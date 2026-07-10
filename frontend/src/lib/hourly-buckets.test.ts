// ---------------------------------------------------------------------------
// bucketHourInZone tests [bu-8ogli]
//
// Regression guard for the UTC-vs-host hour-bucketing bug: ButlerManagementTab
// §5 previously slotted each `hour_start` instant with `new Date(...).getHours()`,
// reading it back in the viewer's HOST timezone. Opening the dashboard from a
// different zone shifted the whole histogram. vitest pins TZ=UTC
// (vite.config.ts), so the skew was invisible to the suite — these tests vary
// process.env.TZ explicitly and assert the owner-zone result never moves.
// ---------------------------------------------------------------------------

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { bucketHourInZone } from "./hourly-buckets";

const HOST_TIMEZONES = ["UTC", "Asia/Singapore", "America/Los_Angeles", "Pacific/Kiritimati"];

describe("bucketHourInZone", () => {
  it("returns null for an unparseable timestamp", () => {
    expect(bucketHourInZone("not-a-date", "Asia/Singapore")).toBeNull();
  });

  it("converts a UTC instant to the owner-zone hour-of-day", () => {
    // 08:00 UTC is 16:00 in Asia/Singapore (+8).
    expect(bucketHourInZone("2026-06-10T08:00:00Z", "Asia/Singapore")).toBe(16);
    // Same instant is 01:00 in America/Los_Angeles (-7 in June, DST).
    expect(bucketHourInZone("2026-06-10T08:00:00Z", "America/Los_Angeles")).toBe(1);
    // Same instant read in UTC is unchanged.
    expect(bucketHourInZone("2026-06-10T08:00:00Z", "UTC")).toBe(8);
  });

  describe("h23 midnight edge", () => {
    it("renders owner-zone midnight as 0, not 24", () => {
      // 16:00 UTC is 00:00 (next day) in Asia/Singapore (+8).
      expect(bucketHourInZone("2026-06-10T16:00:00Z", "Asia/Singapore")).toBe(0);
      // 00:00 UTC read in UTC is 0.
      expect(bucketHourInZone("2026-06-10T00:00:00Z", "UTC")).toBe(0);
    });

    it("renders owner-zone 23:00 as 23", () => {
      // 15:00 UTC is 23:00 in Asia/Singapore (+8).
      expect(bucketHourInZone("2026-06-10T15:00:00Z", "Asia/Singapore")).toBe(23);
    });
  });

  describe("host-timezone invariance", () => {
    const originalTZ = process.env.TZ;

    beforeEach(() => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date("2026-06-10T22:30:00.000Z"));
    });

    afterEach(() => {
      vi.useRealTimers();
      process.env.TZ = originalTZ;
    });

    it("computes the same owner-zone hour in every viewer timezone", () => {
      for (const instant of [
        "2026-06-10T08:00:00Z",
        "2026-06-10T16:00:00Z", // owner-zone midnight
        "2026-06-10T15:00:00Z", // owner-zone 23:00
        "2026-06-10T00:00:00Z",
      ]) {
        const results = new Set<number | null>();
        for (const tz of HOST_TIMEZONES) {
          process.env.TZ = tz;
          results.add(bucketHourInZone(instant, "Asia/Singapore"));
        }
        // One distinct owner-zone hour regardless of the viewer's host zone.
        expect(results.size).toBe(1);
      }
    });
  });
});
