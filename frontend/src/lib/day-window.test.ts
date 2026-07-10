// ---------------------------------------------------------------------------
// day-window tests — owner-tz day keys are host-clock-independent [bu-s0d8j]
//
// Repro method (from the bu-5fwbh sweep): vitest pins TZ=UTC (vite.config.ts),
// which hides host-local bucketing bugs. Here we vary process.env.TZ with a
// fixed fake clock and assert every owner-tz helper returns the SAME key across
// hosts — and that a meal timestamped near owner-midnight buckets into the
// owner-tz day, not the host-tz day.
// ---------------------------------------------------------------------------

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { dayKeyInTimeZone, daysAgoISO, todayISO } from "./day-window";

const OWNER_TZ = "Asia/Singapore"; // UTC+8, DEFAULT_TZ
const HOST_TZS = ["UTC", "Asia/Singapore", "America/Los_Angeles", "Pacific/Kiritimati"];

describe("dayKeyInTimeZone", () => {
  const originalTZ = process.env.TZ;
  afterEach(() => {
    process.env.TZ = originalTZ;
  });

  it("buckets a near-owner-midnight instant into the owner-tz day, not the host day", () => {
    // 2026-07-11T16:30:00Z === 2026-07-12 00:30 in Asia/Singapore (UTC+8).
    // A host on UTC would (wrongly, via getDate) call this 2026-07-11.
    const nearMidnight = new Date("2026-07-11T16:30:00.000Z");
    const owner = new Set<string>();
    for (const tz of HOST_TZS) {
      process.env.TZ = tz;
      owner.add(dayKeyInTimeZone(nearMidnight, OWNER_TZ));
    }
    expect(owner.size).toBe(1);
    expect([...owner][0]).toBe("2026-07-12");
  });

  it("keeps a just-before-owner-midnight instant in the earlier owner-tz day", () => {
    // 2026-07-11T15:30:00Z === 2026-07-11 23:30 in Asia/Singapore.
    const beforeMidnight = new Date("2026-07-11T15:30:00.000Z");
    expect(dayKeyInTimeZone(beforeMidnight, OWNER_TZ)).toBe("2026-07-11");
  });

  it("falls back to UTC (never host-local) for a malformed timezone", () => {
    const instant = new Date("2026-07-11T16:30:00.000Z");
    const results = new Set<string>();
    for (const tz of HOST_TZS) {
      process.env.TZ = tz;
      results.add(dayKeyInTimeZone(instant, "Not/AZone"));
    }
    expect(results.size).toBe(1);
    // UTC calendar day of the instant — independent of host tz.
    expect([...results][0]).toBe("2026-07-11");
  });
});

describe("todayISO / daysAgoISO: owner-tz window is host-clock-independent", () => {
  const originalTZ = process.env.TZ;

  beforeEach(() => {
    // "now" is 2026-07-11T16:30:00Z === 2026-07-12 00:30 in Asia/Singapore.
    // The owner has already ticked over to the 12th; a UTC host clock is still
    // on the 11th. The owner-tz window must follow the owner, not the host.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-11T16:30:00.000Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
    process.env.TZ = originalTZ;
  });

  it("todayISO returns the owner-tz today across every host timezone", () => {
    const results = new Set<string>();
    for (const tz of HOST_TZS) {
      process.env.TZ = tz;
      results.add(todayISO(OWNER_TZ));
    }
    expect(results.size).toBe(1);
    expect([...results][0]).toBe("2026-07-12");
  });

  it("daysAgoISO returns owner-tz calendar days back across every host timezone", () => {
    const results = new Set<string>();
    for (const tz of HOST_TZS) {
      process.env.TZ = tz;
      results.add(daysAgoISO(30, OWNER_TZ));
    }
    expect(results.size).toBe(1);
    // 2026-07-12 minus 30 calendar days = 2026-06-12.
    expect([...results][0]).toBe("2026-06-12");
  });

  it("window end (todayISO) and a same-instant meal bucket agree in owner-tz", () => {
    // A meal logged 'now' must land in the day the default window ends on —
    // the seam the bug (owner-tz window, host-local buckets) would split.
    process.env.TZ = "UTC";
    const windowEnd = todayISO(OWNER_TZ);
    const mealBucket = dayKeyInTimeZone(new Date("2026-07-11T16:30:00.000Z"), OWNER_TZ);
    expect(mealBucket).toBe(windowEnd);
    expect(windowEnd).toBe("2026-07-12");
  });
});
