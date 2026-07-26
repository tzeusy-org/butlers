// @vitest-environment jsdom
/**
 * ReviewTimeline — RTL tests (bu-8usmq).
 *
 * Regression guard: the component previously hard-coded map0..map4 plus
 * Math.min(mindMaps.length, 5), silently dropping reviews from any active mind
 * map beyond the first five. These tests pin that ALL active maps contribute
 * their reviews — no fixed cap, none silently dropped — while keeping the
 * grouped-by-time-period and empty-state behavior intact.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act } from "react";
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import ReviewTimeline from "./ReviewTimeline";
import { AppTimezoneProvider } from "@/components/ui/timezone-context";
import type { MindMap, PendingReviewNode } from "@/api/index.ts";

/**
 * Render with the owner timezone pinned to UTC so the host-local (TZ=UTC)
 * relative fixtures below keep their intended buckets. Owner-tz-varying
 * bucketing is covered in lib/review-buckets.test.ts.
 */
function renderTimeline(onSelectNode = vi.fn()) {
  return render(
    <AppTimezoneProvider timezone="UTC">
      <ReviewTimeline onSelectNode={onSelectNode} />
    </AppTimezoneProvider>,
  );
}

// ---------------------------------------------------------------------------
// Mock education hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-education", () => ({
  useMindMaps: vi.fn(),
  useAllPendingReviews: vi.fn(),
}));

import { useMindMaps, useAllPendingReviews } from "@/hooks/use-education";

const mockUseMindMaps = vi.mocked(useMindMaps);
const mockUseAllPendingReviews = vi.mocked(useAllPendingReviews);

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeMap(id: string, title: string): MindMap {
  return {
    id,
    title,
    root_node_id: null,
    status: "active",
    created_at: "",
    updated_at: "",
    nodes: [],
    edges: [],
  };
}

/** A pending review due ~1 day from "now" (lands in the "This Week" bucket). */
function makeReview(
  nodeId: string,
  label: string,
  overrides: Partial<PendingReviewNode> = {},
): PendingReviewNode {
  const next = new Date();
  next.setDate(next.getDate() + 1);
  return {
    node_id: nodeId,
    label,
    ease_factor: 2.5,
    repetitions: 1,
    next_review_at: next.toISOString(),
    mastery_status: "reviewing",
    mastery_score: 0.62,
    ...overrides,
  };
}

type ReviewResult = ReturnType<typeof useAllPendingReviews>[number];

/**
 * Mock useAllPendingReviews so each map id returns a single review whose label
 * is derived from the map id. This lets each test assert per-map presence and
 * matches the production hook's "one result per id, same order" contract.
 */
function mockReviewsPerMap(maps: MindMap[]) {
  mockUseAllPendingReviews.mockImplementation((mapIds: string[]) =>
    mapIds.map(
      (id) =>
        ({
          data: [makeReview(`${id}-n1`, `Review for ${id}`)],
          isLoading: false,
        }) as unknown as ReviewResult,
    ),
  );
  mockUseMindMaps.mockReturnValue({
    data: { data: maps },
  } as unknown as ReturnType<typeof useMindMaps>);
}

beforeEach(() => {
  // Use mockReset (not clearAllMocks) so both call history AND any prior
  // implementation are dropped per test. Every test re-applies its own mock
  // implementation, so no shared default leaks across cases.
  mockUseMindMaps.mockReset();
  mockUseAllPendingReviews.mockReset();
});

afterEach(() => {
  cleanup();
});

describe("ReviewTimeline — renders all active mind maps", () => {
  it("renders reviews from every map when there are more than 5 (7 maps)", () => {
    const maps = Array.from({ length: 7 }, (_, i) =>
      makeMap(`map-${i}`, `Topic ${i}`),
    );
    mockReviewsPerMap(maps);

    renderTimeline();

    // Every one of the 7 maps must contribute its review — including maps 5 & 6,
    // which the old map0..map4 / Math.min(...,5) cap silently dropped.
    for (let i = 0; i < 7; i++) {
      expect(screen.getByText(`Review for map-${i}`)).toBeTruthy();
      expect(screen.getByText(`Topic ${i}`)).toBeTruthy();
    }
  });

  it("forwards every map id to useAllPendingReviews (no fixed cap)", () => {
    const maps = Array.from({ length: 7 }, (_, i) =>
      makeMap(`map-${i}`, `Topic ${i}`),
    );
    mockReviewsPerMap(maps);

    renderTimeline();

    expect(mockUseAllPendingReviews).toHaveBeenCalledWith(
      maps.map((m) => m.id),
    );
  });

  it("still renders correctly with fewer than 5 maps (2 maps)", () => {
    const maps = [makeMap("map-a", "Alpha"), makeMap("map-b", "Beta")];
    mockReviewsPerMap(maps);

    renderTimeline();

    expect(screen.getByText("Review for map-a")).toBeTruthy();
    expect(screen.getByText("Review for map-b")).toBeTruthy();
    expect(screen.getByText("Alpha")).toBeTruthy();
    expect(screen.getByText("Beta")).toBeTruthy();
  });

  it("shows the empty state when no maps have pending reviews", () => {
    const maps = [makeMap("map-a", "Alpha")];
    mockUseMindMaps.mockReturnValue({
      data: { data: maps },
    } as unknown as ReturnType<typeof useMindMaps>);
    mockUseAllPendingReviews.mockImplementation((mapIds: string[]) =>
      mapIds.map(
        () => ({ data: [], isLoading: false }) as unknown as ReviewResult,
      ),
    );

    renderTimeline();

    expect(screen.getByText(/no reviews scheduled/i)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// bu-86c4c.1 (truth amnesty): mastery badge previously fabricated a fixed
// 50%/100% split from mastery_status alone — every non-mastered node showed
// exactly "50%" regardless of its real mastery_score. These pin the real-data
// path and the honest fallback when no score is available.
// ---------------------------------------------------------------------------

describe("ReviewTimeline — mastery badge", () => {
  it("renders the real mastery_score percentage when present", () => {
    const maps = [makeMap("map-a", "Alpha")];
    mockUseMindMaps.mockReturnValue({
      data: { data: maps },
    } as unknown as ReturnType<typeof useMindMaps>);
    mockUseAllPendingReviews.mockImplementation((mapIds: string[]) =>
      mapIds.map(
        () =>
          ({
            data: [makeReview("map-a-n1", "Closures", { mastery_score: 0.37 })],
            isLoading: false,
          }) as unknown as ReviewResult,
      ),
    );

    renderTimeline();

    // 0.37 * 100 rounded, never the fabricated 50%.
    expect(screen.getByText("37%")).toBeTruthy();
  });

  it("falls back to the mastery status label when mastery_score is unavailable", () => {
    const maps = [makeMap("map-a", "Alpha")];
    mockUseMindMaps.mockReturnValue({
      data: { data: maps },
    } as unknown as ReturnType<typeof useMindMaps>);
    mockUseAllPendingReviews.mockImplementation((mapIds: string[]) =>
      mapIds.map(
        () =>
          ({
            data: [
              makeReview("map-a-n1", "Closures", {
                mastery_status: "learning",
                mastery_score: null,
              }),
            ],
            isLoading: false,
          }) as unknown as ReviewResult,
      ),
    );

    renderTimeline();

    expect(screen.queryByText(/%/)).toBeNull();
    expect(screen.getByText("learning")).toBeTruthy();
  });
});

describe("ReviewTimeline — actionable review controls", () => {
  it("uses a native keyboard-accessible control that emits the owning map and node", async () => {
    const onSelectNode = vi.fn();
    const maps = [makeMap("map-a", "Alpha")];
    mockUseMindMaps.mockReturnValue({
      data: { data: maps },
    } as unknown as ReturnType<typeof useMindMaps>);
    mockUseAllPendingReviews.mockImplementation((mapIds: string[]) =>
      mapIds.map(
        () =>
          ({
            data: [makeReview("node-closures", "Closures")],
            isLoading: false,
          }) as unknown as ReviewResult,
      ),
    );

    renderTimeline(onSelectNode);

    const review = screen.getByRole("button", {
      name: "Open Closures in Alpha",
    });
    expect(review.tagName).toBe("BUTTON");

    review.focus();
    await userEvent.keyboard("{Enter}");

    expect(onSelectNode).toHaveBeenCalledWith({
      mindMapId: "map-a",
      nodeId: "node-closures",
    });
  });
});

// ---------------------------------------------------------------------------
// j/k queue keyboard path (bu-mmdef, keyboard chassis remainder) -- the
// reviews queue was Tab-only. useListTriage's own navigation mechanics are
// unit-tested directly in use-list-triage.test.tsx; only the wiring (real
// DOM focus lands on the right row, in flattened top-to-bottom order across
// group boundaries) is covered here, per the #3586 focus-reality doctrine.
// ---------------------------------------------------------------------------

describe("ReviewTimeline — j/k queue keyboard path (bu-mmdef)", () => {
  function press(key: string) {
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
    });
  }

  it("j moves real DOM focus onto the first review row", () => {
    const maps = [makeMap("map-a", "Alpha")];
    mockUseMindMaps.mockReturnValue({
      data: { data: maps },
    } as unknown as ReturnType<typeof useMindMaps>);
    mockUseAllPendingReviews.mockImplementation((mapIds: string[]) =>
      mapIds.map(
        () =>
          ({
            data: [makeReview("node-1", "First"), makeReview("node-2", "Second")],
            isLoading: false,
          }) as unknown as ReviewResult,
      ),
    );

    renderTimeline();
    press("j");

    const rows = screen.getAllByTestId("review-entry-row");
    expect(rows.length).toBe(2);
    expect(document.activeElement).toBe(rows[0]);
    expect(document.activeElement?.getAttribute("data-review-key")).toBe("map-a-node-1");
  });

  it("j then j again moves focus to the next row, and k moves it back", () => {
    const maps = [makeMap("map-a", "Alpha")];
    mockUseMindMaps.mockReturnValue({
      data: { data: maps },
    } as unknown as ReturnType<typeof useMindMaps>);
    mockUseAllPendingReviews.mockImplementation((mapIds: string[]) =>
      mapIds.map(
        () =>
          ({
            data: [makeReview("node-1", "First"), makeReview("node-2", "Second")],
            isLoading: false,
          }) as unknown as ReviewResult,
      ),
    );

    renderTimeline();
    press("j");
    press("j");

    expect(document.activeElement?.getAttribute("data-review-key")).toBe("map-a-node-2");

    press("k");

    expect(document.activeElement?.getAttribute("data-review-key")).toBe("map-a-node-1");
  });

  it("j/k roves across group boundaries in flattened top-to-bottom order", () => {
    // One review due today (Today bucket) and one due next week (This Week
    // bucket) -- two separate <Card> groups. The cursor must still move
    // linearly from the first group's last row into the second group's
    // first row.
    const maps = [makeMap("map-a", "Alpha")];
    mockUseMindMaps.mockReturnValue({
      data: { data: maps },
    } as unknown as ReturnType<typeof useMindMaps>);
    const now = new Date();
    const todayReview = makeReview("node-today", "Due today", {
      next_review_at: now.toISOString(),
    });
    const laterDate = new Date(now);
    laterDate.setDate(laterDate.getDate() + 3);
    const laterReview = makeReview("node-later", "Due later", {
      next_review_at: laterDate.toISOString(),
    });
    mockUseAllPendingReviews.mockImplementation((mapIds: string[]) =>
      mapIds.map(
        () =>
          ({
            data: [todayReview, laterReview],
            isLoading: false,
          }) as unknown as ReviewResult,
      ),
    );

    renderTimeline();
    press("j");
    expect(document.activeElement?.getAttribute("data-review-key")).toBe("map-a-node-today");

    press("j");
    expect(document.activeElement?.getAttribute("data-review-key")).toBe("map-a-node-later");
  });

  it("publishes the j/k bindings to the footer hint strip", () => {
    const maps = [makeMap("map-a", "Alpha")];
    mockReviewsPerMap(maps);

    renderTimeline();

    const hint = screen.getByRole("note", { name: /keyboard shortcuts for this list/i });
    expect(hint.textContent).toContain("Next item");
    expect(hint.textContent).toContain("Previous item");
  });
});
