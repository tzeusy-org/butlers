import { describe, expect, it } from "vitest";

import {
  HOUR_HEIGHT_PX,
  MINUTES_PER_DAY,
  normalizeDragWindow,
  offsetToMinutes,
  resizeWindowEnd,
  shiftWindow,
  snapMinutes,
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
