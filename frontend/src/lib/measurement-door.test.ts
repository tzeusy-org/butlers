import { describe, expect, it } from "vitest";

import {
  hasValidMeasurementDateBounds,
  hasValidMeasurementUrlState,
  measurementDoorFromInsight,
  measurementDoorHref,
} from "@/lib/measurement-door";

describe("measurement doors", () => {
  it("accepts only typed gap and drift doors with real ordered date bounds", () => {
    const door = measurementDoorFromInsight("measurement-gap", {
      measurement_door: {
        type: "weight",
        since: "2026-07-01",
        until: "2026-07-20",
      },
    });

    expect(door).toEqual({ type: "weight", since: "2026-07-01", until: "2026-07-20" });
    expect(measurementDoorHref(door!)).toBe(
      "/health/measurements?type=weight&since=2026-07-01&until=2026-07-20",
    );
  });

  it.each([
    ["measurement", { measurement_door: { type: "weight", since: "2026-07-01", until: "2026-07-20" } }],
    ["measurement-gap", { href: "https://untrusted.example", measurement_door: { type: "weight", since: "2026-02-30", until: "2026-07-20" } }],
    ["correlation-drift", { measurement_door: { type: "weight", since: "2026-07-20", until: "2026-07-01" } }],
    ["measurement-gap", { measurement_door: { type: "weight", since: "", until: "2026-07-20" } }],
  ])("rejects an ineligible or malformed door (%s)", (category, metadata) => {
    expect(measurementDoorFromInsight(category, metadata)).toBeNull();
  });

  it("allows absent chart date filters independently but rejects invalid ones", () => {
    expect(hasValidMeasurementDateBounds("", "2026-07-20")).toBe(true);
    expect(hasValidMeasurementDateBounds("2026-02-30", "")).toBe(false);
    expect(hasValidMeasurementDateBounds("2026-07-20", "2026-07-01")).toBe(false);
  });

  it("accepts URL state only for a chart-eligible type and valid bounds", () => {
    const chartEligibleTypes = new Set(["weight"]);

    expect(
      hasValidMeasurementUrlState("weight", "2026-07-01", "2026-07-20", chartEligibleTypes),
    ).toBe(true);
    expect(hasValidMeasurementUrlState("", "", "", chartEligibleTypes)).toBe(true);
    expect(
      hasValidMeasurementUrlState("recovery_note", "2026-07-01", "2026-07-20", chartEligibleTypes),
    ).toBe(false);
    expect(
      hasValidMeasurementUrlState("weight", "2026-07-20", "2026-07-01", chartEligibleTypes),
    ).toBe(false);
  });
});
