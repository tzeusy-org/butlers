import { describe, expect, it } from "vitest";

import { elapsedText } from "./session-elapsed";

const NOW = Date.parse("2026-07-06T12:00:00.000Z");

describe("elapsedText", () => {
  it("returns 'just started' for under a minute", () => {
    const startedAt = new Date(NOW - 5_000).toISOString();
    expect(elapsedText(startedAt, NOW)).toBe("just started");
  });

  it("renders minutes elapsed", () => {
    const startedAt = new Date(NOW - 6 * 60_000).toISOString();
    expect(elapsedText(startedAt, NOW)).toBe("6m elapsed");
  });

  it("renders hours elapsed", () => {
    const startedAt = new Date(NOW - 3 * 3_600_000).toISOString();
    expect(elapsedText(startedAt, NOW)).toBe("3h elapsed");
  });

  it("renders days elapsed", () => {
    const startedAt = new Date(NOW - 2 * 24 * 3_600_000).toISOString();
    expect(elapsedText(startedAt, NOW)).toBe("2d elapsed");
  });

  it("returns null for an invalid date", () => {
    expect(elapsedText("not-a-date", NOW)).toBeNull();
  });

  it("returns null for a future start time", () => {
    const startedAt = new Date(NOW + 60_000).toISOString();
    expect(elapsedText(startedAt, NOW)).toBeNull();
  });

  it("defaults `now` to Date.now() when omitted", () => {
    const startedAt = new Date(Date.now() - 5_000).toISOString();
    expect(elapsedText(startedAt)).toBe("just started");
  });
});
