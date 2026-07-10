import { describe, expect, it } from "vitest";

import {
  DOW_LABELS,
  daysFromDowMask,
  dowMaskFromDays,
  formatDowMask,
  formatWindow,
  formatWindowTime,
  toApiTime,
  validateScheduleDraft,
  type ScheduleDraft,
} from "./work-schedule-utils";

describe("dow_mask conversions", () => {
  it("Monday is bit 0", () => {
    expect(dowMaskFromDays([0])).toBe(0b0000001);
    expect(daysFromDowMask(0b0000001)).toEqual([0]);
  });

  it("Sunday is bit 6", () => {
    expect(dowMaskFromDays([6])).toBe(0b1000000);
    expect(daysFromDowMask(0b1000000)).toEqual([6]);
  });

  it("Mon–Fri round-trips", () => {
    const mask = dowMaskFromDays([0, 1, 2, 3, 4]);
    expect(mask).toBe(0b0011111);
    expect(daysFromDowMask(mask)).toEqual([0, 1, 2, 3, 4]);
  });

  it("ignores out-of-range day indices", () => {
    expect(dowMaskFromDays([0, 7, -1, 6])).toBe(0b1000001);
  });

  it("has seven labels in ISO order", () => {
    expect(DOW_LABELS).toEqual(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]);
  });
});

describe("formatDowMask", () => {
  it("renders a contiguous range with an en-dash", () => {
    expect(formatDowMask(0b0011111)).toBe("Mon–Fri");
  });

  it("renders non-contiguous days comma-separated", () => {
    expect(formatDowMask(dowMaskFromDays([0, 2, 4]))).toBe("Mon, Wed, Fri");
  });

  it("mixes ranges and singletons", () => {
    // Mon,Tue,Wed + Fri
    expect(formatDowMask(dowMaskFromDays([0, 1, 2, 4]))).toBe("Mon–Wed, Fri");
  });

  it("empty mask renders empty string", () => {
    expect(formatDowMask(0)).toBe("");
  });
});

describe("time formatting", () => {
  it("strips seconds for display", () => {
    expect(formatWindowTime("09:30:00")).toBe("09:30");
    expect(formatWindowTime("19:30")).toBe("19:30");
  });

  it("formatWindow joins with an en-dash", () => {
    expect(formatWindow("09:30:00", "19:30:00")).toBe("09:30–19:30");
  });

  it("toApiTime adds seconds when absent", () => {
    expect(toApiTime("09:30")).toBe("09:30:00");
    expect(toApiTime("09:30:15")).toBe("09:30:15");
  });
});

describe("validateScheduleDraft", () => {
  const base: ScheduleDraft = {
    dowMask: 0b0011111,
    windowStart: "09:30",
    windowEnd: "19:30",
    label: "Work at Acme",
  };

  it("accepts a valid draft", () => {
    expect(validateScheduleDraft(base)).toBeNull();
  });

  it("rejects no days", () => {
    expect(validateScheduleDraft({ ...base, dowMask: 0 })).toMatch(/at least one day/i);
  });

  it("rejects a blank label", () => {
    expect(validateScheduleDraft({ ...base, label: "  " })).toMatch(/label/i);
  });

  it("rejects an inverted window", () => {
    expect(
      validateScheduleDraft({ ...base, windowStart: "19:30", windowEnd: "09:30" }),
    ).toMatch(/after start/i);
  });

  it("rejects an equal window", () => {
    expect(
      validateScheduleDraft({ ...base, windowStart: "09:30", windowEnd: "09:30" }),
    ).toMatch(/after start/i);
  });

  it("rejects malformed times", () => {
    expect(validateScheduleDraft({ ...base, windowStart: "" })).toMatch(/start and end/i);
  });
});
