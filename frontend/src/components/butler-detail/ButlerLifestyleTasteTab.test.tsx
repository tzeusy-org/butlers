// @vitest-environment jsdom
/**
 * ButlerLifestyleTasteTab — RTL tests.
 *
 * Tests cover:
 *  - Root container renders
 *  - All 4 panels render (KPI strip, taste summary, consumption state, recent additions, digest archive)
 *  - KPI rendering: active preferences count, currently consuming count, recently logged count
 *  - Taste summary chips render from likes_* predicate facts
 *  - Consumption state items render from watches/reads/plays predicate facts
 *  - Recent additions list renders last 10 facts (sorted by created_at desc)
 *  - Empty digest archive renders stub message
 *  - Empty state for each panel when no data
 *  - Loading state shows skeletons, no empty-state text
 *  - Error banner appears when any query fails
 *  - Error state shows error line in individual panels
 *
 * bead: bu-iuol4.33
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
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerLifestyleTasteTab from "./ButlerLifestyleTasteTab";

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-memory", () => ({
  useMemoryRecall: vi.fn(),
  useMemorySearch: vi.fn(),
}));

// Stub <Time> to avoid date-formatting complexity
vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => (
    <time dateTime={value}>{value}</time>
  ),
}));

import { useMemoryRecall, useMemorySearch } from "@/hooks/use-memory";

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
const H1_AGO = new Date(NOW - 1 * 60 * 60 * 1000).toISOString();
const D2_AGO = new Date(NOW - 2 * 24 * 60 * 60 * 1000).toISOString();
const D10_AGO = new Date(NOW - 10 * 24 * 60 * 60 * 1000).toISOString();

const BASE_FACT = {
  importance: 0.8,
  confidence: 0.9,
  decay_rate: 0.01,
  permanence: "permanent",
  source_butler: "lifestyle",
  source_episode_id: null,
  session_id: null,
  supersedes_id: null,
  entity_id: null,
  entity_name: null,
  object_entity_id: null,
  object_entity_name: null,
  validity: "active",
  scope: "lifestyle",
  reference_count: 1,
};

const PREFERENCE_FACTS = [
  {
    ...BASE_FACT,
    id: "fact-pref-001",
    subject: "user",
    predicate: "likes_genre",
    content: "jazz",
    created_at: D2_AGO,
    updated_at: D2_AGO,
  },
  {
    ...BASE_FACT,
    id: "fact-pref-002",
    subject: "user",
    predicate: "likes_cuisine",
    content: "Japanese",
    created_at: D2_AGO,
    updated_at: D2_AGO,
  },
  {
    ...BASE_FACT,
    id: "fact-pref-003",
    subject: "user",
    predicate: "likes_artist",
    content: "Miles Davis",
    created_at: H1_AGO,
    updated_at: H1_AGO,
  },
];

const CONSUMPTION_FACTS = [
  {
    ...BASE_FACT,
    id: "fact-cons-001",
    subject: "user",
    predicate: "watches",
    content: "Succession",
    created_at: D2_AGO,
    updated_at: D2_AGO,
  },
  {
    ...BASE_FACT,
    id: "fact-cons-002",
    subject: "user",
    predicate: "reads",
    content: "The Brothers Karamazov",
    created_at: D2_AGO,
    updated_at: D2_AGO,
  },
  {
    ...BASE_FACT,
    id: "fact-cons-003",
    subject: "user",
    predicate: "plays",
    content: "Elden Ring",
    created_at: H1_AGO,
    updated_at: H1_AGO,
  },
];

// All facts for recall (preference + consumption, with recent fact within 7d)
const ALL_RECALL_FACTS = [
  ...PREFERENCE_FACTS,
  ...CONSUMPTION_FACTS,
  {
    ...BASE_FACT,
    id: "fact-other-001",
    subject: "user",
    predicate: "prefers_music_format",
    content: "vinyl",
    created_at: D10_AGO, // older than 7d
    updated_at: D10_AGO,
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
        <ButlerLifestyleTasteTab />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Default mock setups
// ---------------------------------------------------------------------------

function setupWithData() {
  vi.mocked(useMemoryRecall).mockReturnValue({
    data: {
      data: ALL_RECALL_FACTS,
      meta: { total: ALL_RECALL_FACTS.length, has_more: false, offset: 0, limit: 100 },
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useMemoryRecall>);

  vi.mocked(useMemorySearch).mockImplementation(({ predicates }) => {
    const matchPref = predicates.some((p) => p.startsWith("likes_"));
    const matchCons = predicates.some((p) => ["watches", "reads", "plays"].includes(p));
    const facts = matchPref
      ? PREFERENCE_FACTS
      : matchCons
        ? CONSUMPTION_FACTS
        : [];
    return {
      data: {
        data: ALL_RECALL_FACTS,
        meta: { total: ALL_RECALL_FACTS.length, has_more: false, offset: 0, limit: 200 },
      },
      isLoading: false,
      isError: false,
      facts,
    } as unknown as ReturnType<typeof useMemorySearch>;
  });
}

function setupEmpty() {
  vi.mocked(useMemoryRecall).mockReturnValue({
    data: { data: [], meta: { total: 0, has_more: false, offset: 0, limit: 100 } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useMemoryRecall>);

  vi.mocked(useMemorySearch).mockReturnValue({
    data: { data: [], meta: { total: 0, has_more: false, offset: 0, limit: 200 } },
    isLoading: false,
    isError: false,
    facts: [],
  } as unknown as ReturnType<typeof useMemorySearch>);
}

function setupLoading() {
  vi.mocked(useMemoryRecall).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useMemoryRecall>);

  vi.mocked(useMemorySearch).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
    facts: [],
  } as unknown as ReturnType<typeof useMemorySearch>);
}

function setupError() {
  vi.mocked(useMemoryRecall).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useMemoryRecall>);

  vi.mocked(useMemorySearch).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
    facts: [],
  } as unknown as ReturnType<typeof useMemorySearch>);
}

// ---------------------------------------------------------------------------
// Tests: Root container + panel presence
// ---------------------------------------------------------------------------

describe("ButlerLifestyleTasteTab — all panels present", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the root tab container", () => {
    renderTab();
    expect(screen.getByTestId("lifestyle-taste-tab")).toBeDefined();
  });

  it("renders the KPI strip panel", () => {
    renderTab();
    expect(screen.getByTestId("kpi-strip")).toBeDefined();
  });

  it("renders the taste summary card", () => {
    renderTab();
    expect(screen.getByTestId("taste-summary-card")).toBeDefined();
  });

  it("renders the consumption state card", () => {
    renderTab();
    expect(screen.getByTestId("consumption-state-card")).toBeDefined();
  });

  it("renders the recent additions card", () => {
    renderTab();
    expect(screen.getByTestId("recent-additions-card")).toBeDefined();
  });

  it("renders the digest archive card", () => {
    renderTab();
    expect(screen.getByTestId("digest-archive-card")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: KPI rendering
// ---------------------------------------------------------------------------

describe("ButlerLifestyleTasteTab — KPI rendering", () => {
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

  it("shows active preferences count from likes_* facts", () => {
    renderTab();
    // 3 preference facts — use KPI items to scope text search
    const kpiItems = screen.getAllByTestId("kpi-item");
    expect(kpiItems[0].textContent).toContain("3");
  });

  it("shows consumption count from watches/reads/plays facts", () => {
    renderTab();
    // 3 consumption facts — value shows as "3" in the second KPI item
    expect(screen.getByText("Active preferences")).toBeDefined();
    expect(screen.getByText("Currently consuming")).toBeDefined();
    expect(screen.getByText("Recently logged")).toBeDefined();
    expect(screen.getByText("Weekly digest")).toBeDefined();
    const kpiItems = screen.getAllByTestId("kpi-item");
    expect(kpiItems[1].textContent).toContain("3");
  });

  it("shows recently logged count (facts within 7 days)", () => {
    renderTab();
    // ALL_RECALL_FACTS has 7 facts: 3 prefs (D2_AGO/H1_AGO) + 3 consumption (D2_AGO/H1_AGO) +
    // 1 other (D10_AGO, older than 7d). So 6 facts fall within the 7d window.
    // The recently-logged KPI is driven by allFacts (recallData), which is ALL_RECALL_FACTS.
    const kpiItems = screen.getAllByTestId("kpi-item");
    expect(kpiItems[2].textContent).toContain("6");
  });

  it("shows dash for weekly digest when no digests exist", () => {
    renderTab();
    expect(screen.getByText("—")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Taste summary chips
// ---------------------------------------------------------------------------

describe("ButlerLifestyleTasteTab — taste summary chips", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders taste chip container", () => {
    renderTab();
    expect(screen.getByTestId("taste-chips")).toBeDefined();
  });

  it("renders a chip for each preference fact", () => {
    renderTab();
    const chips = screen.getAllByTestId("taste-chip");
    expect(chips.length).toBe(3);
  });

  it("renders 'jazz' chip within taste chips container", () => {
    renderTab();
    const chipsContainer = screen.getByTestId("taste-chips");
    expect(chipsContainer.textContent).toContain("jazz");
  });

  it("renders 'Japanese' chip within taste chips container", () => {
    renderTab();
    const chipsContainer = screen.getByTestId("taste-chips");
    expect(chipsContainer.textContent).toContain("Japanese");
  });

  it("renders 'Miles Davis' chip within taste chips container", () => {
    renderTab();
    const chipsContainer = screen.getByTestId("taste-chips");
    expect(chipsContainer.textContent).toContain("Miles Davis");
  });
});

// ---------------------------------------------------------------------------
// Tests: Consumption state
// ---------------------------------------------------------------------------

describe("ButlerLifestyleTasteTab — consumption state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders consumption list", () => {
    renderTab();
    expect(screen.getByTestId("consumption-list")).toBeDefined();
  });

  it("renders 3 consumption items", () => {
    renderTab();
    const items = screen.getAllByTestId("consumption-item");
    expect(items.length).toBe(3);
  });

  it("renders 'Succession' with 'watching' label within consumption list", () => {
    renderTab();
    const list = screen.getByTestId("consumption-list");
    expect(list.textContent).toContain("Succession");
    expect(list.textContent).toContain("watching");
  });

  it("renders 'The Brothers Karamazov' with 'reading' label within consumption list", () => {
    renderTab();
    const list = screen.getByTestId("consumption-list");
    expect(list.textContent).toContain("The Brothers Karamazov");
    expect(list.textContent).toContain("reading");
  });

  it("renders 'Elden Ring' with 'playing' label within consumption list", () => {
    renderTab();
    const list = screen.getByTestId("consumption-list");
    expect(list.textContent).toContain("Elden Ring");
    expect(list.textContent).toContain("playing");
  });
});

// ---------------------------------------------------------------------------
// Tests: Recent additions
// ---------------------------------------------------------------------------

describe("ButlerLifestyleTasteTab — recent additions", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders recent additions list", () => {
    renderTab();
    expect(screen.getByTestId("recent-additions-list")).toBeDefined();
  });

  it("renders up to 10 items", () => {
    renderTab();
    const items = screen.getAllByTestId("recent-addition-item");
    expect(items.length).toBeLessThanOrEqual(10);
    expect(items.length).toBeGreaterThanOrEqual(1);
  });

  it("renders all 7 facts from ALL_RECALL_FACTS (fewer than 10)", () => {
    renderTab();
    const items = screen.getAllByTestId("recent-addition-item");
    expect(items.length).toBe(ALL_RECALL_FACTS.length);
  });
});

// ---------------------------------------------------------------------------
// Tests: Weekly digest archive (stub)
// ---------------------------------------------------------------------------

describe("ButlerLifestyleTasteTab — weekly digest archive stub", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the empty-state digest message", () => {
    renderTab();
    expect(screen.getByTestId("digest-empty-state")).toBeDefined();
  });

  it("shows 'No weekly digests yet.' text", () => {
    renderTab();
    expect(screen.getByText("No weekly digests yet.")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Empty state
// ---------------------------------------------------------------------------

describe("ButlerLifestyleTasteTab — empty state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupEmpty();
  });
  afterEach(() => cleanup());

  it("shows empty state message for taste summary", () => {
    renderTab();
    expect(screen.getByText("No taste preferences recorded yet.")).toBeDefined();
  });

  it("shows empty state message for consumption state", () => {
    renderTab();
    expect(screen.getByText("No active consumption tracked.")).toBeDefined();
  });

  it("shows empty state message for recent additions", () => {
    renderTab();
    expect(screen.getByText("No facts logged yet.")).toBeDefined();
  });

  it("still renders digest stub in empty state", () => {
    renderTab();
    expect(screen.getByText("No weekly digests yet.")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Loading state
// ---------------------------------------------------------------------------

describe("ButlerLifestyleTasteTab — loading state", () => {
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

  it("does not show empty-state text while loading", () => {
    renderTab();
    expect(screen.queryByText("No taste preferences recorded yet.")).toBeNull();
    expect(screen.queryByText("No active consumption tracked.")).toBeNull();
    expect(screen.queryByText("No facts logged yet.")).toBeNull();
  });

  it("does not show error banner while loading", () => {
    renderTab();
    expect(screen.queryByTestId("taste-load-error")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Error state
// ---------------------------------------------------------------------------

describe("ButlerLifestyleTasteTab — error state", () => {
  afterEach(() => cleanup());

  it("shows error banner when queries fail", () => {
    vi.resetAllMocks();
    setupError();
    renderTab();
    expect(screen.getByTestId("taste-load-error")).toBeDefined();
  });

  it("shows error line in KPI strip when all queries fail", () => {
    vi.resetAllMocks();
    setupError();
    renderTab();
    const errorLines = screen.getAllByTestId("error-state-line");
    expect(errorLines.length).toBeGreaterThanOrEqual(1);
  });

  it("shows error lines in taste summary and consumption panels when search fails", () => {
    vi.resetAllMocks();
    // Recall succeeds but search fails
    vi.mocked(useMemoryRecall).mockReturnValue({
      data: { data: ALL_RECALL_FACTS, meta: { total: 7, has_more: false, offset: 0, limit: 100 } },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMemoryRecall>);
    vi.mocked(useMemorySearch).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      facts: [],
    } as unknown as ReturnType<typeof useMemorySearch>);
    renderTab();
    const errorLines = screen.getAllByTestId("error-state-line");
    expect(errorLines.length).toBeGreaterThanOrEqual(2); // taste + consumption
  });
});
