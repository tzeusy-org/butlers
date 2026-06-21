import { describe, expect, it } from "vitest";

import {
  formatEventTime,
  HOUR_HEIGHT_PX,
  isoAtMinuteInTz,
  MINUTES_PER_DAY,
  minuteOfDayInTz,
  normalizeDragWindow,
  offsetToMinutes,
  resizeWindowEnd,
  shiftWindow,
  snapMinutes,
  tzDayKey,
} from "./calendar-grid";

describe("snapMinutes", () => {
  it("snaps to the nearest 30-minute boundary", () => {
    expect(snapMinutes(0)).toBe(0);
    expect(snapMinutes(14)).toBe(0);
    expect(snapMinutes(15)).toBe(30);
    expect(snapMinutes(44)).toBe(30);
    expect(snapMinutes(46)).toBe(60);
  });

  it("clamps to the day boundaries", () => {
    expect(snapMinutes(-100)).toBe(0);
    expect(snapMinutes(99_999)).toBe(MINUTES_PER_DAY);
  });

  it("honors a custom step", () => {
    expect(snapMinutes(20, 15)).toBe(15);
    expect(snapMinutes(23, 15)).toBe(30);
  });
});

describe("offsetToMinutes", () => {
  it("maps pixel offset to minutes via the hour height", () => {
    expect(offsetToMinutes(0)).toBe(0);
    expect(offsetToMinutes(HOUR_HEIGHT_PX)).toBe(60);
    expect(offsetToMinutes(HOUR_HEIGHT_PX * 1.5)).toBe(90);
  });
});

describe("normalizeDragWindow", () => {
  it("orders the two marks and snaps them", () => {
    expect(normalizeDragWindow(140, 40)).toEqual({ startMin: 30, endMin: 150 });
  });

  it("enforces a minimum duration when the drag is tiny", () => {
    expect(normalizeDragWindow(60, 65)).toEqual({ startMin: 60, endMin: 90 });
  });

  it("pulls the window back inside the day at the bottom edge", () => {
    expect(normalizeDragWindow(MINUTES_PER_DAY - 5, MINUTES_PER_DAY)).toEqual({
      startMin: MINUTES_PER_DAY - 30,
      endMin: MINUTES_PER_DAY,
    });
  });
});

describe("shiftWindow", () => {
  it("shifts a fixed-duration window and snaps the start", () => {
    expect(shiftWindow(540, 60, 35)).toEqual({ startMin: 570, endMin: 630 });
    expect(shiftWindow(540, 60, -35)).toEqual({ startMin: 510, endMin: 570 });
  });

  it("preserves duration when clamped to the end of the day", () => {
    expect(shiftWindow(MINUTES_PER_DAY - 60, 60, 600)).toEqual({
      startMin: MINUTES_PER_DAY - 60,
      endMin: MINUTES_PER_DAY,
    });
  });

  it("preserves duration when clamped to the start of the day", () => {
    expect(shiftWindow(30, 45, -600)).toEqual({ startMin: 0, endMin: 45 });
  });
});

describe("resizeWindowEnd", () => {
  it("snaps the end and keeps a minimum duration", () => {
    expect(resizeWindowEnd(540, 605)).toBe(600);
    expect(resizeWindowEnd(540, 545)).toBe(570);
  });

  it("never exceeds the day boundary", () => {
    expect(resizeWindowEnd(MINUTES_PER_DAY - 15, MINUTES_PER_DAY + 100)).toBe(MINUTES_PER_DAY);
  });
});

// ---------------------------------------------------------------------------
// Timezone-aware helpers (bu-jtyzs)
//
// These assert behaviour in a fixed, non-UTC workspace timezone so the result
// is independent of the machine running the test — the whole point of the
// migration: render event times in the configured workspace zone, never the
// browser's local zone. Asia/Singapore (UTC+8, no DST) keeps the maths simple.
// ---------------------------------------------------------------------------

const SGT = "Asia/Singapore"; // UTC+8, no DST
const NYC = "America/New_York"; // UTC-5/-4, DST

describe("formatEventTime", () => {
  it("renders an instant in the given workspace timezone, not local", () => {
    // 09:00Z is 17:00 in SGT and 04:00 in NYC.
    const iso = "2026-03-01T09:00:00Z";
    expect(formatEventTime(iso, SGT, "HH:mm")).toBe("17:00");
    expect(formatEventTime(iso, NYC, "HH:mm")).toBe("04:00");
  });

  it("can roll the calendar date across the day boundary", () => {
    // 18:00Z on Mar 1 is 02:00 on Mar 2 in SGT.
    expect(formatEventTime("2026-03-01T18:00:00Z", SGT, "yyyy-MM-dd HH:mm")).toBe(
      "2026-03-02 02:00",
    );
  });

  it("returns the fallback for missing or unparseable input", () => {
    expect(formatEventTime(null, SGT, "HH:mm", "—")).toBe("—");
    expect(formatEventTime("not-a-date", SGT, "HH:mm", "?")).toBe("?");
    expect(formatEventTime("", SGT, "HH:mm")).toBe("");
  });
});

describe("tzDayKey", () => {
  it("buckets late-evening UTC instants into the next day in SGT", () => {
    // 20:00Z Feb 28 is 04:00 Mar 1 in SGT — must bucket as Mar 1.
    expect(tzDayKey("2026-02-28T20:00:00Z", SGT)).toBe("2026-03-01");
    // Same instant is still Feb 28 in NYC.
    expect(tzDayKey("2026-02-28T20:00:00Z", NYC)).toBe("2026-02-28");
  });
});

describe("minuteOfDayInTz", () => {
  it("computes the minute-of-day in the workspace timezone", () => {
    // 17:30 SGT => 17 * 60 + 30.
    expect(minuteOfDayInTz("2026-03-01T09:30:00Z", SGT)).toBe(17 * 60 + 30);
    // Same instant is 04:30 in NYC.
    expect(minuteOfDayInTz("2026-03-01T09:30:00Z", NYC)).toBe(4 * 60 + 30);
  });
});

describe("isoAtMinuteInTz", () => {
  it("is the inverse of minuteOfDayInTz for a wall-clock time", () => {
    // `day` is a browser-local midnight (as produced by the grid's weekDays),
    // so the calendar date is machine-independent.
    const day = new Date(2026, 2, 1);
    // 17:00 wall-clock in SGT on Mar 1 == 09:00Z.
    expect(isoAtMinuteInTz(day, 17 * 60, SGT)).toBe("2026-03-01T09:00:00.000Z");
    expect(minuteOfDayInTz(isoAtMinuteInTz(day, 17 * 60, SGT), SGT)).toBe(17 * 60);
  });

  it("rolls minutes past a full day onto the next calendar date", () => {
    // `day` is a browser-local midnight (as produced by the grid's weekDays),
    // so the calendar date is machine-independent.
    const day = new Date(2026, 2, 1);
    // 24:00 (end-of-day marker) in SGT == 00:00 Mar 2 SGT == 16:00Z Mar 1.
    expect(isoAtMinuteInTz(day, MINUTES_PER_DAY, SGT)).toBe(
      "2026-03-01T16:00:00.000Z",
    );
  });
});
