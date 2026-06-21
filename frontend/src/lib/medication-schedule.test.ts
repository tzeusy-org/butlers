import { describe, expect, it } from "vitest";

import { parseScheduleTime } from "@/lib/medication-schedule";

describe("parseScheduleTime", () => {
  it("parses a well-formed HH:MM into minutes-since-midnight", () => {
    expect(parseScheduleTime("00:00")).toBe(0);
    expect(parseScheduleTime("08:00")).toBe(480);
    expect(parseScheduleTime("08:30")).toBe(510);
    expect(parseScheduleTime("23:59")).toBe(1439);
  });

  it("accepts a single-digit hour", () => {
    expect(parseScheduleTime("8:00")).toBe(480);
    expect(parseScheduleTime("9:05")).toBe(545);
  });

  it("trims surrounding whitespace", () => {
    expect(parseScheduleTime("  08:00  ")).toBe(480);
    expect(parseScheduleTime("\t12:15\n")).toBe(735);
  });

  it("rejects out-of-range hours and minutes", () => {
    expect(parseScheduleTime("24:00")).toBeNull();
    expect(parseScheduleTime("25:00")).toBeNull();
    expect(parseScheduleTime("12:60")).toBeNull();
    expect(parseScheduleTime("12:99")).toBeNull();
  });

  it("rejects malformed strings", () => {
    expect(parseScheduleTime("")).toBeNull();
    expect(parseScheduleTime("0800")).toBeNull();
    expect(parseScheduleTime("8")).toBeNull();
    expect(parseScheduleTime("08:0")).toBeNull();
    expect(parseScheduleTime("08:000")).toBeNull();
    expect(parseScheduleTime("8:00am")).toBeNull();
    expect(parseScheduleTime("noon")).toBeNull();
    expect(parseScheduleTime("08-00")).toBeNull();
    expect(parseScheduleTime("08:00:00")).toBeNull();
  });

  it("rejects non-string inputs", () => {
    expect(parseScheduleTime(null)).toBeNull();
    expect(parseScheduleTime(undefined)).toBeNull();
    expect(parseScheduleTime(480)).toBeNull();
    expect(parseScheduleTime({ time: "08:00" })).toBeNull();
    expect(parseScheduleTime(["08:00"])).toBeNull();
  });
});
