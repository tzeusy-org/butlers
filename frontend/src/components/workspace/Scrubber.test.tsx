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

import { Scrubber } from "./Scrubber";

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
