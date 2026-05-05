// @vitest-environment jsdom

/**
 * Tests for the Scrubber component (bu-ig72b.23).
 *
 * Tests cover:
 * 1. snapToNearest helper — snap logic with one, multiple, and no snap points.
 * 2. Scrubber renders — slider visible, labels.
 * 3. Default timezone — tz prop defaults to DEFAULT_TZ if omitted.
 *
 * Strategy: renderToStaticMarkup for structure tests (no effects).
 */

import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { Scrubber, snapToNearest } from "./Scrubber";

// ---------------------------------------------------------------------------
// Window bounds shared across tests
// ---------------------------------------------------------------------------

const WINDOW_START = new Date("2026-04-25T08:00:00Z");
const WINDOW_END = new Date("2026-04-25T20:00:00Z");

// ---------------------------------------------------------------------------
// Scrubber rendering
// ---------------------------------------------------------------------------

describe("Scrubber rendering", () => {
  it("renders the slider input", () => {
    const html = renderToStaticMarkup(
      <Scrubber
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
        snapMs={[]}
        onScrub={vi.fn()}
      />,
    );
    expect(html).toContain('type="range"');
    expect(html).toContain('data-testid="scrubber"');
    expect(html).toContain('data-testid="scrubber-input"');
  });

  it("renders without snapMs prop (optional)", () => {
    const html = renderToStaticMarkup(
      <Scrubber
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
        onScrub={vi.fn()}
      />,
    );
    expect(html).toContain('type="range"');
    expect(html).toContain('data-testid="scrubber"');
  });

  it("slider min equals windowStartMs", () => {
    const html = renderToStaticMarkup(
      <Scrubber
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
        snapMs={[]}
        onScrub={vi.fn()}
      />,
    );
    expect(html).toContain(`min="${WINDOW_START.getTime()}"`);
  });

  it("slider max equals windowEndMs", () => {
    const html = renderToStaticMarkup(
      <Scrubber
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
        snapMs={[]}
        onScrub={vi.fn()}
      />,
    );
    expect(html).toContain(`max="${WINDOW_END.getTime()}"`);
  });

  it("initial slider value equals windowStartMs", () => {
    const html = renderToStaticMarkup(
      <Scrubber
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
        snapMs={[]}
        onScrub={vi.fn()}
      />,
    );
    expect(html).toContain(`value="${WINDOW_START.getTime()}"`);
  });

  it("renders scrubber-label for current position", () => {
    const html = renderToStaticMarkup(
      <Scrubber
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
        snapMs={[]}
        onScrub={vi.fn()}
      />,
    );
    expect(html).toContain('data-testid="scrubber-label"');
  });
});

// ---------------------------------------------------------------------------
// snapMs behaviour
// ---------------------------------------------------------------------------

describe("snapMs behaviour (via label)", () => {
  it("renders with multiple snap points without error", () => {
    const snapMs = [
      new Date("2026-04-25T09:00:00Z").getTime(),
      new Date("2026-04-25T12:00:00Z").getTime(),
      new Date("2026-04-25T18:00:00Z").getTime(),
    ];

    const html = renderToStaticMarkup(
      <Scrubber
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
        snapMs={snapMs}
        onScrub={vi.fn()}
      />,
    );

    expect(html).toContain('type="range"');
  });

  it("renders with epoch-ms snap points matching daily cost midpoints", () => {
    // Verify that the generalized snapMs contract works for cost data.
    // Daily cost snap points are midday timestamps for each day.
    const middays = [
      new Date("2026-04-23T12:00:00Z").getTime(),
      new Date("2026-04-24T12:00:00Z").getTime(),
      new Date("2026-04-25T12:00:00Z").getTime(),
    ];

    const windowStart = new Date("2026-04-23T00:00:00Z");
    const windowEnd = new Date("2026-04-25T23:59:59Z");

    const html = renderToStaticMarkup(
      <Scrubber
        windowStart={windowStart}
        windowEnd={windowEnd}
        snapMs={middays}
        onScrub={vi.fn()}
      />,
    );

    expect(html).toContain('data-testid="scrubber"');
    expect(html).toContain('data-testid="scrubber-label"');
  });
});

// ---------------------------------------------------------------------------
// snapToNearest unit tests — core logic for the snapMs contract
// ---------------------------------------------------------------------------

describe("snapToNearest", () => {
  const T0 = new Date("2026-04-25T12:00:00Z").getTime(); // noon
  const T1 = new Date("2026-04-25T15:00:00Z").getTime(); // 3 pm
  const T2 = new Date("2026-04-25T18:00:00Z").getTime(); // 6 pm

  it("returns null for empty snap array", () => {
    expect(snapToNearest(T0, [])).toBeNull();
  });

  it("returns the only point when one snap point is given", () => {
    expect(snapToNearest(T0, [T1])).toBe(T1);
  });

  it("snaps to the nearest of two points — closer to first", () => {
    // Midpoint between T0 and T1 is at T0+90min. We query T0+60min → closer to T0.
    const query = T0 + 60 * 60 * 1000;
    expect(snapToNearest(query, [T0, T1])).toBe(T0);
  });

  it("snaps to the nearest of two points — closer to second", () => {
    // T0+120min is exactly half-way; T0+121min is marginally closer to T1.
    const query = T0 + 121 * 60 * 1000;
    expect(snapToNearest(query, [T0, T1])).toBe(T1);
  });

  it("snaps to nearest among three points", () => {
    // Query is between T1 and T2 but closer to T2.
    const query = T2 - 30 * 60 * 1000; // 30 min before T2
    expect(snapToNearest(query, [T0, T1, T2])).toBe(T2);
  });

  it("returns exact match when value equals a snap point", () => {
    expect(snapToNearest(T1, [T0, T1, T2])).toBe(T1);
  });

  it("returns first snap point when query is before the window", () => {
    const before = T0 - 1_000_000;
    expect(snapToNearest(before, [T0, T1, T2])).toBe(T0);
  });

  it("returns last snap point when query is after the window", () => {
    const after = T2 + 1_000_000;
    expect(snapToNearest(after, [T0, T1, T2])).toBe(T2);
  });
});
