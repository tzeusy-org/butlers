/**
 * Unit tests for QA dossier formatting helpers.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatQaDetectedTime } from "../utils";

describe("formatQaDetectedTime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // Pin "now" to a fixed instant; formatting is evaluated in owner time.
    vi.setSystemTime(new Date("2026-05-16T06:19:00.000Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders today's timestamps as time only with lowercase am/pm", () => {
    const ts = "2026-05-16T06:19:00.000Z";
    expect(formatQaDetectedTime(ts)).toBe("2:19 pm");
  });

  it("renders today's morning timestamps as time only with lowercase am", () => {
    const ts = "2026-05-16T01:05:00.000Z";
    expect(formatQaDetectedTime(ts)).toBe("9:05 am");
  });

  it("renders yesterday's timestamps with the ISO-style date prefix", () => {
    const ts = "2026-05-15T06:19:00.000Z";
    expect(formatQaDetectedTime(ts)).toBe("2026-05-15 2:19 pm");
  });

  it("renders last week's timestamps with the ISO-style date prefix", () => {
    const ts = "2026-05-09T00:07:00.000Z";
    expect(formatQaDetectedTime(ts)).toBe("2026-05-09 8:07 am");
  });

  it("zero-pads month and day for non-today dates", () => {
    const ts = "2026-01-03T15:04:00.000Z";
    expect(formatQaDetectedTime(ts)).toBe("2026-01-03 11:04 pm");
  });

  it("returns the raw input when the timestamp is invalid", () => {
    expect(formatQaDetectedTime("not-a-date")).toBe("not-a-date");
  });
});
