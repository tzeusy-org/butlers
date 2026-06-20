import { describe, expect, it } from "vitest";

import { dayWindowInTz } from "./tz-format";

/**
 * dayWindowInTz must match the backend editorial.day_window_utc
 * (datetime.combine(target, time.min, tzinfo=tz)) for every zone, so the
 * drilldown queries the same calendar day the briefing reconstructed. The
 * expected UTC instants below mirror the backend test cases in
 * tests/chronicler/test_editorial.py.
 */
describe("dayWindowInTz / backend parity", () => {
  const cases: Array<[string, string, string, string]> = [
    // tz, day, expected from (UTC), expected to (UTC)
    ["Asia/Singapore", "2026-05-08", "2026-05-07T16:00:00.000Z", "2026-05-08T16:00:00.000Z"],
    ["America/New_York", "2026-05-08", "2026-05-08T04:00:00.000Z", "2026-05-09T04:00:00.000Z"],
    // DST start day (clocks jump forward at 02:00) — matches the backend
    // day_window_utc DST test exactly.
    ["America/New_York", "2026-03-08", "2026-03-08T05:00:00.000Z", "2026-03-09T04:00:00.000Z"],
    // UTC+14: the noon-anchor bug would put this a full day ahead.
    ["Pacific/Kiritimati", "2026-05-08", "2026-05-07T10:00:00.000Z", "2026-05-08T10:00:00.000Z"],
  ];

  it.each(cases)("%s %s -> [%s, %s)", (tz, day, expectedFrom, expectedTo) => {
    const { from, to } = dayWindowInTz(day, tz);
    expect(from.toISOString()).toBe(expectedFrom);
    expect(to.toISOString()).toBe(expectedTo);
  });

  it("spans exactly 24h on a non-DST day", () => {
    const { from, to } = dayWindowInTz("2026-05-08", "Asia/Singapore");
    expect(to.getTime() - from.getTime()).toBe(24 * 60 * 60 * 1000);
  });
});
