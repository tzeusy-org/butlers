// @vitest-environment jsdom
/**
 * ButlerMemoryTab — RTL tests.
 *
 * Tests cover:
 *  - Root container renders
 *  - KPI quartet renders with all 4 counts populated
 *  - Recent writes panel renders with ordered episodes
 *  - Empty state: empty-state line shown when no episodes
 *  - isError state: ErrorLine shown in KPI and recent-writes panels
 *  - Loading state: skeletons shown, no data displayed
 *  - Sub-labels for facts (active) and rules (active) shown when available
 *
 * bead: bu-iuol4.20
 */

import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";

import ButlerMemoryTab from "./ButlerMemoryTab";

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-memory", () => ({
  useMemoryStats: vi.fn(),
  useMemoryRecentWrites: vi.fn(),
  useEntities: vi.fn(),
}));

// Stub <Time> to avoid timezone / date-fns complexity in jsdom
vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) =>
    createElement("time", { dateTime: value }, value),
}));

import {
  useMemoryStats,
  useMemoryRecentWrites,
  useEntities,
} from "@/hooks/use-memory";

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
const MIN5_AGO = new Date(NOW - 5 * 60 * 1_000).toISOString();
const MIN10_AGO = new Date(NOW - 10 * 60 * 1_000).toISOString();
const MIN20_AGO = new Date(NOW - 20 * 60 * 1_000).toISOString();

const MEMORY_STATS = {
  total_episodes: 42,
  unconsolidated_episodes: 8,
  total_facts: 215,
  active_facts: 190,
  fading_facts: 25,
  total_rules: 30,
  candidate_rules: 5,
  established_rules: 18,
  proven_rules: 7,
  anti_pattern_rules: 0,
};

const ENTITIES_RESPONSE = {
  data: [],
  meta: { total: 64, offset: 0, limit: 1, has_more: true },
};

const EPISODES = [
  {
    id: "ep-00000001-aaaa-bbbb-cccc-ddddeeee0001",
    butler: "memory",
    session_id: "sess-001",
    content: "User asked about travel plans to Japan.",
    importance: 0.8,
    reference_count: 2,
    consolidated: false,
    created_at: MIN5_AGO,
    last_referenced_at: null,
    expires_at: null,
    metadata: {},
  },
  {
    id: "ep-00000002-aaaa-bbbb-cccc-ddddeeee0002",
    butler: "memory",
    session_id: "sess-002",
    content: "Discussed upcoming health check appointment.",
    importance: 0.6,
    reference_count: 0,
    consolidated: false,
    created_at: MIN10_AGO,
    last_referenced_at: null,
    expires_at: null,
    metadata: {},
  },
  {
    id: "ep-00000003-aaaa-bbbb-cccc-ddddeeee0003",
    butler: "memory",
    session_id: "sess-003",
    content: "Preference noted: prefers concise summaries.",
    importance: 0.9,
    reference_count: 5,
    consolidated: true,
    created_at: MIN20_AGO,
    last_referenced_at: null,
    expires_at: null,
    metadata: {},
  },
];

const RECENT_WRITES_RESPONSE = {
  data: EPISODES,
  meta: { total: 42, offset: 0, limit: 10, has_more: true },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderTab(butlerName = "memory") {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={makeQueryClient()}>
        <ButlerMemoryTab butlerName={butlerName} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Default mock setups
// ---------------------------------------------------------------------------

function setupWithData() {
  vi.mocked(useMemoryStats).mockReturnValue({
    data: { data: MEMORY_STATS },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useMemoryStats>);

  vi.mocked(useEntities).mockReturnValue({
    data: ENTITIES_RESPONSE,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useEntities>);

  vi.mocked(useMemoryRecentWrites).mockReturnValue({
    data: RECENT_WRITES_RESPONSE,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useMemoryRecentWrites>);
}

function setupEmpty() {
  vi.mocked(useMemoryStats).mockReturnValue({
    data: {
      data: {
        total_episodes: 0,
        unconsolidated_episodes: 0,
        total_facts: 0,
        active_facts: 0,
        fading_facts: 0,
        total_rules: 0,
        candidate_rules: 0,
        established_rules: 0,
        proven_rules: 0,
        anti_pattern_rules: 0,
      },
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useMemoryStats>);

  vi.mocked(useEntities).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 1, has_more: false } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useEntities>);

  vi.mocked(useMemoryRecentWrites).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 10, has_more: false } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useMemoryRecentWrites>);
}

function setupLoading() {
  vi.mocked(useMemoryStats).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useMemoryStats>);

  vi.mocked(useEntities).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useEntities>);

  vi.mocked(useMemoryRecentWrites).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useMemoryRecentWrites>);
}

function setupError() {
  vi.mocked(useMemoryStats).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useMemoryStats>);

  vi.mocked(useEntities).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useEntities>);

  vi.mocked(useMemoryRecentWrites).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useMemoryRecentWrites>);
}

// ---------------------------------------------------------------------------
// Cleanup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ButlerMemoryTab", () => {
  it("renders the root container", () => {
    setupWithData();
    renderTab();
    expect(screen.getByTestId("butler-memory-tab")).toBeDefined();
  });

  describe("KPI quartet — all 4 counts populated", () => {
    it("renders all 4 KPI cells", () => {
      setupWithData();
      renderTab();
      const kpiItems = screen.getAllByTestId("kpi-item");
      expect(kpiItems.length).toBe(4);
    });

    it("shows episode count", () => {
      setupWithData();
      renderTab();
      const quartet = screen.getByTestId("kpi-quartet");
      expect(quartet.textContent).toContain("42");
    });

    it("shows fact count", () => {
      setupWithData();
      renderTab();
      const quartet = screen.getByTestId("kpi-quartet");
      expect(quartet.textContent).toContain("215");
    });

    it("shows entity count from entities meta.total", () => {
      setupWithData();
      renderTab();
      const quartet = screen.getByTestId("kpi-quartet");
      expect(quartet.textContent).toContain("64");
    });

    it("shows rule count", () => {
      setupWithData();
      renderTab();
      const quartet = screen.getByTestId("kpi-quartet");
      expect(quartet.textContent).toContain("30");
    });

    it("shows active facts sub-label", () => {
      setupWithData();
      renderTab();
      // active_facts = 190 → sub "+190 active"
      const quartet = screen.getByTestId("kpi-quartet");
      expect(quartet.textContent).toContain("+190 active");
    });

    it("shows active rules sub-label (established + proven)", () => {
      setupWithData();
      renderTab();
      // established_rules (18) + proven_rules (7) = 25
      const quartet = screen.getByTestId("kpi-quartet");
      expect(quartet.textContent).toContain("+25 active");
    });
  });

  describe("Recent writes panel", () => {
    it("renders recent writes card", () => {
      setupWithData();
      renderTab();
      expect(screen.getByTestId("recent-writes-card")).toBeDefined();
    });

    it("renders all episode rows", () => {
      setupWithData();
      renderTab();
      const rows = screen.getAllByTestId("recent-write-row");
      expect(rows.length).toBe(3);
    });

    it("renders episode content", () => {
      setupWithData();
      renderTab();
      const list = screen.getByTestId("recent-writes-list");
      expect(list.textContent).toContain("User asked about travel plans to Japan.");
      expect(list.textContent).toContain("Discussed upcoming health check appointment.");
      expect(list.textContent).toContain("Preference noted: prefers concise summaries.");
    });

    it("renders episode timestamps via <Time>", () => {
      setupWithData();
      renderTab();
      const timeEls = screen.getAllByRole("time");
      // Three episodes = three time elements
      expect(timeEls.length).toBeGreaterThanOrEqual(3);
      expect(timeEls[0].getAttribute("dateTime")).toBe(MIN5_AGO);
      expect(timeEls[1].getAttribute("dateTime")).toBe(MIN10_AGO);
      expect(timeEls[2].getAttribute("dateTime")).toBe(MIN20_AGO);
    });

    it("renders episodes in order returned by the hook (newest first)", () => {
      setupWithData();
      renderTab();
      const rows = screen.getAllByTestId("recent-write-row");
      // First row should contain content of first episode
      expect(rows[0].textContent).toContain("User asked about travel plans to Japan.");
      expect(rows[1].textContent).toContain("Discussed upcoming health check appointment.");
      expect(rows[2].textContent).toContain("Preference noted: prefers concise summaries.");
    });
  });

  describe("Empty state — no episodes", () => {
    it("shows empty-state message when there are no recent writes", () => {
      setupEmpty();
      renderTab();
      const emptyLine = screen.getByTestId("empty-state-line");
      expect(emptyLine).toBeDefined();
      expect(emptyLine.textContent).toContain("No memory writes recorded yet.");
    });

    it("shows zero counts in KPIs", () => {
      setupEmpty();
      renderTab();
      // All four KPI cells should show 0
      const kpiItems = screen.getAllByTestId("kpi-item");
      expect(kpiItems.length).toBe(4);
      kpiItems.forEach((item) => {
        expect(item.textContent).toContain("0");
      });
    });
  });

  describe("isError state", () => {
    it("shows ErrorLine in KPI panel when stats fail", () => {
      setupError();
      renderTab();
      const errorLines = screen.getAllByTestId("error-state-line");
      // At minimum one ErrorLine from KPI
      expect(errorLines.length).toBeGreaterThanOrEqual(1);
    });

    it("error message mentions memory stats failure", () => {
      setupError();
      renderTab();
      const errorLines = screen.getAllByTestId("error-state-line");
      const hasStatsError = errorLines.some((el) =>
        el.textContent?.includes("Could not load memory stats"),
      );
      expect(hasStatsError).toBe(true);
    });

    it("shows ErrorLine for recent writes when writes query fails", () => {
      // Only make recent writes fail
      vi.mocked(useMemoryStats).mockReturnValue({
        data: { data: MEMORY_STATS },
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useMemoryStats>);

      vi.mocked(useEntities).mockReturnValue({
        data: ENTITIES_RESPONSE,
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useEntities>);

      vi.mocked(useMemoryRecentWrites).mockReturnValue({
        data: undefined,
        isLoading: false,
        isError: true,
      } as unknown as ReturnType<typeof useMemoryRecentWrites>);

      renderTab();
      const errorLines = screen.getAllByTestId("error-state-line");
      const hasWritesError = errorLines.some((el) =>
        el.textContent?.includes("Could not load recent writes"),
      );
      expect(hasWritesError).toBe(true);
    });
  });

  describe("Loading state", () => {
    it("shows loading skeletons in KPI quartet", () => {
      setupLoading();
      renderTab();
      const loadingLines = screen.getAllByTestId("loading-line");
      expect(loadingLines.length).toBeGreaterThanOrEqual(4);
    });

    it("does not render recent-write rows while loading", () => {
      setupLoading();
      renderTab();
      const rows = screen.queryAllByTestId("recent-write-row");
      expect(rows.length).toBe(0);
    });
  });
});
