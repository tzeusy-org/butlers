// @vitest-environment jsdom
/**
 * ButlerEducationReviewsTab — RTL tests pinning the three sections.
 *
 * Tests:
 *  - Renders three sections (mastery KPI strip, due-now, frontier)
 *  - Empty states are shown explicitly when data is empty (no infinite spinner)
 *  - KPI values render with data
 *  - Due-now items link to /education
 *  - Frontier items appear when data is present
 *
 * bead: bu-3cujw.1
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
  usePendingReviews: vi.fn(),
  useMasterySummary: vi.fn(),
  useFrontierNodes: vi.fn(),
}));

import {
  useMindMaps,
  usePendingReviews,
  useMasterySummary,
  useFrontierNodes,
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

  vi.mocked(usePendingReviews).mockImplementation((mindMapId) => {
    if (mindMapId === "map-1") {
      return { data: PENDING_REVIEWS, isLoading: false } as ReturnType<typeof usePendingReviews>;
    }
    return { data: [], isLoading: false } as ReturnType<typeof usePendingReviews>;
  });

  vi.mocked(useMasterySummary).mockImplementation((mindMapId) => {
    if (mindMapId === "map-1") {
      return { data: MASTERY_SUMMARY_1, isLoading: false } as ReturnType<typeof useMasterySummary>;
    }
    if (mindMapId === "map-2") {
      return { data: MASTERY_SUMMARY_2, isLoading: false } as ReturnType<typeof useMasterySummary>;
    }
    return { data: null, isLoading: false } as ReturnType<typeof useMasterySummary>;
  });

  vi.mocked(useFrontierNodes).mockImplementation((mindMapId) => {
    if (mindMapId === "map-1") {
      return { data: FRONTIER_NODES, isLoading: false } as ReturnType<typeof useFrontierNodes>;
    }
    return { data: [], isLoading: false } as ReturnType<typeof useFrontierNodes>;
  });
}

function setupEmpty() {
  vi.mocked(useMindMaps).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 20 } },
    isLoading: false,
  } as ReturnType<typeof useMindMaps>);

  vi.mocked(usePendingReviews).mockReturnValue(
    { data: [], isLoading: false } as ReturnType<typeof usePendingReviews>,
  );
  vi.mocked(useMasterySummary).mockReturnValue(
    { data: null, isLoading: false } as ReturnType<typeof useMasterySummary>,
  );
  vi.mocked(useFrontierNodes).mockReturnValue(
    { data: [], isLoading: false } as ReturnType<typeof useFrontierNodes>,
  );
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

  it("renders the due-now section", () => {
    renderTab();
    expect(screen.getByTestId("reviews-due-now-section")).toBeDefined();
  });

  it("renders the frontier section", () => {
    renderTab();
    expect(screen.getByTestId("reviews-frontier-section")).toBeDefined();
  });

  it("renders all four KPI labels", () => {
    renderTab();
    expect(screen.getByText("Total cards")).toBeDefined();
    expect(screen.getByText("Mastered")).toBeDefined();
    expect(screen.getByText("Due today")).toBeDefined();
    expect(screen.getByText("Due this week")).toBeDefined();
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
// Tests: Due-now list
// ---------------------------------------------------------------------------

describe("ButlerEducationReviewsTab — due-now list", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });

  afterEach(() => cleanup());

  it("renders due-now items", () => {
    renderTab();
    const items = screen.getAllByTestId("due-now-item");
    expect(items.length).toBeGreaterThanOrEqual(1);
  });

  it("shows list comprehension item in due-now list", () => {
    renderTab();
    expect(screen.getByText("List comprehensions")).toBeDefined();
  });

  it("due-now items link to /education", () => {
    renderTab();
    const items = screen.getAllByTestId("due-now-item") as HTMLAnchorElement[];
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

  it("shows empty state for due-now when no reviews are pending", () => {
    renderTab();
    // No due-now-list — empty state line shows instead
    expect(screen.queryByTestId("due-now-list")).toBeNull();
    // Empty state lines appear (at least one for due-now)
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
// Tests: getAllTabs includes education reviews tab
// ---------------------------------------------------------------------------

import { getAllTabs, isValidTab } from "@/pages/ButlerDetailPage";

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
