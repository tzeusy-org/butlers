import { describe, expect, it } from "vitest";

import {
  addIsoDays,
  clampIsoDay,
  greetSubject,
  isAtEarliest,
  isAtLatest,
  isValidIsoDay,
  nextIsoDay,
  prevIsoDay,
} from "./chronicles-date-nav";

describe("chronicles-date-nav", () => {
  it("steps days across month and year boundaries", () => {
    expect(nextIsoDay("2026-05-08")).toBe("2026-05-09");
    expect(prevIsoDay("2026-05-08")).toBe("2026-05-07");
    expect(prevIsoDay("2026-05-01")).toBe("2026-04-30");
    expect(nextIsoDay("2026-12-31")).toBe("2027-01-01");
    expect(addIsoDays("2024-02-28", 1)).toBe("2024-02-29"); // leap year
    expect(addIsoDays("2026-05-08", -7)).toBe("2026-05-01");
  });

  it("clamps within [earliest, latest]", () => {
    expect(clampIsoDay("2026-05-20", "2026-01-01", "2026-05-08")).toBe("2026-05-08");
    expect(clampIsoDay("2025-12-01", "2026-01-01", "2026-05-08")).toBe("2026-01-01");
    expect(clampIsoDay("2026-03-03", "2026-01-01", "2026-05-08")).toBe("2026-03-03");
    // null earliest leaves the lower bound open
    expect(clampIsoDay("2020-01-01", null, "2026-05-08")).toBe("2020-01-01");
  });

  it("reports stepper boundaries", () => {
    expect(isAtLatest("2026-05-08", "2026-05-08")).toBe(true);
    expect(isAtLatest("2026-05-07", "2026-05-08")).toBe(false);
    expect(isAtEarliest("2026-01-01", "2026-01-01")).toBe(true);
    expect(isAtEarliest("2026-01-02", "2026-01-01")).toBe(false);
    // no known earliest => never at the earliest
    expect(isAtEarliest("2020-01-01", null)).toBe(false);
    expect(isAtEarliest("2020-01-01", undefined)).toBe(false);
  });

  it("validates ISO day strings and rejects malformed input", () => {
    expect(isValidIsoDay("2026-05-08")).toBe(true);
    expect(isValidIsoDay("2024-02-29")).toBe(true); // real leap day
    expect(isValidIsoDay(null)).toBe(false);
    expect(isValidIsoDay(undefined)).toBe(false);
    expect(isValidIsoDay("")).toBe(false);
    expect(isValidIsoDay("garbage")).toBe(false);
    expect(isValidIsoDay("2026-5-8")).toBe(false); // not zero-padded
    expect(isValidIsoDay("2026-13-01")).toBe(false); // impossible month
    expect(isValidIsoDay("2026-02-30")).toBe(false); // impossible day
    expect(isValidIsoDay("2025-02-29")).toBe(false); // not a leap year
    expect(isValidIsoDay("2026-05-08T00:00:00")).toBe(false); // has time
  });

  it("derives a date-relative greeting subject", () => {
    const latest = "2026-05-08"; // a Friday
    expect(greetSubject("2026-05-08", latest)).toBe("Yesterday");
    expect(greetSubject("2026-05-07", latest)).toBe("Thursday"); // 1 day back
    expect(greetSubject("2026-05-04", latest)).toBe("Monday"); // 4 days back
    expect(greetSubject("2026-05-01", latest)).toBe("1 May"); // 7 days back -> short date
    expect(greetSubject("2026-01-15", latest)).toBe("15 Jan");
  });
});
