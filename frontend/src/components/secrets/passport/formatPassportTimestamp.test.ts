// ---------------------------------------------------------------------------
// formatPassportTimestamp tests — bu-eptoz review follow-up
//
// Coverage:
//   - null/undefined pass through as null
//   - already-formatted / placeholder strings pass through unchanged
//   - a raw ISO datetime formats identically regardless of the viewer's
//     host/browser timezone (must match the backend's UTC-based
//     `_format_probe_time` — see atoms.tsx docstring). Before this fix, the
//     function used local (`getHours`/`getMinutes`) getters, so ISSUED /
//     LAST VERIFIED could silently disagree with the probe block's "verified
//     14:21 today" for the same instant whenever the host clock wasn't UTC.
//     vitest pins TZ=UTC (vite.config.ts), which made that regression
//     invisible to the existing suite — this test varies TZ explicitly to
//     close that gap.
// ---------------------------------------------------------------------------

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatPassportTimestamp } from "./atoms.tsx";

describe("formatPassportTimestamp: pass-through", () => {
  it("returns null for null/undefined", () => {
    expect(formatPassportTimestamp(null)).toBeNull();
    expect(formatPassportTimestamp(undefined)).toBeNull();
  });

  it("passes through already-formatted / placeholder strings unchanged", () => {
    expect(formatPassportTimestamp("14:21 today")).toBe("14:21 today");
    expect(formatPassportTimestamp("yesterday 09:08")).toBe("yesterday 09:08");
    expect(formatPassportTimestamp("—")).toBe("—");
  });

  it("returns the raw string unchanged if it fails to parse as a date", () => {
    expect(formatPassportTimestamp("2026-13-99T99:99:99")).toBe("2026-13-99T99:99:99");
  });
});

describe("formatPassportTimestamp: timezone parity with backend", () => {
  const originalTZ = process.env.TZ;

  beforeEach(() => {
    // "now" is 2026-07-06T02:00:00Z — well before the raw timestamp below in
    // UTC terms, but AFTER it in several local timezones, which is exactly
    // the kind of skew that broke "today" vs "yesterday" pre-fix.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-07T02:00:00.000Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
    process.env.TZ = originalTZ;
  });

  it("formats identically across viewer timezones (UTC-anchored, not host-local)", () => {
    const raw = "2026-07-06T23:50:00.000000+00:00";
    const results = new Set<string | null>();
    for (const tz of ["UTC", "Asia/Singapore", "America/Los_Angeles", "Pacific/Kiritimati"]) {
      process.env.TZ = tz;
      results.add(formatPassportTimestamp(raw));
    }
    // Every timezone must agree — proves the result doesn't depend on the
    // viewer's host clock, matching the backend's UTC-only `_format_probe_time`.
    expect(results.size).toBe(1);
    expect([...results][0]).toBe("yesterday 23:50");
  });

  it("labels a same-UTC-calendar-day timestamp as 'today' regardless of host timezone", () => {
    const raw = "2026-07-07T01:00:00.000000+00:00";
    for (const tz of ["UTC", "Asia/Singapore", "America/Los_Angeles"]) {
      process.env.TZ = tz;
      expect(formatPassportTimestamp(raw)).toBe("01:00 today");
    }
  });
});
