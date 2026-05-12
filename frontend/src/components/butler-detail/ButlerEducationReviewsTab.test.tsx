// @vitest-environment jsdom
/**
 * ButlerEducationReviewsTab — RTL tests for the redesigned 4-col panel grid.
 *
 * Layout (3 rows):
 *  Row 1: 4 KPI cells — total cards, mastered count, overdue count, avg mastery score
 *  Row 2: mind maps progress + pending reviews timeline
 *  Row 3: frontier nodes + 7d retention trend
 *
 * Tests:
 *  - KPI quartet renders all four cells
 *  - KPI aggregation (total_nodes, mastered_count, overdue, avg mastery)
 *  - Mind maps progress panel renders and handles empty state
 *  - Review timeline grouping (Overdue / Today / This Week / Later)
 *  - Review timeline color-coded left borders
 *  - Empty states are explicit (no infinite spinner)
 *  - Frontier list renders items
 *  - Retention chart renders with data / empty state
 *  - No fixed 5-map cap
 *
 * bead: bu-iuol4.26
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerEducationReviewsTab from "./ButlerEducationReviewsTab";

// ---------------------------------------------------------------------------
// Mock education hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-education", () => ({
  useMindMaps: vi.fn(),
  useAllPendingReviews: vi.fn(),
  useAllMasterySummaries: vi.fn(),
  useAllFrontierNodes: vi.fn(),
  useMindMapAnalyticsTrend: vi.fn(),
}));

import {
  useMindMaps,
  useAllPendingReviews,
  useAllMasterySummaries,
  useAllFrontierNodes,
  useMindMapAnalyticsTrend,
} from "@/hooks/use-education";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const ACTIVE_MAPS = [
  { id: "map-1", title: "Python", status: "active", root_node_id: null, created_at: "", updated_at: "", nodes: [], edges: [] },
  { id: "map-2", title: "Calculus", status: "active", root_node_id: null, created_at: "", updated_at: "", nodes: [], edges: [] },
];

const PENDING_REVIEWS = [
  {
    node_id: "node-abc",
    label: "List comprehensions",
    ease_factor: 2.5,
    repetitions: 3,
    next_review_at: new Date(Date.now() - 3600_000).toISOString(), // 1h overdue
    mastery_status: "reviewing",
  },
  {
    node_id: "node-def",
    label: "Decorators",
    ease_factor: 2.2,
    repetitions: 1,
    next_review_at: new Date(Date.now() - 7200_000).toISOString(), // 2h overdue
    mastery_status: "learning",
  },
  {
    node_id: "node-mno",
    label: "Type hints",
    ease_factor: 2.3,
    repetitions: 2,
    // Due later today: end-of-today minus 30 minutes, ensuring it falls in Today bucket.
    next_review_at: (() => {
      const d = new Date();
      d.setHours(23, 29, 59, 999);
      return d.toISOString();
    })(),
    mastery_status: "learning",
  },
  {
    node_id: "node-ghi",
    label: "Lambda functions",
    ease_factor: 2.0,
    repetitions: 0,
    next_review_at: new Date(Date.now() + 3 * 24 * 60 * 60_000).toISOString(), // 3 days away — This Week
    mastery_status: "learning",
  },
  {
    node_id: "node-jkl",
    label: "Async await",
    ease_factor: 2.0,
    repetitions: 0,
    next_review_at: new Date(Date.now() + 14 * 24 * 60 * 60_000).toISOString(), // 14 days away — Later
    mastery_status: "unseen",
  },
];

const MASTERY_SUMMARY_1 = {
  mind_map_id: "map-1",
  total_nodes: 25,
  mastered_count: 10,
  learning_count: 8,
  reviewing_count: 4,
  unseen_count: 3,
  diagnosed_count: 0,
  avg_mastery_score: 0.65,
  struggling_node_ids: [],
};

const MASTERY_SUMMARY_2 = {
  mind_map_id: "map-2",
  total_nodes: 15,
  mastered_count: 5,
  learning_count: 4,
  reviewing_count: 3,
  unseen_count: 3,
  diagnosed_count: 0,
  avg_mastery_score: 0.5,
  struggling_node_ids: [],
};

const FRONTIER_NODES = [
  {
    id: "node-f1",
    mind_map_id: "map-1",
    label: "Generators",
    description: null,
    depth: 2,
    mastery_score: 0.1,
    mastery_status: "unseen",
    ease_factor: 2.5,
    repetitions: 0,
    next_review_at: null,
    last_reviewed_at: null,
    effort_minutes: null,
    metadata: {},
    created_at: "",
    updated_at: "",
  },
];

const TREND_DATA = {
  mind_map_id: "map-1",
  days: 7,
  trend: [
    { id: "t1", mind_map_id: "map-1", snapshot_date: "2026-05-05", metrics: { mastery_pct: 0.40 }, created_at: "2026-05-05T00:00:00Z" },
    { id: "t2", mind_map_id: "map-1", snapshot_date: "2026-05-06", metrics: { mastery_pct: 0.45 }, created_at: "2026-05-06T00:00:00Z" },
    { id: "t3", mind_map_id: "map-1", snapshot_date: "2026-05-07", metrics: { mastery_pct: 0.50 }, created_at: "2026-05-07T00:00:00Z" },
  ],
};

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderTab() {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <MemoryRouter>
        <ButlerEducationReviewsTab />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Default mock setups
// ---------------------------------------------------------------------------

function setupWithData() {
  vi.mocked(useMindMaps).mockReturnValue({
    data: { data: ACTIVE_MAPS, meta: { total: 2, offset: 0, limit: 20 } },
    isLoading: false,
  } as unknown as ReturnType<typeof useMindMaps>);

  vi.mocked(useAllPendingReviews).mockImplementation((mapIds) =>
    mapIds.map((id) =>
      id === "map-1"
        ? ({ data: PENDING_REVIEWS, isLoading: false } as ReturnType<typeof useAllPendingReviews>[number])
        : ({ data: [], isLoading: false } as unknown as ReturnType<typeof useAllPendingReviews>[number]),
    ),
  );

  vi.mocked(useAllMasterySummaries).mockImplementation((mapIds) =>
    mapIds.map((id) => {
      if (id === "map-1") {
        return { data: MASTERY_SUMMARY_1, isLoading: false } as unknown as ReturnType<typeof useAllMasterySummaries>[number];
      }
      if (id === "map-2") {
        return { data: MASTERY_SUMMARY_2, isLoading: false } as unknown as ReturnType<typeof useAllMasterySummaries>[number];
      }
      return { data: null, isLoading: false } as unknown as ReturnType<typeof useAllMasterySummaries>[number];
    }),
  );

  vi.mocked(useAllFrontierNodes).mockImplementation((mapIds) =>
    mapIds.map((id) =>
      id === "map-1"
        ? ({ data: FRONTIER_NODES, isLoading: false } as ReturnType<typeof useAllFrontierNodes>[number])
        : ({ data: [], isLoading: false } as unknown as ReturnType<typeof useAllFrontierNodes>[number]),
    ),
  );

  vi.mocked(useMindMapAnalyticsTrend).mockReturnValue({
    data: TREND_DATA,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useMindMapAnalyticsTrend>);
}

function setupEmpty() {
  vi.mocked(useMindMaps).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 20 } },
    isLoading: false,
  } as unknown as ReturnType<typeof useMindMaps>);

  vi.mocked(useAllPendingReviews).mockReturnValue([]);
  vi.mocked(useAllMasterySummaries).mockReturnValue([]);
  vi.mocked(useAllFrontierNodes).mockReturnValue([]);

  vi.mocked(useMindMapAnalyticsTrend).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useMindMapAnalyticsTrend>);
}

function setupLoading() {
  vi.mocked(useMindMaps).mockReturnValue({
    data: undefined,
    isLoading: true,
  } as ReturnType<typeof useMindMaps>);

  vi.mocked(useAllPendingReviews).mockReturnValue([]);
  vi.mocked(useAllMasterySummaries).mockReturnValue([]);
  vi.mocked(useAllFrontierNodes).mockReturnValue([]);

  vi.mocked(useMindMapAnalyticsTrend).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useMindMapAnalyticsTrend>);
}

// ---------------------------------------------------------------------------
// Tests: all panel sections are rendered
// ---------------------------------------------------------------------------

describe("ButlerEducationReviewsTab — panel sections present", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders the KPI strip section", () => {
    renderTab();
    expect(screen.getByTestId("mastery-kpi-strip")).toBeDefined();
  });

  it("renders 4 KPI cells", () => {
    renderTab();
    const kpiValues = screen.getAllByTestId("kpi-value");
    expect(kpiValues.length).toBe(4);
  });

  it("renders the review timeline section", () => {
    renderTab();
    expect(screen.getByTestId("reviews-timeline-section")).toBeDefined();
  });

  it("renders the mind maps progress panel", () => {
    renderTab();
    expect(screen.getByTestId("mind-maps-progress-panel")).toBeDefined();
  });

  it("renders the frontier section", () => {
    renderTab();
    expect(screen.getByTestId("reviews-frontier-section")).toBeDefined();
  });

  it("renders the retention trend panel", () => {
    renderTab();
    expect(screen.getByTestId("retention-trend-panel")).toBeDefined();
  });

  it("renders all KPI labels (total cards, mastered, overdue, avg mastery)", () => {
    renderTab();
    expect(screen.getByText("Total cards")).toBeDefined();
    expect(screen.getByText("Mastered")).toBeDefined();
    // "Overdue" appears as both a KPI label and a timeline section title
    const overdueElements = screen.getAllByText("Overdue");
    expect(overdueElements.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Avg mastery")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: KPI aggregation
// ---------------------------------------------------------------------------

describe("ButlerEducationReviewsTab — KPI values aggregate across maps", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("aggregates total_nodes across maps (25 + 15 = 40)", () => {
    renderTab();
    const kpiValues = screen.getAllByTestId("kpi-value");
    // KPI order: total cards, mastered, overdue, avg mastery
    expect(kpiValues[0].textContent).toBe("40");
  });

  it("aggregates mastered_count across maps (10 + 5 = 15)", () => {
    renderTab();
    const kpiValues = screen.getAllByTestId("kpi-value");
    expect(kpiValues[1].textContent).toBe("15");
  });

  it("shows overdue count for overdue items (2 overdue in fixture)", () => {
    renderTab();
    const kpiValues = screen.getAllByTestId("kpi-value");
    // 2 items are overdue in PENDING_REVIEWS (node-abc and node-def)
    expect(kpiValues[2].textContent).toBe("2");
  });

  it("shows avg mastery score as percentage", () => {
    renderTab();
    const kpiValues = screen.getAllByTestId("kpi-value");
    // weighted avg of 0.65*25 and 0.5*15 over 40 total = (16.25+7.5)/40 = 23.75/40 = 59%
    expect(kpiValues[3].textContent).toBe("59%");
  });
});

// ---------------------------------------------------------------------------
// Tests: mind maps progress panel
// ---------------------------------------------------------------------------

describe("ButlerEducationReviewsTab — mind maps progress panel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders a progress row for each active mind map", () => {
    renderTab();
    const rows = screen.getAllByTestId("mind-map-progress-row");
    expect(rows.length).toBe(2);
  });

  it("renders Python and Calculus map titles", () => {
    renderTab();
    // Both maps appear (may appear multiple times as map title + review badge context)
    expect(screen.getAllByText("Python").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Calculus").length).toBeGreaterThanOrEqual(1);
  });

  it("renders progress bars for maps with mastery data", () => {
    renderTab();
    const bars = screen.getAllByTestId("mastery-progress-bar");
    expect(bars.length).toBe(2);
  });

  it("shows empty state when no active maps exist", () => {
    vi.resetAllMocks();
    setupEmpty();
    renderTab();
    // Empty state message in the mind maps progress panel
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByTestId("mind-maps-list")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: review timeline grouping
// ---------------------------------------------------------------------------

describe("ButlerEducationReviewsTab — review timeline grouping", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders the Overdue section for past-due items", () => {
    renderTab();
    expect(screen.getByTestId("reviews-overdue-section")).toBeDefined();
    expect(screen.getByText("List comprehensions")).toBeDefined();
    expect(screen.getByText("Decorators")).toBeDefined();
  });

  it("renders the Today section for items due later today with amber border", () => {
    renderTab();
    const todaySection = screen.getByTestId("reviews-today-section");
    expect(todaySection).toBeDefined();
    expect(screen.getByText("Type hints")).toBeDefined();
    const content = todaySection.querySelector("[class*='border-l-amber']");
    expect(content).not.toBeNull();
  });

  it("renders the This Week section for items due within 7 days", () => {
    renderTab();
    expect(screen.getByTestId("reviews-this-week-section")).toBeDefined();
    expect(screen.getByText("Lambda functions")).toBeDefined();
  });

  it("renders the Later section for items due beyond 7 days", () => {
    renderTab();
    expect(screen.getByTestId("reviews-later-section")).toBeDefined();
    expect(screen.getByText("Async await")).toBeDefined();
  });

  it("overdue section has red left border class", () => {
    renderTab();
    const overdueSection = screen.getByTestId("reviews-overdue-section");
    const content = overdueSection.querySelector("[class*='border-l-red']");
    expect(content).not.toBeNull();
  });

  it("this-week section has blue left border class", () => {
    renderTab();
    const weekSection = screen.getByTestId("reviews-this-week-section");
    const content = weekSection.querySelector("[class*='border-l-blue']");
    expect(content).not.toBeNull();
  });

  it("review items link to /education", () => {
    renderTab();
    const items = screen.getAllByTestId("review-item") as HTMLAnchorElement[];
    for (const item of items) {
      expect(item.getAttribute("href")).toBe("/education");
    }
  });
});

// ---------------------------------------------------------------------------
// Tests: Frontier list
// ---------------------------------------------------------------------------

describe("ButlerEducationReviewsTab — frontier list", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders frontier items when data is present", () => {
    renderTab();
    const items = screen.getAllByTestId("frontier-item");
    expect(items.length).toBeGreaterThanOrEqual(1);
  });

  it("shows Generators in frontier list", () => {
    renderTab();
    expect(screen.getByText("Generators")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Retention 7d trend chart
// ---------------------------------------------------------------------------

describe("ButlerEducationReviewsTab — retention trend chart", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders the retention chart when trend data is present", () => {
    renderTab();
    expect(screen.getByTestId("retention-chart")).toBeDefined();
  });

  it("renders the retention sparkline", () => {
    renderTab();
    expect(screen.getByTestId("retention-sparkline")).toBeDefined();
  });

  it("shows latest retention value (50% from last trend snapshot)", () => {
    renderTab();
    const latestValue = screen.getByTestId("retention-latest-value");
    expect(latestValue.textContent).toBe("50%");
  });

  it("shows empty state when no trend data", () => {
    vi.mocked(useMindMapAnalyticsTrend).mockReturnValue({
      data: { mind_map_id: "map-1", days: 7, trend: [] },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMindMapAnalyticsTrend>);

    renderTab();
    expect(screen.queryByTestId("retention-chart")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });

  it("shows error state when trend fetch fails", () => {
    vi.mocked(useMindMapAnalyticsTrend).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as unknown as ReturnType<typeof useMindMapAnalyticsTrend>);

    renderTab();
    expect(screen.queryByTestId("retention-chart")).toBeNull();
    const errorLines = screen.getAllByTestId("error-state-line");
    expect(errorLines.length).toBeGreaterThanOrEqual(1);
  });

  it("calls useMindMapAnalyticsTrend with the first active map id and 7 days", () => {
    renderTab();
    expect(vi.mocked(useMindMapAnalyticsTrend)).toHaveBeenCalledWith("map-1", 7);
  });

  // ---------------------------------------------------------------------------
  // extractMasteryPct strict-accessor tests [bu-8mtqt]
  //
  // The function reads ONLY the canonical key `mastery_pct`.  Former aliases
  // (mastered_pct, mastery_percent) were never emitted by the backend and the
  // fallback loop has been removed.  Entries missing `mastery_pct` are excluded
  // from chartData (fail-fast behaviour).
  // ---------------------------------------------------------------------------

  it("shows empty state when trend entry uses legacy mastered_pct key (not canonical)", () => {
    vi.mocked(useMindMapAnalyticsTrend).mockReturnValue({
      data: {
        mind_map_id: "map-1",
        days: 7,
        trend: [
          { id: "t1", mind_map_id: "map-1", snapshot_date: "2026-05-10", metrics: { mastered_pct: 0.72 }, created_at: "2026-05-10T00:00:00Z" },
        ],
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMindMapAnalyticsTrend>);

    renderTab();
    // mastered_pct is NOT the canonical key → entry excluded → empty state
    expect(screen.queryByTestId("retention-chart")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });

  it("shows empty state when trend entry uses legacy mastery_percent key (not canonical)", () => {
    vi.mocked(useMindMapAnalyticsTrend).mockReturnValue({
      data: {
        mind_map_id: "map-1",
        days: 7,
        trend: [
          { id: "t1", mind_map_id: "map-1", snapshot_date: "2026-05-10", metrics: { mastery_percent: 0.88 }, created_at: "2026-05-10T00:00:00Z" },
        ],
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMindMapAnalyticsTrend>);

    renderTab();
    // mastery_percent is NOT the canonical key → entry excluded → empty state
    expect(screen.queryByTestId("retention-chart")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });

  it("shows empty state when metrics contain no known mastery key", () => {
    vi.mocked(useMindMapAnalyticsTrend).mockReturnValue({
      data: {
        mind_map_id: "map-1",
        days: 7,
        trend: [
          { id: "t1", mind_map_id: "map-1", snapshot_date: "2026-05-10", metrics: { unknown_key: 0.5 }, created_at: "2026-05-10T00:00:00Z" },
        ],
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMindMapAnalyticsTrend>);

    renderTab();
    // All entries filtered → chartData empty → empty state shown
    expect(screen.queryByTestId("retention-chart")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });

  it("clamps mastery_pct already expressed as percentage (>1 value → no multiply by 100)", () => {
    vi.mocked(useMindMapAnalyticsTrend).mockReturnValue({
      data: {
        mind_map_id: "map-1",
        days: 7,
        trend: [
          { id: "t1", mind_map_id: "map-1", snapshot_date: "2026-05-10", metrics: { mastery_pct: 65 }, created_at: "2026-05-10T00:00:00Z" },
        ],
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMindMapAnalyticsTrend>);

    renderTab();
    const latestValue = screen.getByTestId("retention-latest-value");
    expect(latestValue.textContent).toBe("65%");
  });
});

// ---------------------------------------------------------------------------
// Tests: explicit empty states (no infinite spinner)
// ---------------------------------------------------------------------------

describe("ButlerEducationReviewsTab — explicit empty states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupEmpty();
  });

  afterEach(() => cleanup());

  it("shows empty state for reviews when no reviews are pending", () => {
    renderTab();
    // No timeline group sections — empty state line shows instead
    expect(screen.queryByTestId("reviews-overdue-section")).toBeNull();
    expect(screen.queryByTestId("reviews-today-section")).toBeNull();
    expect(screen.queryByTestId("reviews-this-week-section")).toBeNull();
    expect(screen.queryByTestId("reviews-later-section")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });

  it("shows empty state for frontier when no frontier nodes exist", () => {
    renderTab();
    expect(screen.queryByTestId("frontier-list")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });

  it("renders KPI strip even when no maps exist (shows dashes)", () => {
    renderTab();
    expect(screen.getByTestId("mastery-kpi-strip")).toBeDefined();
    const kpiValues = screen.getAllByTestId("kpi-value");
    // Total cards and mastered show "—" with no maps
    expect(kpiValues[0].textContent).toBe("—");
    expect(kpiValues[1].textContent).toBe("—");
  });

  it("renders mind maps empty state when no maps exist", () => {
    renderTab();
    expect(screen.queryByTestId("mind-maps-list")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: loading state
// ---------------------------------------------------------------------------

describe("ButlerEducationReviewsTab — loading state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupLoading();
  });

  afterEach(() => cleanup());

  it("shows loading placeholders instead of empty-state lines while queries are pending", () => {
    renderTab();
    const loadingLines = screen.getAllByTestId("loading-line");
    expect(loadingLines.length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByTestId("empty-state-line")).toBeNull();
  });

  it("does not render timeline group sections while loading", () => {
    renderTab();
    expect(screen.queryByTestId("reviews-overdue-section")).toBeNull();
    expect(screen.queryByTestId("reviews-today-section")).toBeNull();
    expect(screen.queryByTestId("reviews-this-week-section")).toBeNull();
    expect(screen.queryByTestId("reviews-later-section")).toBeNull();
  });

  it("does not render frontier-list while loading", () => {
    renderTab();
    expect(screen.queryByTestId("frontier-list")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: no fixed 5-map cap
// ---------------------------------------------------------------------------

describe("ButlerEducationReviewsTab — no fixed 5-map cap", () => {
  const SIX_MAPS = [
    { id: "map-1", title: "Python", status: "active", root_node_id: null, created_at: "", updated_at: "", nodes: [], edges: [] },
    { id: "map-2", title: "Calculus", status: "active", root_node_id: null, created_at: "", updated_at: "", nodes: [], edges: [] },
    { id: "map-3", title: "Chemistry", status: "active", root_node_id: null, created_at: "", updated_at: "", nodes: [], edges: [] },
    { id: "map-4", title: "History", status: "active", root_node_id: null, created_at: "", updated_at: "", nodes: [], edges: [] },
    { id: "map-5", title: "Music", status: "active", root_node_id: null, created_at: "", updated_at: "", nodes: [], edges: [] },
    { id: "map-6", title: "Japanese", status: "active", root_node_id: null, created_at: "", updated_at: "", nodes: [], edges: [] },
  ];

  const MAP6_REVIEW = {
    node_id: "node-m6",
    label: "Hiragana basics",
    ease_factor: 2.5,
    repetitions: 1,
    next_review_at: new Date(Date.now() - 1800_000).toISOString(), // overdue
    mastery_status: "learning",
  };

  const MAP6_MASTERY = {
    mind_map_id: "map-6",
    total_nodes: 8,
    mastered_count: 2,
    learning_count: 3,
    reviewing_count: 2,
    unseen_count: 1,
    diagnosed_count: 0,
    avg_mastery_score: 0.4,
    struggling_node_ids: [],
  };

  beforeEach(() => {
    vi.resetAllMocks();

    vi.mocked(useMindMaps).mockReturnValue({
      data: { data: SIX_MAPS, meta: { total: 6, offset: 0, limit: 20 } },
      isLoading: false,
    } as unknown as ReturnType<typeof useMindMaps>);

    vi.mocked(useAllPendingReviews).mockImplementation((mapIds) =>
      mapIds.map((id) =>
        id === "map-6"
          ? ({ data: [MAP6_REVIEW], isLoading: false } as ReturnType<typeof useAllPendingReviews>[number])
          : ({ data: [], isLoading: false } as unknown as ReturnType<typeof useAllPendingReviews>[number]),
      ),
    );

    vi.mocked(useAllMasterySummaries).mockImplementation((mapIds) =>
      mapIds.map((id) =>
        id === "map-6"
          ? ({ data: MAP6_MASTERY, isLoading: false } as unknown as ReturnType<typeof useAllMasterySummaries>[number])
          : ({ data: null, isLoading: false } as unknown as ReturnType<typeof useAllMasterySummaries>[number]),
      ),
    );

    vi.mocked(useAllFrontierNodes).mockImplementation((mapIds) =>
      mapIds.map(() => ({ data: [], isLoading: false } as unknown as ReturnType<typeof useAllFrontierNodes>[number])),
    );

    vi.mocked(useMindMapAnalyticsTrend).mockReturnValue({
      data: { mind_map_id: "map-1", days: 7, trend: [] },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMindMapAnalyticsTrend>);
  });

  afterEach(() => cleanup());

  it("passes all 6 map IDs to the aggregate hooks (not capped at 5)", () => {
    renderTab();
    expect(screen.getByText("Hiragana basics")).toBeDefined();
  });

  it("aggregates KPI totals from all 6 maps including map-6 (only map-6 has mastery → 8 nodes)", () => {
    renderTab();
    const kpiValues = screen.getAllByTestId("kpi-value");
    expect(kpiValues[0].textContent).toBe("8");
    expect(kpiValues[1].textContent).toBe("2");
  });

  it("renders progress rows for all 6 mind maps", () => {
    renderTab();
    const rows = screen.getAllByTestId("mind-map-progress-row");
    expect(rows.length).toBe(6);
  });
});

// ---------------------------------------------------------------------------
// Tests: education reviews tab in getAllTabs
// ---------------------------------------------------------------------------

import { getAllTabs, isValidTab } from "@/pages/butler-detail-tabs";

describe("ButlerDetailPage — education reviews tab in getAllTabs", () => {
  it("education butler has 'reviews' tab in operator mode", () => {
    expect(getAllTabs("education", "operator")).toContain("reviews");
  });

  it("education butler has 'reviews' tab in resident mode", () => {
    expect(getAllTabs("education", "resident")).toContain("reviews");
  });

  it("'reviews' is a valid tab for education butler in both modes", () => {
    expect(isValidTab("reviews", "education", "operator")).toBe(true);
    expect(isValidTab("reviews", "education", "resident")).toBe(true);
  });

  it("'reviews' is NOT a valid tab for non-education butlers", () => {
    expect(isValidTab("reviews", "general", "operator")).toBe(false);
    expect(isValidTab("reviews", "health", "resident")).toBe(false);
  });

  it("non-education butlers do not include 'reviews' tab", () => {
    expect(getAllTabs("general", "operator")).not.toContain("reviews");
    expect(getAllTabs("health", "resident")).not.toContain("reviews");
  });
});
