// @vitest-environment jsdom
/**
 * ButlerChroniclerTimelinesTab — RTL tests pinning all 5 sections.
 *
 * Tests:
 *  - All 5 sections render (KPI strip, today timeline, sources, category breakdown, day-close)
 *  - Loading states show loading placeholders, not empty-state text
 *  - Empty states are shown explicitly when data is empty
 *  - Episode spine renders items with privacy masking for sensitive episodes
 *  - KPI values render with data
 *  - Day-close stale marker renders when response.stale is true
 *  - Day-close prose renders when response.stale is false
 *  - Pagination: "Load more" button shown when has_more=true, hidden when false
 *  - Pagination: clicking "Load more" triggers fetch with offset=50
 *  - Pagination: button shows "Loading…" while isFetching and offset > 0
 *
 * bead: bu-aeg7w
 */

import { describe, it, expect, vi, beforeAll, afterAll, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AppTimezoneProvider } from "@/components/ui/timezone-context";
import ButlerChroniclerTimelinesTab from "./ButlerChroniclerTimelinesTab";

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-chronicles-kpi", () => ({
  useChroniclesKpi: vi.fn(),
}));

vi.mock("@/hooks/use-chronicles", () => ({
  useChroniclesEpisodes: vi.fn(),
  useChroniclesByCategory: vi.fn(),
  useChroniclesSourceState: vi.fn(),
  useChroniclesDayClose: vi.fn(),
}));

import { useChroniclesKpi } from "@/hooks/use-chronicles-kpi";
import {
  useChroniclesEpisodes,
  useChroniclesByCategory,
  useChroniclesSourceState,
  useChroniclesDayClose,
} from "@/hooks/use-chronicles";

// ---------------------------------------------------------------------------
// Fixed clock — prevents midnight/timezone flakes
// ---------------------------------------------------------------------------

/** Fixed date used for all time-dependent fixtures. */
const FIXED_NOW_ISO = "2026-05-10T08:00:00.000Z";
const TODAY = "2026-05-10";

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

const KPI_DATA = {
  data: {
    hours_by_top_lanes: [
      { lane: "work", hours: 5.5 },
      { lane: "music", hours: 2.0 },
      { lane: "exercise", hours: 1.0 },
    ],
    longest_episode_minutes: 90,
    longest_episode_title: "Deep work session",
    longest_gap_minutes: 45,
    sleep_minutes: 480,
    streaks: { sleep: 7, exercise: 3 },
  },
};

const EPISODES = [
  {
    id: "ep-1",
    source_name: "toggl",
    source_ref: "ref-1",
    episode_type: "task",
    start_at: `${TODAY}T09:00:00Z`,
    end_at: `${TODAY}T10:30:00Z`,
    precision: "minute",
    title: "Morning work block",
    payload: {},
    privacy: "normal",
    retention_days: null,
    tombstone_at: null,
    canonical_start_at: `${TODAY}T09:00:00Z`,
    canonical_end_at: `${TODAY}T10:30:00Z`,
    canonical_title: "Morning work block",
    canonical_privacy: "normal",
    corrected_at: null,
    correction_note: null,
    created_at: `${TODAY}T00:00:00Z`,
    updated_at: `${TODAY}T00:00:00Z`,
    category: "work",
  },
  {
    id: "ep-2",
    source_name: "sleep_monitor",
    source_ref: "ref-2",
    episode_type: "sleep",
    start_at: `${TODAY}T00:00:00Z`,
    end_at: `${TODAY}T08:00:00Z`,
    precision: "minute",
    title: "Night sleep",
    payload: {},
    privacy: "sensitive",
    retention_days: 90,
    tombstone_at: null,
    canonical_start_at: `${TODAY}T00:00:00Z`,
    canonical_end_at: `${TODAY}T08:00:00Z`,
    canonical_title: "Night sleep",
    canonical_privacy: "sensitive",
    corrected_at: null,
    correction_note: null,
    created_at: `${TODAY}T00:00:00Z`,
    updated_at: `${TODAY}T00:00:00Z`,
    category: "sleep",
  },
];

const SOURCE_ROWS = [
  {
    source_name: "toggl",
    chronicler_compatibility: "supported",
    read_surface: "api",
    boundary_semantics: "closed",
    optional_schema: false,
    active: true,
    inactive_reason: null,
    last_run_at: `${TODAY}T10:00:00Z`,
    last_error: null,
    subsource_checkpoints: null,
  },
  {
    source_name: "calendar",
    chronicler_compatibility: "supported",
    read_surface: "api",
    boundary_semantics: "open",
    optional_schema: false,
    active: false,
    inactive_reason: "No credentials configured",
    last_run_at: null,
    last_error: "Auth error",
    subsource_checkpoints: null,
  },
];

const CATEGORY_BUCKETS = [
  {
    category: "work",
    total_seconds: 19800,
    episode_count: 5,
    source_breakdown: [],
    precision: "minute",
    retention_floor_days: null,
  },
  {
    category: "sleep",
    total_seconds: 28800,
    episode_count: 1,
    source_breakdown: [],
    precision: "minute",
    retention_floor_days: 90,
  },
];

const DAY_CLOSE_FRESH = {
  stale: false as const,
  prose:
    "Today was a productive day. You spent most of the morning working, followed by a long exercise session in the afternoon.",
  provenance_refs: ["ep-1"],
  cache_built_at: `${TODAY}T23:00:00Z`,
};

const DAY_CLOSE_STALE = {
  stale: true as const,
  cache_built_at: `${TODAY}T20:00:00Z`,
  last_invalidating_event_at: `${TODAY}T22:30:00Z`,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderTab() {
  return render(
    <AppTimezoneProvider timezone="Asia/Singapore">
      <QueryClientProvider client={makeQueryClient()}>
        <ButlerChroniclerTimelinesTab />
      </QueryClientProvider>
    </AppTimezoneProvider>,
  );
}

// ---------------------------------------------------------------------------
// Default mock setup: all data loaded
// ---------------------------------------------------------------------------

function setupWithData() {
  vi.mocked(useChroniclesKpi).mockReturnValue({
    data: KPI_DATA,
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useChroniclesKpi>);

  vi.mocked(useChroniclesEpisodes).mockReturnValue({
    data: { data: EPISODES, meta: { total: 2, offset: 0, limit: 50, has_more: false } },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useChroniclesEpisodes>);

  vi.mocked(useChroniclesSourceState).mockReturnValue({
    data: { data: SOURCE_ROWS, meta: {} },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useChroniclesSourceState>);

  vi.mocked(useChroniclesByCategory).mockReturnValue({
    data: { data: { start_at: "", end_at: "", tz: "UTC", buckets: CATEGORY_BUCKETS }, meta: {} },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useChroniclesByCategory>);

  vi.mocked(useChroniclesDayClose).mockReturnValue({
    data: DAY_CLOSE_FRESH,
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useChroniclesDayClose>);
}

function setupEmpty() {
  vi.mocked(useChroniclesKpi).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useChroniclesKpi>);

  vi.mocked(useChroniclesEpisodes).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useChroniclesEpisodes>);

  vi.mocked(useChroniclesSourceState).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useChroniclesSourceState>);

  vi.mocked(useChroniclesByCategory).mockReturnValue({
    data: { data: { start_at: "", end_at: "", tz: "UTC", buckets: [] }, meta: {} },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useChroniclesByCategory>);

  vi.mocked(useChroniclesDayClose).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useChroniclesDayClose>);
}

function setupLoading() {
  vi.mocked(useChroniclesKpi).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useChroniclesKpi>);

  vi.mocked(useChroniclesEpisodes).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useChroniclesEpisodes>);

  vi.mocked(useChroniclesSourceState).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useChroniclesSourceState>);

  vi.mocked(useChroniclesByCategory).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useChroniclesByCategory>);

  vi.mocked(useChroniclesDayClose).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useChroniclesDayClose>);
}

// ---------------------------------------------------------------------------
// Tests: 5 sections present
// ---------------------------------------------------------------------------

describe("ButlerChroniclerTimelinesTab — all 5 sections present", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the KPI strip section", () => {
    renderTab();
    expect(screen.getByTestId("kpi-strip")).toBeDefined();
  });

  it("renders the today timeline card", () => {
    renderTab();
    expect(screen.getByTestId("today-timeline-card")).toBeDefined();
  });

  it("renders the sources card", () => {
    renderTab();
    expect(screen.getByTestId("sources-card")).toBeDefined();
  });

  it("renders the category breakdown card", () => {
    renderTab();
    expect(screen.getByTestId("category-breakdown-card")).toBeDefined();
  });

  it("renders the day-close card", () => {
    renderTab();
    expect(screen.getByTestId("day-close-card")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: KPI values render with data
// ---------------------------------------------------------------------------

describe("ButlerChroniclerTimelinesTab — KPI values", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders kpi-item elements for top lanes and sleep", () => {
    renderTab();
    const items = screen.getAllByTestId("kpi-item");
    // 3 top lanes + sleep + sleep streak + longest episode = 6
    expect(items.length).toBeGreaterThanOrEqual(4);
  });

  it("renders KPI value for sleep streak", () => {
    renderTab();
    const values = screen.getAllByTestId("kpi-value");
    const texts = values.map((v) => v.textContent ?? "");
    expect(texts.some((t) => t.includes("7d"))).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Tests: Episode spine renders items with privacy masking
// ---------------------------------------------------------------------------

describe("ButlerChroniclerTimelinesTab — episode spine", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders episode spine items", () => {
    renderTab();
    const items = screen.getAllByTestId("episode-spine-item");
    expect(items.length).toBeGreaterThanOrEqual(1);
  });

  it("renders normal episode title as plain text", () => {
    renderTab();
    expect(screen.getByText("Morning work block")).toBeDefined();
  });

  it("masks sensitive episode title as '···'", () => {
    renderTab();
    const masked = screen.queryAllByText("···");
    expect(masked.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: Source health widget
// ---------------------------------------------------------------------------

describe("ButlerChroniclerTimelinesTab — source health widget", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders source health rows", () => {
    renderTab();
    const rows = screen.getAllByTestId("source-health-row");
    expect(rows.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: Category breakdown
// ---------------------------------------------------------------------------

describe("ButlerChroniclerTimelinesTab — category breakdown", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders category breakdown items", () => {
    renderTab();
    const items = screen.getAllByTestId("category-breakdown-item");
    expect(items.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: Day-close prose renders fresh response
// ---------------------------------------------------------------------------

describe("ButlerChroniclerTimelinesTab — day-close prose (fresh)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders day-close prose container", () => {
    renderTab();
    expect(screen.getByTestId("day-close-prose")).toBeDefined();
  });

  it("renders the day-close prose text", () => {
    renderTab();
    expect(screen.getByText(/Today was a productive day/)).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Day-close stale marker
// ---------------------------------------------------------------------------

describe("ButlerChroniclerTimelinesTab — day-close stale marker", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
    // Override day-close with stale response
    vi.mocked(useChroniclesDayClose).mockReturnValue({
      data: DAY_CLOSE_STALE,
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useChroniclesDayClose>);
  });
  afterEach(() => cleanup());

  it("renders stale marker when day-close is stale", () => {
    renderTab();
    expect(screen.getByTestId("day-close-stale")).toBeDefined();
  });

  it("shows stale message text", () => {
    renderTab();
    expect(screen.getByText(/Summary is stale/)).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Empty states
// ---------------------------------------------------------------------------

describe("ButlerChroniclerTimelinesTab — empty states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupEmpty();
  });
  afterEach(() => cleanup());

  it("shows empty state for KPI when no data", () => {
    renderTab();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });

  it("shows empty state for episodes when no episodes", () => {
    renderTab();
    expect(screen.queryByTestId("episode-spine")).toBeNull();
  });

  it("shows empty state for sources when no sources", () => {
    renderTab();
    expect(screen.queryByTestId("source-health-list")).toBeNull();
  });

  it("shows empty state for category breakdown when no buckets", () => {
    renderTab();
    expect(screen.queryByTestId("category-breakdown-list")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Loading states
// ---------------------------------------------------------------------------

describe("ButlerChroniclerTimelinesTab — loading states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupLoading();
  });
  afterEach(() => cleanup());

  it("shows loading placeholders while loading", () => {
    renderTab();
    const loadingLines = screen.getAllByTestId("loading-line");
    expect(loadingLines.length).toBeGreaterThanOrEqual(1);
  });

  it("does not show empty-state text while loading", () => {
    renderTab();
    expect(screen.queryByTestId("empty-state-line")).toBeNull();
  });

  it("does not render episode-spine while loading", () => {
    renderTab();
    expect(screen.queryByTestId("episode-spine")).toBeNull();
  });

  it("does not render category-breakdown-list while loading", () => {
    renderTab();
    expect(screen.queryByTestId("category-breakdown-list")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: "Load more" pagination
// ---------------------------------------------------------------------------

describe("ButlerChroniclerTimelinesTab — episode timeline pagination", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("hides Load more button when has_more is false", () => {
    // setupWithData returns has_more: false
    renderTab();
    expect(screen.queryByTestId("load-more-button")).toBeNull();
  });

  it("shows Load more button when has_more is true", () => {
    vi.mocked(useChroniclesEpisodes).mockReturnValue({
      data: { data: EPISODES, meta: { total: 100, offset: 0, limit: 50, has_more: true } },
      isLoading: false,
      isFetching: false,
      isError: false,
    } as ReturnType<typeof useChroniclesEpisodes>);

    renderTab();
    expect(screen.getByTestId("load-more-button")).toBeDefined();
    expect(screen.getByTestId("load-more-button").textContent).toBe("Load more");
  });

  it("clicking Load more triggers a new fetch with offset=50", () => {
    vi.mocked(useChroniclesEpisodes).mockReturnValue({
      data: { data: EPISODES, meta: { total: 100, offset: 0, limit: 50, has_more: true } },
      isLoading: false,
      isFetching: false,
      isError: false,
    } as ReturnType<typeof useChroniclesEpisodes>);

    renderTab();
    const btn = screen.getByTestId("load-more-button");
    fireEvent.click(btn);

    // After clicking, useChroniclesEpisodes must have been called with offset: 50.
    const calls = vi.mocked(useChroniclesEpisodes).mock.calls;
    const offsetCalls = calls.filter((c) => c[0]?.offset === 50);
    expect(offsetCalls.length).toBeGreaterThanOrEqual(1);
  });

  it("shows Loading… text on Load more button while fetching next page", () => {
    // To trigger isFetchingMore=true in the component, we need:
    //   1. nextOffset > 0 (set by clicking "Load more")
    //   2. isFetching=true from the hook
    // Strategy: render page 0 (has_more=true), click the button, then observe that
    // the hook is called with offset=50; stub subsequent calls as isFetching=true.
    vi.mocked(useChroniclesEpisodes)
      // First call: page 0 loaded successfully.
      .mockReturnValueOnce({
        data: { data: EPISODES, meta: { total: 100, offset: 0, limit: 50, has_more: true } },
        isLoading: false,
        isFetching: false,
        isError: false,
      } as ReturnType<typeof useChroniclesEpisodes>)
      // Second call onward (after click → offset becomes 50): page is fetching.
      .mockReturnValue({
        data: undefined,
        isLoading: false,
        isFetching: true,
        isError: false,
      } as ReturnType<typeof useChroniclesEpisodes>);

    renderTab();

    // Click Load more — nextOffset advances to 50, causing a re-render with the second mock.
    fireEvent.click(screen.getByTestId("load-more-button"));

    // Button should now show "Loading…" and be disabled.
    const btn = screen.getByTestId("load-more-button");
    expect(btn.textContent).toBe("Loading…");
    expect((btn as HTMLButtonElement).disabled).toBe(true);
  });
});
