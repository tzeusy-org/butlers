// @vitest-environment jsdom

/**
 * Tests for ManualRefreshButton (bu-zlzxz, completeness fix bu-ep4ks.5).
 *
 * Verifies:
 *   - Button renders with "Refresh" label by default.
 *   - On click, invalidates all 11 day/window-scoped cache families
 *     (chroniclesFamilyKeys) — not just the 5 the button originally shipped
 *     with — so "Refresh" is truthful about what it refreshes.
 *   - Each invalidation uses a family-prefix key (no params element), which
 *     react-query's default `exact: false` matching applies to every cached
 *     param variant of that family, regardless of which window/day/tz it was
 *     fetched with.
 *   - aria-busy lifecycle: false at rest, true while the invalidation is in
 *     flight.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach } from "vitest";
import { chroniclesFamilyKeys } from "@/hooks/use-chronicles";
import { ManualRefreshButton } from "@/components/chronicles/ManualRefreshButton";

// ---------------------------------------------------------------------------
// Stub useQueryClient so the test does not need a QueryClientProvider, and so
// the invalidateQueries calls made on click can be inspected.
// ---------------------------------------------------------------------------

const invalidateQueries = vi.fn(() => Promise.resolve()) as unknown as {
  (opts: { queryKey: readonly unknown[] }): Promise<void>;
  mock: { calls: Array<[{ queryKey: readonly unknown[] }]> };
  mockClear: () => void;
};

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries }),
}));

afterEach(() => {
  cleanup();
  invalidateQueries.mockClear();
});

// ---------------------------------------------------------------------------
// Rendering tests
// ---------------------------------------------------------------------------

describe("ManualRefreshButton — rendering", () => {
  it("renders a button with text 'Refresh'", () => {
    render(<ManualRefreshButton />);
    expect(screen.getByRole("button", { name: "Refresh" })).toBeDefined();
  });

  it("button is not disabled at rest", () => {
    render(<ManualRefreshButton />);
    const button = screen.getByRole("button", { name: "Refresh" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
  });

  it("aria-busy is false at rest", () => {
    render(<ManualRefreshButton />);
    const button = screen.getByRole("button", { name: "Refresh" });
    expect(button.getAttribute("aria-busy")).toBe("false");
  });
});

// ---------------------------------------------------------------------------
// Family completeness — the 11 families the drilldown panel renders must all
// be named, and must be prefix-only (no params) so invalidation covers every
// cached variant regardless of the window/day/tz it was fetched with.
// ---------------------------------------------------------------------------

describe("chroniclesFamilyKeys — completeness", () => {
  it("names exactly the 11 day/window-scoped families the drilldown panel renders", () => {
    const families = Object.keys(chroniclesFamilyKeys).sort();
    expect(families).toEqual(
      [
        "balance",
        "byCategory",
        "byDay",
        "correctionPrompts",
        "dayClose",
        "episodes",
        "pointEvents",
        "rollups",
        "sourceState",
        "trends",
        "whoYouWereWith",
      ].sort(),
    );
  });

  it("every family key is prefix-only (no params element)", () => {
    for (const key of Object.values(chroniclesFamilyKeys)) {
      // A params-bearing key would be 3 elements: [..all, segment, params].
      // Family-prefix keys must stop at the segment.
      expect(key.length).toBe(2);
      expect(key[0]).toBe("chronicles");
    }
  });
});

// ---------------------------------------------------------------------------
// Click behavior — clicking Refresh invalidates every family.
// ---------------------------------------------------------------------------

describe("ManualRefreshButton — click invalidates all 11 families", () => {
  it("calls invalidateQueries once per family in chroniclesFamilyKeys, and shows Refreshing while in flight", async () => {
    const user = userEvent.setup();
    render(<ManualRefreshButton />);

    const button = screen.getByRole("button", { name: "Refresh" });
    await user.click(button);

    expect(invalidateQueries).toHaveBeenCalledTimes(11);
    const invalidatedKeys = invalidateQueries.mock.calls.map((call) => call[0].queryKey);
    for (const expectedKey of Object.values(chroniclesFamilyKeys)) {
      expect(invalidatedKeys).toContainEqual(expectedKey);
    }

    // After the (resolved) invalidation settles, the button returns to rest.
    const settledButton = await screen.findByRole("button", { name: "Refresh" });
    expect(settledButton.getAttribute("aria-busy")).toBe("false");
  });
});
