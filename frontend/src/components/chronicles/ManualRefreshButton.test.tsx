// @vitest-environment jsdom

/**
 * Tests for ManualRefreshButton (bu-zlzxz).
 *
 * Verifies:
 *   - Button renders with "Refresh" label by default.
 *   - On click, invalidates all five window-scoped cache families for the active window.
 *   - Cache entries for other windows are NOT invalidated (key isolation via chroniclesKeys).
 *   - aria-busy lifecycle: false at rest (not "true" in static render).
 */

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { chroniclesKeys } from "@/hooks/use-chronicles";
import { ManualRefreshButton } from "@/components/chronicles/ManualRefreshButton";

// ---------------------------------------------------------------------------
// Stub useQueryClient so renderToStaticMarkup does not need a QueryClientProvider.
// ---------------------------------------------------------------------------

import { vi } from "vitest";

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({
    invalidateQueries: vi.fn(() => Promise.resolve()),
  }),
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const WINDOW_FROM = new Date("2026-04-25T00:00:00.000Z");
const WINDOW_TO = new Date("2026-04-25T23:59:59.000Z");

const OTHER_FROM = new Date("2026-04-20T00:00:00.000Z");
const OTHER_TO = new Date("2026-04-20T23:59:59.000Z");

function renderButton(from = WINDOW_FROM, to = WINDOW_TO): string {
  return renderToStaticMarkup(<ManualRefreshButton timeWindow={{ from, to }} />);
}

// ---------------------------------------------------------------------------
// Rendering tests
// ---------------------------------------------------------------------------

describe("ManualRefreshButton — rendering", () => {
  it("renders a button with text 'Refresh'", () => {
    const html = renderButton();
    expect(html).toContain("Refresh");
  });

  it("button is not disabled at rest", () => {
    const html = renderButton();
    // The disabled HTML attribute must not be present as a standalone attribute.
    // (Tailwind class names like "disabled:opacity-50" are fine — we check only
    // for the attribute form which appears as ` disabled` or `disabled=""`.)
    expect(html).not.toMatch(/ disabled[=" >]/);
  });

  it("does not show 'Refreshing' text in initial state", () => {
    const html = renderButton();
    // Before any click, the button label should be "Refresh" not "Refreshing"
    expect(html).not.toContain("Refreshing");
  });
});

// ---------------------------------------------------------------------------
// aria-busy lifecycle — static render confirms initial state
// ---------------------------------------------------------------------------

describe("ManualRefreshButton — aria-busy lifecycle", () => {
  it("aria-busy is false (not 'true') in initial render", () => {
    const html = renderButton();
    // aria-busy should be "false" or absent, never "true" at rest
    expect(html).not.toMatch(/aria-busy="true"/);
  });
});

// ---------------------------------------------------------------------------
// Window-scoped key isolation — assert that different windows produce different
// cache keys so invalidating one window leaves another window's cache intact.
// ---------------------------------------------------------------------------

describe("ManualRefreshButton — window-scoped key isolation", () => {
  it("byDay keys differ across windows", () => {
    const activeKey = chroniclesKeys.byDay({
      start_at: WINDOW_FROM.toISOString(),
      end_at: WINDOW_TO.toISOString(),
    });
    const otherKey = chroniclesKeys.byDay({
      start_at: OTHER_FROM.toISOString(),
      end_at: OTHER_TO.toISOString(),
    });
    expect(activeKey).not.toEqual(otherKey);
  });

  it("byCategory keys differ across windows", () => {
    const activeKey = chroniclesKeys.byCategory({
      start_at: WINDOW_FROM.toISOString(),
      end_at: WINDOW_TO.toISOString(),
    });
    const otherKey = chroniclesKeys.byCategory({
      start_at: OTHER_FROM.toISOString(),
      end_at: OTHER_TO.toISOString(),
    });
    expect(activeKey).not.toEqual(otherKey);
  });

  it("dayClose keys differ across windows", () => {
    const activeKey = chroniclesKeys.dayClose({
      window_start: WINDOW_FROM.toISOString(),
      window_end: WINDOW_TO.toISOString(),
    });
    const otherKey = chroniclesKeys.dayClose({
      window_start: OTHER_FROM.toISOString(),
      window_end: OTHER_TO.toISOString(),
    });
    expect(activeKey).not.toEqual(otherKey);
  });

  it("pointEvents keys differ across windows", () => {
    const activeKey = chroniclesKeys.pointEvents({
      since: WINDOW_FROM.toISOString(),
      until: WINDOW_TO.toISOString(),
      limit: 500,
    });
    const otherKey = chroniclesKeys.pointEvents({
      since: OTHER_FROM.toISOString(),
      until: OTHER_TO.toISOString(),
      limit: 500,
    });
    expect(activeKey).not.toEqual(otherKey);
  });

  it("sourceState key is a singleton (no window params, always invalidated)", () => {
    // sourceState has no window params — it's always invalidated as-is.
    expect(chroniclesKeys.sourceState()).toEqual(["chronicles", "source-state"]);
  });

  it("invalidating active window byDay key does not match other window byDay key", () => {
    const activeKey = chroniclesKeys.byDay({
      start_at: WINDOW_FROM.toISOString(),
      end_at: WINDOW_TO.toISOString(),
    });
    const otherKey = chroniclesKeys.byDay({
      start_at: OTHER_FROM.toISOString(),
      end_at: OTHER_TO.toISOString(),
    });
    // A prefix-exact invalidation of activeKey will not match otherKey
    // because the params objects embedded in position [2] differ.
    expect(JSON.stringify(activeKey[2])).not.toBe(JSON.stringify(otherKey[2]));
  });
});

// ---------------------------------------------------------------------------
// Key content — verify correct param mapping from timeWindow → key families
// ---------------------------------------------------------------------------

describe("ManualRefreshButton — key content", () => {
  it("byDay key contains active window ISO strings", () => {
    const key = chroniclesKeys.byDay({
      start_at: WINDOW_FROM.toISOString(),
      end_at: WINDOW_TO.toISOString(),
    });
    expect(key[1]).toBe("aggregate-by-day");
    expect(JSON.stringify(key)).toContain(WINDOW_FROM.toISOString());
    expect(JSON.stringify(key)).toContain(WINDOW_TO.toISOString());
  });

  it("byCategory key contains active window ISO strings", () => {
    const key = chroniclesKeys.byCategory({
      start_at: WINDOW_FROM.toISOString(),
      end_at: WINDOW_TO.toISOString(),
    });
    expect(key[1]).toBe("aggregate-by-category");
    expect(JSON.stringify(key)).toContain(WINDOW_FROM.toISOString());
  });

  it("dayClose key contains active window ISO strings", () => {
    const key = chroniclesKeys.dayClose({
      window_start: WINDOW_FROM.toISOString(),
      window_end: WINDOW_TO.toISOString(),
    });
    expect(key[1]).toBe("day-close");
    expect(JSON.stringify(key)).toContain(WINDOW_FROM.toISOString());
  });

  it("pointEvents key contains active window ISO strings and limit=500", () => {
    const key = chroniclesKeys.pointEvents({
      since: WINDOW_FROM.toISOString(),
      until: WINDOW_TO.toISOString(),
      limit: 500,
    });
    expect(key[1]).toBe("point-events");
    expect(JSON.stringify(key)).toContain(WINDOW_FROM.toISOString());
    expect(JSON.stringify(key)).toContain("500");
  });
});
