/**
 * Unit tests for QA dossier formatting helpers.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatQaDetectedTime } from "../utils";

describe("formatQaDetectedTime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // Pin "now" to a fixed local moment so today/non-today branches are stable.
    // 2026-05-16 14:19 local time.
    vi.setSystemTime(new Date(2026, 4, 16, 14, 19, 0));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders today's timestamps as time only with lowercase am/pm", () => {
    const ts = new Date(2026, 4, 16, 14, 19, 0).toISOString();
    expect(formatQaDetectedTime(ts)).toBe("2:19 pm");
  });

  it("renders today's morning timestamps as time only with lowercase am", () => {
    const ts = new Date(2026, 4, 16, 9, 5, 0).toISOString();
    expect(formatQaDetectedTime(ts)).toBe("9:05 am");
  });

  it("renders yesterday's timestamps with the ISO-style date prefix", () => {
    const ts = new Date(2026, 4, 15, 14, 19, 0).toISOString();
    expect(formatQaDetectedTime(ts)).toBe("2026-05-15 2:19 pm");
  });

  it("renders last week's timestamps with the ISO-style date prefix", () => {
    const ts = new Date(2026, 4, 9, 8, 7, 0).toISOString();
    expect(formatQaDetectedTime(ts)).toBe("2026-05-09 8:07 am");
  });

  it("zero-pads month and day for non-today dates", () => {
    const ts = new Date(2026, 0, 3, 23, 4, 0).toISOString();
    expect(formatQaDetectedTime(ts)).toBe("2026-01-03 11:04 pm");
  });

  it("returns the raw input when the timestamp is invalid", () => {
    expect(formatQaDetectedTime("not-a-date")).toBe("not-a-date");
  });
});
