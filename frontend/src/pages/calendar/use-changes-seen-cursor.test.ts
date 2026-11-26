import { expect, it } from "vitest";

import { clampSince } from "@/pages/calendar/use-changes-seen-cursor";

const NOW = Date.parse("2026-11-30T12:00:00Z");

it("defaults to the last day, never reaches past the route's 14 day bound, never the future", () => {
  expect(clampSince(null, NOW)).toBe("2026-11-29T12:00:00.000Z");
  expect(clampSince("not a date", NOW)).toBe("2026-11-29T12:00:00.000Z");
  expect(Date.parse(clampSince("2026-01-01T00:00:00Z", NOW))).toBeGreaterThan(
    NOW - 14 * 24 * 60 * 60 * 1000,
  );
  expect(clampSince("2027-01-01T00:00:00Z", NOW)).toBe("2026-11-30T12:00:00.000Z");
  expect(clampSince("2026-11-30T08:00:00Z", NOW)).toBe("2026-11-30T08:00:00.000Z");
});
