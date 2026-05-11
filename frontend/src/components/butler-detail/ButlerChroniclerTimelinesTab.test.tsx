// @vitest-environment jsdom
/**
 * ButlerChroniclerTimelinesTab — RTL tests for the redesigned tab (bu-iuol4.25).
 *
 * Tests:
 *  - Main sections present: KPI strip, today timeline, sources
 *  - KPI rendering: today events count, sources live count, longest gap
 *  - KPI: next assembly shows relative time when schedule data is available
 *  - Loading states show loading placeholders, not empty-state text
 *  - Today timeline empty state when no episodes
 *  - Sources: live/stale/offline status badge variants
 *  - Sources empty state when no sources configured
 *  - Episode spine renders items with privacy masking for sensitive episodes
 *  - Pagination: "Load more" button shown when hasNextPage=true, hidden when false
 *  - Pagination: clicking "Load more" calls fetchNextPage
 *  - Pagination: button shows "Loading…" while isFetchingNextPage
 *  - Multi-page live update: all loaded pages remain active after Load more
 *
 * bead: bu-iuol4.25
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
  useChroniclesEpisodesInfinite: vi.fn(),
  useChroniclesSourceState: vi.fn(),
}));

vi.mock("@/hooks/use-schedules", () => ({
  useSchedules: vi.fn(),
}));

import { useChroniclesKpi } from "@/hooks/use-chronicles-kpi";
import {
  useChroniclesEpisodesInfinite,
  useChroniclesSourceState,
} from "@/hooks/use-chronicles";
import { useSchedules } from "@/hooks/use-schedules";

// ---------------------------------------------------------------------------
// Fixed clock — prevents midnight/timezone flakes
// ---------------------------------------------------------------------------

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

// SOURCE_ROWS: toggl = live (active, recent), calendar = offline (inactive), spotify = planned
const SOURCE_ROWS = [
  {
    source_name: "toggl",
    chronicler_compatibility: "supported",
    read_surface: "api",
    boundary_semantics: "closed",
    optional_schema: false,
    active: true,
    inactive_reason: null,
    // last_run_at within 2h of FIXED_NOW_ISO (08:00Z), so "live"
    last_run_at: `${TODAY}T07:30:00Z`,
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
  {
    source_name: "spotify",
    chronicler_compatibility: "planned",
    read_surface: null,
    boundary_semantics: null,
    optional_schema: false,
    active: false,
    inactive_reason: null,
    last_run_at: null,
    last_error: null,
    subsource_checkpoints: null,
  },
];

// A source that is active but with a last_run_at older than 2h = "stale"
const STALE_SOURCE = {
  source_name: "home_assistant",
  chronicler_compatibility: "supported",
  read_surface: "api",
  boundary_semantics: "open",
  optional_schema: false,
  active: true,
  inactive_reason: null,
  // more than 2h before FIXED_NOW_ISO (08:00Z)
  last_run_at: `${TODAY}T05:00:00Z`,
  last_error: null,
  subsource_checkpoints: null,
};

const SCHEDULES_DATA = {
  data: [
    {
      id: "sched-1",
      name: "chronicler_day_close",
      cron: "5 1 * * *",
      prompt: "...",
      dispatch_mode: "prompt",
      job_name: null,
      job_args: null,
      complexity: "high",
      source: "toml",
      enabled: true,
      // 17 hours in the future from FIXED_NOW_ISO = next day 01:05Z
      next_run_at: "2026-05-11T01:05:00Z",
      last_run_at: "2026-05-10T01:05:00Z",
      created_at: `${TODAY}T00:00:00Z`,
      updated_at: `${TODAY}T00:00:00Z`,
    },
  ],
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

  vi.mocked(useChroniclesEpisodesInfinite).mockReturnValue({
    data: {
      pages: [{ data: EPISODES, meta: { total: 2, offset: 0, limit: 50, has_more: false } }],
      pageParams: [0],
    },
    isLoading: false,
    isFetchingNextPage: false,
    hasNextPage: false,
    fetchNextPage: vi.fn(),
    isError: false,
  } as unknown as ReturnType<typeof useChroniclesEpisodesInfinite>);

  vi.mocked(useChroniclesSourceState).mockReturnValue({
    data: { data: SOURCE_ROWS, meta: {} },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useChroniclesSourceState>);

  vi.mocked(useSchedules).mockReturnValue({
    data: SCHEDULES_DATA,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useSchedules>);
}

function setupEmpty() {
  vi.mocked(useChroniclesKpi).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useChroniclesKpi>);

  vi.mocked(useChroniclesEpisodesInfinite).mockReturnValue({
    data: {
      pages: [{ data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } }],
      pageParams: [0],
    },
    isLoading: false,
    isFetchingNextPage: false,
    hasNextPage: false,
    fetchNextPage: vi.fn(),
    isError: false,
  } as unknown as ReturnType<typeof useChroniclesEpisodesInfinite>);

  vi.mocked(useChroniclesSourceState).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useChroniclesSourceState>);

  vi.mocked(useSchedules).mockReturnValue({
    data: { data: [] },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useSchedules>);
}

function setupLoading() {
  vi.mocked(useChroniclesKpi).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useChroniclesKpi>);

  vi.mocked(useChroniclesEpisodesInfinite).mockReturnValue({
    data: undefined,
    isLoading: true,
    isFetchingNextPage: false,
    hasNextPage: false,
    fetchNextPage: vi.fn(),
    isError: false,
  } as unknown as ReturnType<typeof useChroniclesEpisodesInfinite>);

  vi.mocked(useChroniclesSourceState).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as ReturnType<typeof useChroniclesSourceState>);

  vi.mocked(useSchedules).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useSchedules>);
}

// ---------------------------------------------------------------------------
// Tests: main sections present
// ---------------------------------------------------------------------------

describe("ButlerChroniclerTimelinesTab — main sections present", () => {
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

  it("does not render a category breakdown card", () => {
    renderTab();
    expect(screen.queryByTestId("category-breakdown-card")).toBeNull();
  });

  it("does not render a day-close card", () => {
    renderTab();
    expect(screen.queryByTestId("day-close-card")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: KPI rendering
// ---------------------------------------------------------------------------

describe("ButlerChroniclerTimelinesTab — KPI rendering", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders 4 kpi-item cells", () => {
    renderTab();
    const items = screen.getAllByTestId("kpi-item");
    expect(items.length).toBe(4);
  });

  it("renders today events count (2 episodes in fixture)", () => {
    renderTab();
    // The KpiCell value for today events shows the episode count
    const items = screen.getAllByTestId("kpi-item");
    const texts = items.map((el) => el.textContent ?? "");
    // First cell: "Today events" label + count "2"
    expect(texts[0]).toContain("Today events");
    expect(texts[0]).toContain("2");
  });

  it("renders sources live count (1 active source within 2h)", () => {
    renderTab();
    const items = screen.getAllByTestId("kpi-item");
    const texts = items.map((el) => el.textContent ?? "");
    // Second cell: "Sources live" label + count "1"
    expect(texts[1]).toContain("Sources live");
    expect(texts[1]).toContain("1");
  });

  it("renders longest gap from KPI data (45 min = 45m)", () => {
    renderTab();
    const items = screen.getAllByTestId("kpi-item");
    const texts = items.map((el) => el.textContent ?? "");
    // Third cell: "Longest gap"
    expect(texts[2]).toContain("Longest gap");
    expect(texts[2]).toContain("45m");
  });

  it("renders next assembly cell label", () => {
    renderTab();
    const items = screen.getAllByTestId("kpi-item");
    const texts = items.map((el) => el.textContent ?? "");
    // Fourth cell: "Next assembly"
    expect(texts[3]).toContain("Next assembly");
  });

  it("shows dash for next assembly when no schedule data", () => {
    vi.mocked(useSchedules).mockReturnValue({
      data: { data: [] },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useSchedules>);

    renderTab();
    const items = screen.getAllByTestId("kpi-item");
    const assemblyText = items[3].textContent ?? "";
    expect(assemblyText).toContain("—");
  });

  it("renders a relative time value for next assembly when schedule data is present", () => {
    renderTab();
    // setupWithData provides SCHEDULES_DATA with next_run_at 17h in the future
    // The 4th KPI item should contain a <time> element (rendered by <Time>)
    const items = screen.getAllByTestId("kpi-item");
    const assemblyCell = items[3];
    const timeEl = assemblyCell.querySelector("time");
    expect(timeEl).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Episode spine
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
// Tests: Today timeline empty state
// ---------------------------------------------------------------------------

describe("ButlerChroniclerTimelinesTab — today timeline empty state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupEmpty();
  });
  afterEach(() => cleanup());

  it("shows empty state when no episodes recorded", () => {
    renderTab();
    expect(screen.queryByTestId("episode-spine")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: Sources status badge variants (live / stale / offline)
// ---------------------------------------------------------------------------

describe("ButlerChroniclerTimelinesTab — sources status badge variants", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders source-health-list when sources are configured", () => {
    renderTab();
    expect(screen.getByTestId("source-health-list")).toBeDefined();
  });

  it("renders a live badge for an active source with recent last_run_at", () => {
    renderTab();
    const liveBadges = screen.getAllByTestId("source-status-badge-live");
    expect(liveBadges.length).toBeGreaterThanOrEqual(1);
  });

  it("renders an offline badge for an inactive source", () => {
    renderTab();
    const offlineBadges = screen.getAllByTestId("source-status-badge-offline");
    expect(offlineBadges.length).toBeGreaterThanOrEqual(1);
  });

  it("renders a planned badge for a planned source", () => {
    renderTab();
    const plannedBadges = screen.getAllByTestId("source-status-badge-planned");
    expect(plannedBadges.length).toBeGreaterThanOrEqual(1);
  });

  it("renders a stale badge for an active source with old last_run_at", () => {
    vi.mocked(useChroniclesSourceState).mockReturnValue({
      data: { data: [STALE_SOURCE], meta: {} },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useChroniclesSourceState>);

    renderTab();
    const staleBadges = screen.getAllByTestId("source-status-badge-stale");
    expect(staleBadges.length).toBeGreaterThanOrEqual(1);
  });

  it("shows empty state when no sources configured", () => {
    vi.mocked(useChroniclesSourceState).mockReturnValue({
      data: { data: [], meta: {} },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useChroniclesSourceState>);

    renderTab();
    expect(screen.queryByTestId("source-health-list")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
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
});

// ---------------------------------------------------------------------------
// Tests: Pagination ("Load more")
// ---------------------------------------------------------------------------

describe("ButlerChroniclerTimelinesTab — episode timeline pagination", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("hides Load more button when hasNextPage is false", () => {
    renderTab();
    expect(screen.queryByTestId("load-more-button")).toBeNull();
  });

  it("shows Load more button when hasNextPage is true", () => {
    vi.mocked(useChroniclesEpisodesInfinite).mockReturnValue({
      data: {
        pages: [{ data: EPISODES, meta: { total: 100, offset: 0, limit: 50, has_more: true } }],
        pageParams: [0],
      },
      isLoading: false,
      isFetchingNextPage: false,
      hasNextPage: true,
      fetchNextPage: vi.fn(),
      isError: false,
    } as unknown as ReturnType<typeof useChroniclesEpisodesInfinite>);

    renderTab();
    expect(screen.getByTestId("load-more-button")).toBeDefined();
    expect(screen.getByTestId("load-more-button").textContent).toBe("Load more");
  });

  it("clicking Load more calls fetchNextPage", () => {
    const fetchNextPage = vi.fn().mockResolvedValue(undefined);
    vi.mocked(useChroniclesEpisodesInfinite).mockReturnValue({
      data: {
        pages: [{ data: EPISODES, meta: { total: 100, offset: 0, limit: 50, has_more: true } }],
        pageParams: [0],
      },
      isLoading: false,
      isFetchingNextPage: false,
      hasNextPage: true,
      fetchNextPage,
      isError: false,
    } as unknown as ReturnType<typeof useChroniclesEpisodesInfinite>);

    renderTab();
    fireEvent.click(screen.getByTestId("load-more-button"));
    expect(fetchNextPage).toHaveBeenCalledTimes(1);
  });

  it("shows Loading text on Load more button while isFetchingNextPage", () => {
    vi.mocked(useChroniclesEpisodesInfinite).mockReturnValue({
      data: {
        pages: [{ data: EPISODES, meta: { total: 100, offset: 0, limit: 50, has_more: true } }],
        pageParams: [0],
      },
      isLoading: false,
      isFetchingNextPage: true,
      hasNextPage: true,
      fetchNextPage: vi.fn(),
      isError: false,
    } as unknown as ReturnType<typeof useChroniclesEpisodesInfinite>);

    renderTab();
    const btn = screen.getByTestId("load-more-button");
    expect(btn.textContent).toBe("Loading…");
    expect((btn as HTMLButtonElement).disabled).toBe(true);
  });

  it("renders episodes from all loaded pages (live update: first page stays active)", () => {
    const EP_PAGE2 = {
      id: "ep-3",
      source_name: "toggl",
      source_ref: "ref-3",
      episode_type: "task",
      start_at: `${TODAY}T14:00:00Z`,
      end_at: `${TODAY}T15:00:00Z`,
      precision: "minute",
      title: "Afternoon session",
      payload: {},
      privacy: "normal",
      retention_days: null,
      tombstone_at: null,
      canonical_start_at: `${TODAY}T14:00:00Z`,
      canonical_end_at: `${TODAY}T15:00:00Z`,
      canonical_title: "Afternoon session",
      canonical_privacy: "normal",
      corrected_at: null,
      correction_note: null,
      created_at: `${TODAY}T00:00:00Z`,
      updated_at: `${TODAY}T00:00:00Z`,
      category: "work",
    };

    vi.mocked(useChroniclesEpisodesInfinite).mockReturnValue({
      data: {
        pages: [
          { data: EPISODES, meta: { total: 103, offset: 0, limit: 50, has_more: true } },
          { data: [EP_PAGE2], meta: { total: 103, offset: 50, limit: 50, has_more: false } },
        ],
        pageParams: [0, 50],
      },
      isLoading: false,
      isFetchingNextPage: false,
      hasNextPage: false,
      fetchNextPage: vi.fn(),
      isError: false,
    } as unknown as ReturnType<typeof useChroniclesEpisodesInfinite>);

    renderTab();

    const items = screen.getAllByTestId("episode-spine-item");
    expect(items.length).toBe(3);
    expect(screen.getByText("Afternoon session")).toBeDefined();
    expect(screen.getByText("Morning work block")).toBeDefined();
  });
});
