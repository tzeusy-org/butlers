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

describe("entryDaySegments", () => {
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
