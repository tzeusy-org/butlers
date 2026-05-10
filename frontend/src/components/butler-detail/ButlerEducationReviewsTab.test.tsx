// @vitest-environment jsdom
/**
 * ButlerEducationReviewsTab — RTL tests pinning the three sections.
 *
 * Tests:
 *  - Renders three sections (mastery KPI strip, review timeline, frontier)
 *  - Timeline sections grouped by Overdue / Today / This Week / Later
 *  - Color-coded left borders on each timeline group section
 *  - Empty states are shown explicitly when data is empty (no infinite spinner)
 *  - KPI values render with data
 *  - Review items link to /education
 *  - Frontier items appear when data is present
 *
 * bead: bu-3cujw.1, bu-1zefq
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
}));

import {
  useMindMaps,
  useAllPendingReviews,
  useAllMasterySummaries,
  useAllFrontierNodes,
} from "@/hooks/use-education";

// ---------------------------------------------------------------------------
// Helpers
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
// Default mock setup: one active map with data
// ---------------------------------------------------------------------------

function setupWithData() {
  vi.mocked(useMindMaps).mockReturnValue({
    data: { data: ACTIVE_MAPS, meta: { total: 2, offset: 0, limit: 20 } },
    isLoading: false,
  } as ReturnType<typeof useMindMaps>);

  // useAllPendingReviews returns an array of results, one per map ID.
  vi.mocked(useAllPendingReviews).mockImplementation((mapIds) =>
    mapIds.map((id) =>
      id === "map-1"
        ? ({ data: PENDING_REVIEWS, isLoading: false } as ReturnType<typeof useAllPendingReviews>[number])
        : ({ data: [], isLoading: false } as ReturnType<typeof useAllPendingReviews>[number]),
    ),
  );

  vi.mocked(useAllMasterySummaries).mockImplementation((mapIds) =>
    mapIds.map((id) => {
      if (id === "map-1") {
        return { data: MASTERY_SUMMARY_1, isLoading: false } as ReturnType<typeof useAllMasterySummaries>[number];
      }
      if (id === "map-2") {
        return { data: MASTERY_SUMMARY_2, isLoading: false } as ReturnType<typeof useAllMasterySummaries>[number];
      }
      return { data: null, isLoading: false } as ReturnType<typeof useAllMasterySummaries>[number];
    }),
  );

  vi.mocked(useAllFrontierNodes).mockImplementation((mapIds) =>
    mapIds.map((id) =>
      id === "map-1"
        ? ({ data: FRONTIER_NODES, isLoading: false } as ReturnType<typeof useAllFrontierNodes>[number])
        : ({ data: [], isLoading: false } as ReturnType<typeof useAllFrontierNodes>[number]),
    ),
  );
}

function setupEmpty() {
  vi.mocked(useMindMaps).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 20 } },
    isLoading: false,
  } as ReturnType<typeof useMindMaps>);

  // No maps → hooks receive empty arrays → return empty arrays.
  vi.mocked(useAllPendingReviews).mockReturnValue([]);
  vi.mocked(useAllMasterySummaries).mockReturnValue([]);
  vi.mocked(useAllFrontierNodes).mockReturnValue([]);
}

function setupLoading() {
  vi.mocked(useMindMaps).mockReturnValue({
    data: undefined,
    isLoading: true,
  } as ReturnType<typeof useMindMaps>);

  // When maps are still loading, the aggregate hooks receive an empty mapIds
  // array and return []. isLoading from useMindMaps drives the overall state.
  vi.mocked(useAllPendingReviews).mockReturnValue([]);
  vi.mocked(useAllMasterySummaries).mockReturnValue([]);
  vi.mocked(useAllFrontierNodes).mockReturnValue([]);
}

// ---------------------------------------------------------------------------
// Tests: three sections are rendered
// ---------------------------------------------------------------------------

describe("ButlerEducationReviewsTab — three sections present", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders the mastery KPI strip section", () => {
    renderTab();
    expect(screen.getByTestId("mastery-kpi-strip")).toBeDefined();
  });

  it("renders the review timeline section", () => {
    renderTab();
    expect(screen.getByTestId("reviews-timeline-section")).toBeDefined();
  });

  it("renders the frontier section", () => {
    renderTab();
    expect(screen.getByTestId("reviews-frontier-section")).toBeDefined();
  });

  it("renders all three KPI labels", () => {
    renderTab();
    expect(screen.getByText("Total cards")).toBeDefined();
    expect(screen.getByText("Mastered")).toBeDefined();
    // "Overdue" appears as both a KPI label and a timeline section title when there are overdue items
    const overdueElements = screen.getAllByText("Overdue");
    expect(overdueElements.length).toBeGreaterThanOrEqual(1);
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
    // KPI strip values rendered with data-testid="kpi-value"
    const kpiValues = screen.getAllByTestId("kpi-value");
    // First KPI is Total cards = 40
    expect(kpiValues[0].textContent).toBe("40");
  });

  it("aggregates mastered_count across maps (10 + 5 = 15)", () => {
    renderTab();
    const kpiValues = screen.getAllByTestId("kpi-value");
    expect(kpiValues[1].textContent).toBe("15");
  });
});

// ---------------------------------------------------------------------------
// Tests: Review timeline grouping
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
    // Both overdue items should be visible
    expect(screen.getByText("List comprehensions")).toBeDefined();
    expect(screen.getByText("Decorators")).toBeDefined();
  });

  it("renders the Today section for items due later today with amber border", () => {
    renderTab();
    const todaySection = screen.getByTestId("reviews-today-section");
    expect(todaySection).toBeDefined();
    expect(screen.getByText("Type hints")).toBeDefined();
    // Amber left border indicates Today bucket
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
    // CardContent inside has the border class
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
// Tests: empty states are explicit (no infinite spinner)
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
    // Empty state lines appear (at least one for reviews)
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
    // With no maps, total nodes and mastered show "—"
    const kpiValues = screen.getAllByTestId("kpi-value");
    expect(kpiValues[0].textContent).toBe("—");
    expect(kpiValues[1].textContent).toBe("—");
  });
});

// ---------------------------------------------------------------------------
// Tests: loading state shows placeholder, not empty-state text
// ---------------------------------------------------------------------------

describe("ButlerEducationReviewsTab — loading state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupLoading();
  });

  afterEach(() => cleanup());

  it("shows loading placeholders instead of empty-state lines while queries are pending", () => {
    renderTab();
    // Loading lines appear — no empty-state text while loading
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
// Tests: all active mind maps contribute (no fixed 5-map cap)
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
    } as ReturnType<typeof useMindMaps>);

    vi.mocked(useAllPendingReviews).mockImplementation((mapIds) =>
      mapIds.map((id) =>
        id === "map-6"
          ? ({ data: [MAP6_REVIEW], isLoading: false } as ReturnType<typeof useAllPendingReviews>[number])
          : ({ data: [], isLoading: false } as ReturnType<typeof useAllPendingReviews>[number]),
      ),
    );

    vi.mocked(useAllMasterySummaries).mockImplementation((mapIds) =>
      mapIds.map((id) =>
        id === "map-6"
          ? ({ data: MAP6_MASTERY, isLoading: false } as ReturnType<typeof useAllMasterySummaries>[number])
          : ({ data: null, isLoading: false } as ReturnType<typeof useAllMasterySummaries>[number]),
      ),
    );

    vi.mocked(useAllFrontierNodes).mockImplementation((mapIds) =>
      mapIds.map(() => ({ data: [], isLoading: false } as ReturnType<typeof useAllFrontierNodes>[number])),
    );
  });

  afterEach(() => cleanup());

  it("passes all 6 map IDs to the aggregate hooks (not capped at 5)", () => {
    renderTab();
    // If the old 5-cap were still present, map-6's review would not appear.
    expect(screen.getByText("Hiragana basics")).toBeDefined();
  });

  it("aggregates KPI totals from all 6 maps including map-6 (0+0+0+0+0+8 = 8 total nodes)", () => {
    renderTab();
    const kpiValues = screen.getAllByTestId("kpi-value");
    // Only map-6 has a mastery summary in this setup → total_nodes = 8
    expect(kpiValues[0].textContent).toBe("8");
    expect(kpiValues[1].textContent).toBe("2");
  });
});

// ---------------------------------------------------------------------------
// Tests: getAllTabs includes education reviews tab
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
