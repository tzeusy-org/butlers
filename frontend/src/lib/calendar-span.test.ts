import { describe, expect, it } from "vitest";

import {
  bucketEntriesByDay,
  entryContinuation,
  entryDayKeys,
  entryDaySegments,
  MAX_SPAN_DAYS,
  packSegments,
} from "./calendar-span";

const TZ = "UTC";
const entry = (id: string, start: string, end: string) => ({
  entry_id: id,
  start_at: start,
  end_at: end,
});

describe("entryDayKeys", () => {
  it("covers every day a multi-day event touches", () => {
    expect(
      entryDayKeys(
        entry("a", "2026-11-23T20:00:00Z", "2026-11-26T09:00:00Z"),
        TZ,
      ),
    ).toEqual(["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26"]);
  });

  it("treats the end as exclusive, so a midnight end does not claim the next day", () => {
    expect(
      entryDayKeys(
        entry("a", "2026-11-23T00:00:00Z", "2026-11-25T00:00:00Z"),
        TZ,
      ),
    ).toEqual(["2026-11-23", "2026-11-24"]);
  });

  it("falls back to the start day for a zero-length or reversed event", () => {
    expect(
      entryDayKeys(
        entry("a", "2026-11-23T10:00:00Z", "2026-11-23T10:00:00Z"),
        TZ,
      ),
    ).toEqual(["2026-11-23"]);
    expect(
      entryDayKeys(
        entry("a", "2026-11-23T10:00:00Z", "2026-11-20T10:00:00Z"),
        TZ,
      ),
    ).toEqual(["2026-11-23"]);
  });

  it("buckets in the workspace timezone, not UTC", () => {
    // 23:00 UTC is already the next day in Tokyo.
    expect(
      entryDayKeys(
        entry("a", "2026-11-23T23:00:00Z", "2026-11-23T23:30:00Z"),
        "Asia/Tokyo",
      ),
    ).toEqual(["2026-11-24"]);
  });

  it("caps a runaway span", () => {
    expect(
      entryDayKeys(
        entry("a", "2026-01-01T00:00:00Z", "2030-01-01T00:00:00Z"),
        TZ,
      ),
    ).toHaveLength(MAX_SPAN_DAYS);
  });
});

describe("entryDayKeys windowing and caps", () => {
  const long = entry("a", "2026-01-01T00:00:00Z", "2026-04-01T00:00:00Z"); // 90 days

  it("keeps a long event visible in a later window, with markers on both edges", () => {
    const window = { startKey: "2026-03-01", endKey: "2026-03-31" };
    const keys = entryDayKeys(long, TZ, window);
    expect(keys[0]).toBe("2026-03-01");
    expect(keys).toHaveLength(31);
    expect(entryContinuation(long, "2026-03-01", TZ)).toEqual({
      fromPrev: true,
      toNext: true,
    });
    expect(bucketEntriesByDay([long], TZ, window).get("2026-03-31")).toEqual([
      long,
    ]);
  });

  it("discloses a capped span: the last kept day still continues", () => {
    const keys = entryDayKeys(long, TZ);
    expect(keys).toHaveLength(MAX_SPAN_DAYS);
    expect(entryContinuation(long, keys[keys.length - 1], TZ).toNext).toBe(
      true,
    );
  });
});

describe("all-day events read their dates in their own zone", () => {
  const allDay = (start: string, end: string, timezone: string) => ({
    ...entry("d", start, end),
    all_day: true,
    timezone,
  });

  it("does not grow a phantom day for Singapore-midnight storage viewed from UTC", () => {
    // 24 Nov 00:00 to 25 Nov 00:00 in Singapore is 23 Nov 16:00Z to 24 Nov 16:00Z.
    const e = allDay(
      "2026-11-23T16:00:00Z",
      "2026-11-24T16:00:00Z",
      "Asia/Singapore",
    );
    expect(entryDayKeys(e, "UTC")).toEqual(["2026-11-24"]);
    expect(entryDayKeys(e, "America/Los_Angeles")).toEqual(["2026-11-24"]);
  });

  it("handles a negative-offset event zone viewed from a positive-offset workspace", () => {
    // 24 Nov 00:00 to 26 Nov 00:00 in Los Angeles (UTC-8) is 08:00Z to 08:00Z.
    const e = allDay(
      "2026-11-24T08:00:00Z",
      "2026-11-26T08:00:00Z",
      "America/Los_Angeles",
    );
    expect(entryDayKeys(e, "Asia/Singapore")).toEqual([
      "2026-11-24",
      "2026-11-25",
    ]);
  });

  it("falls back to the workspace zone for an unknown event zone", () => {
    const e = allDay(
      "2026-11-24T00:00:00Z",
      "2026-11-25T00:00:00Z",
      "Not/AZone",
    );
    expect(entryDayKeys(e, "UTC")).toEqual(["2026-11-24"]);
  });
});

describe("daylight saving", () => {
  const NY = "America/New_York";

  it("spring forward: the civil day is 23h long, so a midnight-to-midnight event is one day", () => {
    // 8 Mar 2026 00:00 EST is 05:00Z; 9 Mar 00:00 EDT is 04:00Z.
    const e = entry("a", "2026-03-08T05:00:00Z", "2026-03-09T04:00:00Z");
    expect(entryDayKeys(e, NY)).toEqual(["2026-03-08"]);
    const [seg] = entryDaySegments(e, NY);
    expect([seg.startMin, seg.endMin]).toEqual([0, 1440]);
  });

  it("fall back: the 25h day stays one day", () => {
    // 1 Nov 2026 00:00 EDT is 04:00Z; 2 Nov 00:00 EST is 05:00Z.
    const e = entry("a", "2026-11-01T04:00:00Z", "2026-11-02T05:00:00Z");
    expect(entryDayKeys(e, NY)).toEqual(["2026-11-01"]);
  });

  it("an event across the changeover covers each civil day once", () => {
    const e = entry("a", "2026-03-07T15:00:00Z", "2026-03-09T15:00:00Z");
    expect(entryDayKeys(e, NY)).toEqual([
      "2026-03-07",
      "2026-03-08",
      "2026-03-09",
    ]);
  });
});

describe("entryDaySegments", () => {
  it("a zero-length event at midnight is a point, not a 24h block", () => {
    const [seg] = entryDaySegments(
      entry("a", "2026-11-23T00:00:00Z", "2026-11-23T00:00:00Z"),
      TZ,
    );
    expect([seg.startMin, seg.endMin]).toEqual([0, 0]);
  });

  it("a reversed event at midnight is a point too", () => {
    const [seg] = entryDaySegments(
      entry("a", "2026-11-23T00:00:00Z", "2026-11-22T00:00:00Z"),
      TZ,
    );
    expect([seg.startMin, seg.endMin]).toEqual([0, 0]);
  });

  it("clips the first and last day and fills the middle", () => {
    const segs = entryDaySegments(
      entry("a", "2026-11-23T20:00:00Z", "2026-11-25T09:00:00Z"),
      TZ,
    );
    expect(
      segs.map((s) => [
        s.startMin,
        s.endMin,
        s.continuesFromPrev,
        s.continuesToNext,
      ]),
    ).toEqual([
      [1200, 1440, false, true],
      [0, 1440, true, true],
      [0, 540, true, false],
    ]);
  });

  it("keeps a same-day event as one unmarked segment", () => {
    const [seg] = entryDaySegments(
      entry("a", "2026-11-23T09:00:00Z", "2026-11-23T10:30:00Z"),
      TZ,
    );
    expect([
      seg.startMin,
      seg.endMin,
      seg.continuesFromPrev,
      seg.continuesToNext,
    ]).toEqual([540, 630, false, false]);
  });
});

describe("bucketEntriesByDay", () => {
  it("places a multi-day event on every covered day", () => {
    const buckets = bucketEntriesByDay(
      [entry("trip", "2026-11-23T00:00:00Z", "2026-11-26T00:00:00Z")],
      TZ,
    );
    expect([...buckets.keys()]).toEqual([
      "2026-11-23",
      "2026-11-24",
      "2026-11-25",
    ]);
  });
});

describe("entryContinuation", () => {
  it("marks only the days that border the span", () => {
    const e = entry("a", "2026-11-23T00:00:00Z", "2026-11-26T00:00:00Z");
    expect(entryContinuation(e, "2026-11-23", TZ)).toEqual({
      fromPrev: false,
      toNext: true,
    });
    expect(entryContinuation(e, "2026-11-24", TZ)).toEqual({
      fromPrev: true,
      toNext: true,
    });
    expect(entryContinuation(e, "2026-11-25", TZ)).toEqual({
      fromPrev: true,
      toNext: false,
    });
  });
});

describe("packSegments", () => {
  const seg = (id: string, from: string, to: string) =>
    entryDaySegments(
      entry(id, `2026-11-23T${from}:00Z`, `2026-11-23T${to}:00Z`),
      TZ,
    )[0];
  const place = (segments: ReturnType<typeof seg>[]) =>
    Object.fromEntries(
      packSegments(segments).map((p) => [
        p.entry.entry_id,
        [p.column, p.columns],
      ]),
    );

  it("leaves a lone event full width", () => {
    expect(place([seg("a", "09:00", "10:00")])).toEqual({ a: [0, 1] });
  });

  it("puts overlapping events side by side", () => {
    expect(
      place([seg("a", "09:00", "11:00"), seg("b", "10:00", "12:00")]),
    ).toEqual({
      a: [0, 2],
      b: [1, 2],
    });
  });

  it("reuses a freed column and keeps separate clusters independent", () => {
    expect(
      place([
        seg("a", "09:00", "12:00"),
        seg("b", "09:30", "10:00"),
        seg("c", "10:30", "11:00"),
        seg("d", "14:00", "15:00"),
      ]),
    ).toEqual({ a: [0, 2], b: [1, 2], c: [1, 2], d: [0, 1] });
  });

  it("counts a short event as 15 minutes so a neighbour right after does not hide under it", () => {
    expect(
      place([seg("a", "09:00", "09:05"), seg("b", "09:10", "10:00")]),
    ).toEqual({
      a: [0, 2],
      b: [1, 2],
    });
  });
});


describe("DST spring-forward block geometry", () => {
  // America/New_York 2026-03-08: 02:00 does not exist, so the clock jumps 01:59 -> 03:00.
  it("sizes a block by the wall clock the grid draws, not by elapsed time", () => {
    const entry = {
      entry_id: "spring-1",
      start_at: "2026-03-08T06:30:00Z", // 01:30 EST
      end_at: "2026-03-08T07:30:00Z", // 03:30 EDT, one real hour later
      all_day: false,
    };
    const [seg] = entryDaySegments(entry, "America/New_York", {
      startKey: "2026-03-08",
      endKey: "2026-03-08",
    });
    // The grid's rows are wall-clock hours (00..23), so the block runs 01:30 to 03:30 on them.
    expect(seg.startMin).toBe(90);
    expect(seg.endMin).toBe(210);
  });

  it("an event wholly after the gap keeps its true one-hour height", () => {
    const entry = {
      entry_id: "spring-2",
      start_at: "2026-03-08T07:00:00Z", // 03:00 EDT
      end_at: "2026-03-08T08:00:00Z", // 04:00 EDT
      all_day: false,
    };
    const [seg] = entryDaySegments(entry, "America/New_York", {
      startKey: "2026-03-08",
      endKey: "2026-03-08",
    });
    expect(seg.endMin - seg.startMin).toBe(60);
  });
});
