// @vitest-environment jsdom
/**
 * ButlerGeneralCollectionsTab — RTL tests.
 *
 * Tests cover:
 *  - Root container renders
 *  - All panels present (KPI strip, collections directory, recent items, histogram, quick actions)
 *  - KPI rendering: total collections, total items, recently modified, largest collection
 *  - Collections table rows render
 *  - Recent items sidebar renders
 *  - Histogram chart renders with various bracket counts
 *  - Quick actions: search input present, "create collection" button opens dialog
 *  - isError handling: error banner, ErrorLine in each panel
 *  - Loading state: skeletons, no empty-state text
 *  - Empty state: empty-state text when no data
 *
 * bead: bu-iuol4.30
 */

import {
  afterEach,
  beforeAll,
  afterAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";

import ButlerGeneralCollectionsTab from "./ButlerGeneralCollectionsTab";

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-general", () => ({
  useGeneralStats: vi.fn(),
  useGeneralCollections: vi.fn(),
  useGeneralEntities: vi.fn(),
}));

// Stub <Time> to avoid date-formatting complexity
vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => createElement("time", { dateTime: value }, value),
}));

// Mock recharts — avoids SVG/canvas complexity in jsdom
vi.mock("recharts", () => {
  const BarChart = ({ children }: { children: React.ReactNode }) =>
    createElement("div", { "data-testid": "recharts-bar-chart" }, children);

  const Bar = ({ dataKey }: { dataKey: string }) =>
    createElement("div", { "data-testid": `recharts-bar-${dataKey}` });

  const Cell = () => null;

  const XAxis = () => null;
  const YAxis = () => null;
  const Tooltip = () => null;

  const ResponsiveContainer = ({ children }: { children: React.ReactNode }) =>
    createElement(
      "div",
      { "data-testid": "recharts-responsive-container" },
      children,
    );

  return { BarChart, Bar, Cell, XAxis, YAxis, Tooltip, ResponsiveContainer };
});

import {
  useGeneralCollections,
  useGeneralEntities,
  useGeneralStats,
} from "@/hooks/use-general";

// ---------------------------------------------------------------------------
// Fixed clock
// ---------------------------------------------------------------------------

const FIXED_NOW_ISO = "2026-05-11T12:00:00.000Z";

beforeAll(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(FIXED_NOW_ISO));
});

afterAll(() => {
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// Fixture data
// ---------------------------------------------------------------------------

const NOW = new Date(FIXED_NOW_ISO).getTime();
const H2_AGO = new Date(NOW - 2 * 60 * 60 * 1000).toISOString();
const D3_AGO = new Date(NOW - 3 * 24 * 60 * 60 * 1000).toISOString();

const SAMPLE_STATS = {
  total_collections: 12,
  total_entities: 347,
  last_modified_collection: "Book notes",
  largest_collection_size: 88,
  size_histogram: [
    { bracket: "0", count: 2 },
    { bracket: "1-10", count: 5 },
    { bracket: "11-100", count: 4 },
    { bracket: "101+", count: 1 },
  ],
};

const SAMPLE_COLLECTIONS = [
  {
    id: "col-001",
    name: "Book notes",
    description: "Reading notes",
    entity_count: 88,
    created_at: D3_AGO,
  },
  {
    id: "col-002",
    name: "Ideas",
    description: null,
    entity_count: 15,
    created_at: H2_AGO,
  },
  {
    id: "col-003",
    name: "Recipes",
    description: "Cooking recipes",
    entity_count: 3,
    created_at: D3_AGO,
  },
];

const SAMPLE_ENTITIES = [
  {
    id: "ent-001",
    collection_id: "col-001",
    collection_name: "Book notes",
    data: { title: "Neuromancer" },
    tags: ["fiction", "sci-fi"],
    created_at: H2_AGO,
    updated_at: H2_AGO,
  },
  {
    id: "ent-002",
    collection_id: "col-002",
    collection_name: "Ideas",
    data: { text: "Build a second brain" },
    tags: [],
    created_at: D3_AGO,
    updated_at: D3_AGO,
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderTab() {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={makeQueryClient()}>
        <ButlerGeneralCollectionsTab />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Default mock setups
// ---------------------------------------------------------------------------

function setupWithData() {
  vi.mocked(useGeneralStats).mockReturnValue({
    data: SAMPLE_STATS,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useGeneralStats>);

  vi.mocked(useGeneralCollections).mockReturnValue({
    data: {
      data: SAMPLE_COLLECTIONS,
      meta: { total: SAMPLE_COLLECTIONS.length, offset: 0, limit: 10 },
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useGeneralCollections>);

  vi.mocked(useGeneralEntities).mockReturnValue({
    data: {
      data: SAMPLE_ENTITIES,
      meta: { total: SAMPLE_ENTITIES.length, offset: 0, limit: 5 },
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useGeneralEntities>);
}

function setupEmpty() {
  vi.mocked(useGeneralStats).mockReturnValue({
    data: {
      total_collections: 0,
      total_entities: 0,
      last_modified_collection: null,
      largest_collection_size: 0,
      size_histogram: [
        { bracket: "0", count: 0 },
        { bracket: "1-10", count: 0 },
        { bracket: "11-100", count: 0 },
        { bracket: "101+", count: 0 },
      ],
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useGeneralStats>);

  vi.mocked(useGeneralCollections).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 10 } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useGeneralCollections>);

  vi.mocked(useGeneralEntities).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 5 } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useGeneralEntities>);
}

function setupLoading() {
  vi.mocked(useGeneralStats).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useGeneralStats>);

  vi.mocked(useGeneralCollections).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useGeneralCollections>);

  vi.mocked(useGeneralEntities).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useGeneralEntities>);
}

function setupError() {
  vi.mocked(useGeneralStats).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useGeneralStats>);

  vi.mocked(useGeneralCollections).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useGeneralCollections>);

  vi.mocked(useGeneralEntities).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useGeneralEntities>);
}

// ---------------------------------------------------------------------------
// Tests: Root container + all panels present
// ---------------------------------------------------------------------------

describe("ButlerGeneralCollectionsTab — all panels present", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the root tab container", () => {
    renderTab();
    expect(screen.getByTestId("general-collections-tab")).toBeDefined();
  });

  it("renders the KPI strip panel", () => {
    renderTab();
    expect(screen.getByTestId("kpi-strip")).toBeDefined();
  });

  it("renders the collections directory card", () => {
    renderTab();
    expect(screen.getByTestId("collections-directory-card")).toBeDefined();
  });

  it("renders the recent items card", () => {
    renderTab();
    expect(screen.getByTestId("recent-items-card")).toBeDefined();
  });

  it("renders the size histogram card", () => {
    renderTab();
    expect(screen.getByTestId("size-histogram-card")).toBeDefined();
  });

  it("renders the quick actions card", () => {
    renderTab();
    expect(screen.getByTestId("quick-actions-card")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: KPI rendering
// ---------------------------------------------------------------------------

describe("ButlerGeneralCollectionsTab — KPI rendering", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders 4 KPI items in the strip", () => {
    renderTab();
    const items = screen.getAllByTestId("kpi-item");
    expect(items.length).toBe(4);
  });

  it("shows total collections count", () => {
    renderTab();
    const items = screen.getAllByTestId("kpi-item");
    expect(items[0].textContent).toContain("12");
  });

  it("shows total entities count", () => {
    renderTab();
    const items = screen.getAllByTestId("kpi-item");
    expect(items[1].textContent).toContain("347");
  });

  it("shows last modified collection name", () => {
    renderTab();
    const items = screen.getAllByTestId("kpi-item");
    expect(items[2].textContent).toContain("Book notes");
  });

  it("shows largest collection size", () => {
    renderTab();
    const items = screen.getAllByTestId("kpi-item");
    expect(items[3].textContent).toContain("88");
  });

  it("shows dash when no collections have been modified", () => {
    vi.mocked(useGeneralStats).mockReturnValue({
      data: { ...SAMPLE_STATS, last_modified_collection: null },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useGeneralStats>);
    renderTab();
    const items = screen.getAllByTestId("kpi-item");
    expect(items[2].textContent).toContain("—");
  });
});

// ---------------------------------------------------------------------------
// Tests: Collections table
// ---------------------------------------------------------------------------

describe("ButlerGeneralCollectionsTab — collections table", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the collections table container", () => {
    renderTab();
    expect(screen.getByTestId("collections-table")).toBeDefined();
  });

  it("renders a row for each collection", () => {
    renderTab();
    const rows = screen.getAllByTestId("collection-row");
    expect(rows.length).toBe(SAMPLE_COLLECTIONS.length);
  });

  it("renders collection name in each row", () => {
    renderTab();
    const table = screen.getByTestId("collections-table");
    expect(table.textContent).toContain("Book notes");
    expect(table.textContent).toContain("Ideas");
    expect(table.textContent).toContain("Recipes");
  });

  it("renders entity count in each row", () => {
    renderTab();
    const rows = screen.getAllByTestId("collection-row");
    expect(rows[0].textContent).toContain("88");
  });
});

// ---------------------------------------------------------------------------
// Tests: Recent items sidebar
// ---------------------------------------------------------------------------

describe("ButlerGeneralCollectionsTab — recent items sidebar", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the recent items list", () => {
    renderTab();
    expect(screen.getByTestId("recent-items-list")).toBeDefined();
  });

  it("renders up to 5 items", () => {
    renderTab();
    const items = screen.getAllByTestId("recent-item");
    expect(items.length).toBeLessThanOrEqual(5);
    expect(items.length).toBeGreaterThanOrEqual(1);
  });

  it("renders collection name badges on items", () => {
    renderTab();
    const list = screen.getByTestId("recent-items-list");
    expect(list.textContent).toContain("Book notes");
  });
});

// ---------------------------------------------------------------------------
// Tests: Histogram chart with various bracket counts
// ---------------------------------------------------------------------------

describe("ButlerGeneralCollectionsTab — size histogram", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the histogram chart container when data is present", () => {
    renderTab();
    expect(screen.getByTestId("histogram-chart")).toBeDefined();
  });

  it("renders recharts bar chart inside histogram card", () => {
    renderTab();
    const histCard = screen.getByTestId("size-histogram-card");
    expect(histCard.querySelector('[data-testid="recharts-bar-chart"]')).not.toBeNull();
  });

  it("shows empty state when all bucket counts are zero", () => {
    vi.mocked(useGeneralStats).mockReturnValue({
      data: {
        ...SAMPLE_STATS,
        size_histogram: [
          { bracket: "0", count: 0 },
          { bracket: "1-10", count: 0 },
          { bracket: "11-100", count: 0 },
          { bracket: "101+", count: 0 },
        ],
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useGeneralStats>);
    renderTab();
    expect(screen.getByTestId("size-histogram-card").textContent).toContain(
      "No collections to display.",
    );
  });

  it("renders histogram with only one populated bracket", () => {
    vi.mocked(useGeneralStats).mockReturnValue({
      data: {
        ...SAMPLE_STATS,
        size_histogram: [
          { bracket: "0", count: 0 },
          { bracket: "1-10", count: 7 },
          { bracket: "11-100", count: 0 },
          { bracket: "101+", count: 0 },
        ],
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useGeneralStats>);
    renderTab();
    expect(screen.getByTestId("histogram-chart")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Quick actions
// ---------------------------------------------------------------------------

describe("ButlerGeneralCollectionsTab — quick actions", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the create collection button", () => {
    renderTab();
    expect(screen.getByTestId("create-collection-button")).toBeDefined();
  });

  it("renders the collection search input", () => {
    renderTab();
    expect(screen.getByTestId("collection-search-input")).toBeDefined();
  });

  it("opens create collection dialog when button is clicked", () => {
    renderTab();
    const btn = screen.getByTestId("create-collection-button");
    fireEvent.click(btn);
    expect(screen.getByTestId("create-collection-dialog")).toBeDefined();
  });

  it("confirms create button is disabled when name is empty", () => {
    renderTab();
    fireEvent.click(screen.getByTestId("create-collection-button"));
    const confirmBtn = screen.getByTestId("confirm-create-button");
    expect(confirmBtn.hasAttribute("disabled")).toBe(true);
  });

  it("accepts text input for search", () => {
    renderTab();
    const input = screen.getByTestId("collection-search-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "books" } });
    expect(input.value).toBe("books");
  });
});

// ---------------------------------------------------------------------------
// Tests: Empty state
// ---------------------------------------------------------------------------

describe("ButlerGeneralCollectionsTab — empty state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupEmpty();
  });
  afterEach(() => cleanup());

  it("shows empty state message for collections", () => {
    renderTab();
    expect(screen.getByText("No collections yet.")).toBeDefined();
  });

  it("shows empty state message for recent items", () => {
    renderTab();
    expect(screen.getByText("No items yet.")).toBeDefined();
  });

  it("shows empty state message for histogram when all buckets are zero", () => {
    renderTab();
    expect(screen.getByText("No collections to display.")).toBeDefined();
  });

  it("does not show error banner in empty state", () => {
    renderTab();
    expect(screen.queryByTestId("collections-load-error")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Loading state
// ---------------------------------------------------------------------------

describe("ButlerGeneralCollectionsTab — loading state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupLoading();
  });
  afterEach(() => cleanup());

  it("shows loading skeletons", () => {
    renderTab();
    const loadingLines = screen.getAllByTestId("loading-line");
    expect(loadingLines.length).toBeGreaterThanOrEqual(1);
  });

  it("does not show empty state text while loading", () => {
    renderTab();
    expect(screen.queryByText("No collections yet.")).toBeNull();
    expect(screen.queryByText("No items yet.")).toBeNull();
  });

  it("does not show error banner while loading", () => {
    renderTab();
    expect(screen.queryByTestId("collections-load-error")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Error / isError handling
// ---------------------------------------------------------------------------

describe("ButlerGeneralCollectionsTab — error state", () => {
  afterEach(() => cleanup());

  it("shows error banner when all queries fail", () => {
    vi.resetAllMocks();
    setupError();
    renderTab();
    expect(screen.getByTestId("collections-load-error")).toBeDefined();
  });

  it("shows ErrorLine in KPI strip when stats fail", () => {
    vi.resetAllMocks();
    setupError();
    renderTab();
    const errorLines = screen.getAllByTestId("error-state-line");
    expect(errorLines.length).toBeGreaterThanOrEqual(1);
  });

  it("shows ErrorLine in collections directory when collections fail", () => {
    vi.resetAllMocks();
    // Stats succeed, collections fail
    vi.mocked(useGeneralStats).mockReturnValue({
      data: SAMPLE_STATS,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useGeneralStats>);
    vi.mocked(useGeneralCollections).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as unknown as ReturnType<typeof useGeneralCollections>);
    vi.mocked(useGeneralEntities).mockReturnValue({
      data: { data: SAMPLE_ENTITIES, meta: { total: 2, offset: 0, limit: 5 } },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useGeneralEntities>);
    renderTab();
    // Error banner visible
    expect(screen.getByTestId("collections-load-error")).toBeDefined();
    // ErrorLine in collections directory
    expect(screen.getByText("Could not load collections.")).toBeDefined();
  });

  it("shows ErrorLine in recent items when entities fail", () => {
    vi.resetAllMocks();
    vi.mocked(useGeneralStats).mockReturnValue({
      data: SAMPLE_STATS,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useGeneralStats>);
    vi.mocked(useGeneralCollections).mockReturnValue({
      data: { data: SAMPLE_COLLECTIONS, meta: { total: 3, offset: 0, limit: 10 } },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useGeneralCollections>);
    vi.mocked(useGeneralEntities).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as unknown as ReturnType<typeof useGeneralEntities>);
    renderTab();
    expect(screen.getByText("Could not load recent items.")).toBeDefined();
  });
});
