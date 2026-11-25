import { describe, expect, it } from "vitest";

import { DEFAULT_SCROLL_HOUR, dayViewDoor, scrollHourFor } from "@/lib/calendar-doors";

describe("scrollHourFor", () => {
  it("scrolls to half an hour before a valid time", () => {
    expect(scrollHourFor("16:00")).toBe(15.5);
    expect(scrollHourFor("00:10")).toBe(0);
  });

  it("falls back to the default for a missing or malformed time", () => {
    for (const bad of [null, "", "7", "24:00", "12:60", "noon", "12:00:00"]) {
      expect(scrollHourFor(bad)).toBe(DEFAULT_SCROLL_HOUR);
    }
  });
});

describe("dayViewDoor", () => {
  it("round-trips the time it writes", () => {
    const door = dayViewDoor("2026-11-27", "2026-11-27T16:00:00+08:00");
    const time = new URL(door, "http://x").searchParams.get("time");
    expect(scrollHourFor(time)).toBeGreaterThan(0);
  });
});
