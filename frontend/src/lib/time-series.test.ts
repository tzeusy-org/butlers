import { describe, expect, it } from "vitest";
import { DAY_MS, buildTimeSeries, tailLabel } from "./time-series";

const jan1 = Date.UTC(2026, 0, 1);
const mar1 = Date.UTC(2026, 2, 1);
const mar2 = Date.UTC(2026, 2, 2);
const now = Date.UTC(2026, 2, 10);

describe("buildTimeSeries", () => {
  const input = [mar2, jan1, mar1].map((at, i) => ({ at, values: { v: i } }));

  it("orders irregular readings by real time and breaks the line at the gap", () => {
    const s = buildTimeSeries(input, ["v"], { windowEnd: now, maxGapMs: 7 * DAY_MS });
    const xs = s.rows.map((r) => r.x);
    expect(xs).toEqual([...xs].sort((a, b) => a - b));
    // jan1 (v=1), gap null, mar1 (v=2), mar2 (v=0): no gap between the adjacent days
    expect(s.rows.map((r) => r.v)).toEqual([1, null, 2, 0]);
    expect(s.observationCount).toBe(3);
  });

  it("ends the domain at the injected now and reports the tail", () => {
    const s = buildTimeSeries(input, ["v"], { windowEnd: now, maxGapMs: 7 * DAY_MS });
    expect(s.domain).toEqual([jan1, now]);
    expect(s.tailDays).toBe(8);
    expect(tailLabel(s.tailDays)).toBe("last reading 8d ago");
  });

  it("excludes readings older than the window and ignores null-only rows", () => {
    const s = buildTimeSeries(
      [...input, { at: mar2 + 1, values: { v: null } }],
      ["v"],
      { windowStart: mar1, windowEnd: now, maxGapMs: 7 * DAY_MS },
    );
    expect(s.observationCount).toBe(2);
    expect(s.rows).toHaveLength(2);
  });

  it("has no tail label for same-day data and none for empty input", () => {
    expect(tailLabel(0)).toBeNull();
    const empty = buildTimeSeries([], ["v"], { windowEnd: now, maxGapMs: DAY_MS });
    expect(empty.tailDays).toBeNull();
    expect(empty.rows).toEqual([]);
  });
});
