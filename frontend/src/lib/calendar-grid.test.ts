import { addDays, addWeeks, format, startOfWeek } from "date-fns";
import { describe, expect, it } from "vitest";

import {
  dateTimeLocalToIso,
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
  tzCalendarWindow,
  tzDateTimeLocalInput,
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

describe("tzDateTimeLocalInput / dateTimeLocalToIso", () => {
  it("prefills a datetime-local input in the workspace timezone, not local", () => {
    // 09:00Z is 17:00 in SGT and 04:00 in NYC.
    const iso = "2026-03-01T09:00:00Z";
    expect(tzDateTimeLocalInput(iso, SGT)).toBe("2026-03-01T17:00");
    expect(tzDateTimeLocalInput(iso, NYC)).toBe("2026-03-01T04:00");
  });

  it("parses a wall-clock value as an instant in the workspace timezone", () => {
    // 17:00 wall clock in SGT (UTC+8) is 09:00Z.
    expect(dateTimeLocalToIso("2026-03-01T17:00", SGT)).toBe(
      "2026-03-01T09:00:00.000Z",
    );
    // The same wall-clock numbers mean a different instant in NYC (UTC-5).
    expect(dateTimeLocalToIso("2026-03-01T17:00", NYC)).toBe(
      "2026-03-01T22:00:00.000Z",
    );
  });

  it("round-trips a form value through a non-local timezone", () => {
    // Editing "2pm" in the workspace zone must come back as 2pm in that zone,
    // independent of the browser's local zone.
    const tz = SGT;
    const wall = "2026-07-04T14:00";
    const iso = dateTimeLocalToIso(wall, tz);
    expect(iso).not.toBeNull();
    expect(tzDateTimeLocalInput(iso as string, tz)).toBe(wall);
  });

  it("returns null/fallback for blank or unparseable values", () => {
    expect(dateTimeLocalToIso("", SGT)).toBeNull();
    expect(dateTimeLocalToIso("   ", SGT)).toBeNull();
    expect(tzDateTimeLocalInput(null, SGT)).toBe("");
    expect(tzDateTimeLocalInput("not-a-date", SGT, "—")).toBe("—");
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

describe("tzCalendarWindow", () => {
  it("anchors the week window/columns in the workspace timezone", () => {
    // Mid-week anchor; workspace tz = NYC (UTC-5, no DST this week).
    const anchor = new Date("2026-02-04T12:00:00Z"); // Wed
    const { start, end, queryStart, queryEnd } = tzCalendarWindow(
      "week",
      anchor,
      NYC,
    );

    // 7 visible columns keyed the way the grid keys them (browser-local
    // yyyy-MM-dd), matching tzDayKey() bucketing: Mon Feb 2 .. Sun Feb 8.
    const dayKeys = Array.from({ length: 7 }, (_, i) =>
      format(addDays(start, i), "yyyy-MM-dd"),
    );
    expect(dayKeys).toEqual([
      "2026-02-02",
      "2026-02-03",
      "2026-02-04",
      "2026-02-05",
      "2026-02-06",
      "2026-02-07",
      "2026-02-08",
    ]);
    expect(format(end, "yyyy-MM-dd")).toBe("2026-02-09");

    // Backend query bounds are the workspace-tz (NYC, -5) midnight instants.
    expect(queryStart).toBe("2026-02-02T05:00:00.000Z");
    expect(queryEnd).toBe("2026-02-09T05:00:00.000Z");
  });

  it("keeps a workspace-tz Monday-morning event (prior Sunday in UTC) inside the window", () => {
    // Workspace tz = SGT (UTC+8). Anchor is Wed of the SGT week.
    const anchor = new Date("2026-02-04T00:00:00+08:00");
    const { queryStart, queryEnd } = tzCalendarWindow("week", anchor, SGT);

    // Week is Mon Feb 2 .. Sun Feb 8 in SGT; the window opens at SGT midnight
    // Monday == 2026-02-01T16:00:00Z (still the prior Sunday in UTC).
    expect(queryStart).toBe("2026-02-01T16:00:00.000Z");
    expect(queryEnd).toBe("2026-02-08T16:00:00.000Z");

    // An event at 00:30 Monday SGT is 2026-02-01T16:30:00Z — Sunday in UTC, yet
    // it falls inside the workspace-tz window (a browser-local UTC window would
    // have excluded it) and buckets to the visible Monday column.
    const event = "2026-02-01T16:30:00Z";
    const eventMs = new Date(event).getTime();
    expect(eventMs).toBeGreaterThanOrEqual(new Date(queryStart).getTime());
    expect(eventMs).toBeLessThan(new Date(queryEnd).getTime());
    expect(tzDayKey(event, SGT)).toBe("2026-02-02");
  });

  it("matches plain browser-local startOfWeek when tz equals the browser zone (no regression)", () => {
    const browserTz =
      Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    const anchor = new Date(2026, 1, 4, 9, 30); // local Wed Feb 4 09:30
    const { start, end, queryStart, queryEnd } = tzCalendarWindow(
      "week",
      anchor,
      browserTz,
    );
    expect(start).toEqual(startOfWeek(anchor, { weekStartsOn: 1 }));
    expect(end).toEqual(addWeeks(start, 1));
    expect(queryStart).toBe(start.toISOString());
    expect(queryEnd).toBe(end.toISOString());
  });
});
