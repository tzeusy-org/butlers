/**
 * Decoy for the clock-shift guard (bu-5ii4gj.22). It models a test that baked a "future" fixture
 * date in at authoring time and compares it with the real clock: green today, red once the clock
 * passes the fixture. `scripts/check-clock-shift.mjs` runs it unshifted (must pass) and shifted
 * (must fail), which proves the shifted run still detects an unpinned date test. It is skipped in
 * every ordinary run.
 */
import { describe, expect, it } from "vitest";

const DAY_MS = 86_400_000;

describe.runIf(process.env.CLOCK_SHIFT_DECOY === "1")("clock-shift decoy", () => {
  it("an unpinned fixture date 30 days ahead is still in the future", () => {
    // The unshifted wall clock, read the way a fixture author read it on the day.
    const authoredAt = performance.timeOrigin;
    const fixture = new Date(authoredAt + 30 * DAY_MS);
    expect(fixture.getTime()).toBeGreaterThan(Date.now());
  });
});
