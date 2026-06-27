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
import { render, screen, cleanup } from "@testing-library/react";

import ReviewTimeline from "./ReviewTimeline";
import type { MindMap, PendingReviewNode } from "@/api/index.ts";

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
function makeReview(nodeId: string, label: string): PendingReviewNode {
  const next = new Date();
  next.setDate(next.getDate() + 1);
  return {
    node_id: nodeId,
    label,
    ease_factor: 2.5,
    repetitions: 1,
    next_review_at: next.toISOString(),
    mastery_status: "reviewing",
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

    render(<ReviewTimeline />);

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

    render(<ReviewTimeline />);

    expect(mockUseAllPendingReviews).toHaveBeenCalledWith(
      maps.map((m) => m.id),
    );
  });

  it("still renders correctly with fewer than 5 maps (2 maps)", () => {
    const maps = [makeMap("map-a", "Alpha"), makeMap("map-b", "Beta")];
    mockReviewsPerMap(maps);

    render(<ReviewTimeline />);

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

    render(<ReviewTimeline />);

    expect(screen.getByText(/no reviews scheduled/i)).toBeTruthy();
  });
});
