// @vitest-environment jsdom
/**
 * ButlerLifestyleTasteTab — RTL tests (bu-2jtfw.10).
 *
 * Replaces the bu-iuol4.33/bu-h7q85 suite: the tab now reads the taste
 * ledger (works/taste_signals/verdicts) instead of subject="user" facts.
 *
 * Tests cover:
 *  - Root container + panel presence
 *  - KPI strip renders totals from meta.total / summary counts, not page length
 *  - Ledger-degraded note appears when ledger_available=false
 *  - Taste verdicts chips render from the verdicts list
 *  - Recent works list renders from the works list
 *  - Empty states for each panel
 *  - Loading state shows skeletons, no empty-state text
 *  - Error banner + per-panel error lines when a query fails
 *
 * bead: bu-2jtfw.10
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerLifestyleTasteTab from "./ButlerLifestyleTasteTab";

vi.mock("@/hooks/use-memory", () => ({
  useLifestyleTasteSummary: vi.fn(),
  useLifestyleTasteVerdicts: vi.fn(),
  useLifestyleTasteWorks: vi.fn(),
}));

vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => <time dateTime={value}>{value}</time>,
}));

import {
  useLifestyleTasteSummary,
  useLifestyleTasteVerdicts,
  useLifestyleTasteWorks,
} from "@/hooks/use-memory";

// ---------------------------------------------------------------------------
// Fixture data
// ---------------------------------------------------------------------------

const SUMMARY_FIXTURE = {
  total_works: 220,
  total_signals: 340,
  total_verdicts: 61,
  recent_signals_7d: 12,
  works_by_kind: { track: 220 },
  signals_by_kind: { listen_completed: 200, listen_skipped: 140 },
  ledger_available: true,
};

const VERDICTS_FIXTURE = [
  { id: "v1", work_id: null, predicate: "likes_genre", verdict_text: "loves jazz", source: "legacy_fact", created_at: "2026-08-01T00:00:00Z" },
  { id: "v2", work_id: null, predicate: "likes_cuisine", verdict_text: "Japanese", source: "legacy_fact", created_at: "2026-08-01T00:00:00Z" },
];

const WORKS_FIXTURE = [
  { id: "w1", kind: "track", title: "Song A", external_ids: {}, created_at: "2026-09-01T00:00:00Z" },
  { id: "w2", kind: "track", title: "Song B", external_ids: {}, created_at: "2026-09-01T00:00:00Z" },
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

function setupWithData({
  summary = SUMMARY_FIXTURE,
  verdicts = VERDICTS_FIXTURE,
  verdictsTotal = 61,
  works = WORKS_FIXTURE,
  worksTotal = 250,
} = {}) {
  vi.mocked(useLifestyleTasteSummary).mockReturnValue({
    data: summary,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useLifestyleTasteSummary>);

  vi.mocked(useLifestyleTasteVerdicts).mockReturnValue({
    data: { data: verdicts, meta: { total: verdictsTotal, offset: 0, limit: 20, has_more: false } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useLifestyleTasteVerdicts>);

  vi.mocked(useLifestyleTasteWorks).mockReturnValue({
    data: { data: works, meta: { total: worksTotal, offset: 0, limit: 10, has_more: true } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useLifestyleTasteWorks>);
}

function setupEmpty() {
  setupWithData({
    summary: { ...SUMMARY_FIXTURE, total_works: 0, total_verdicts: 0, recent_signals_7d: 0 },
    verdicts: [],
    verdictsTotal: 0,
    works: [],
    worksTotal: 0,
  });
}

function setupLoading() {
  vi.mocked(useLifestyleTasteSummary).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useLifestyleTasteSummary>);
  vi.mocked(useLifestyleTasteVerdicts).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useLifestyleTasteVerdicts>);
  vi.mocked(useLifestyleTasteWorks).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useLifestyleTasteWorks>);
}

function setupError() {
  vi.mocked(useLifestyleTasteSummary).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useLifestyleTasteSummary>);
  vi.mocked(useLifestyleTasteVerdicts).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useLifestyleTasteVerdicts>);
  vi.mocked(useLifestyleTasteWorks).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useLifestyleTasteWorks>);
}

// ---------------------------------------------------------------------------
// Tests: Root container + panel presence
// ---------------------------------------------------------------------------

describe("ButlerLifestyleTasteTab — panels present", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the root tab container", () => {
    renderTab();
    expect(screen.getByTestId("lifestyle-taste-tab")).toBeDefined();
  });

  it("renders the KPI strip, taste verdicts, and recent works cards", () => {
    renderTab();
    expect(screen.getByTestId("kpi-strip")).toBeDefined();
    expect(screen.getByTestId("taste-summary-card")).toBeDefined();
    expect(screen.getByTestId("recent-works-card")).toBeDefined();
  });

  it("no longer renders a weekly digest archive panel", () => {
    renderTab();
    expect(screen.queryByTestId("digest-archive-card")).toBeNull();
    expect(screen.queryByText("No weekly digests yet.")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: KPI totals — the literal bug this bead fixes
// ---------------------------------------------------------------------------

describe("ButlerLifestyleTasteTab — KPI totals render from meta.total", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });
  afterEach(() => cleanup());

  it("shows total_works from the summary endpoint, not a fetched page length", () => {
    // Only 2 works are on the fetched page, but the ledger has 220.
    setupWithData();
    renderTab();
    const kpiItems = screen.getAllByTestId("kpi-item");
    expect(kpiItems[0].textContent).toContain("220");
    expect(WORKS_FIXTURE.length).toBe(2); // sanity: the page really is smaller than the total
  });

  it("shows total verdicts from meta.total, exceeding the fetched page length", () => {
    setupWithData({ verdicts: VERDICTS_FIXTURE.slice(0, 1), verdictsTotal: 61 });
    renderTab();
    const kpiItems = screen.getAllByTestId("kpi-item");
    // Fetched page has 1 verdict; the real total (61) must render, not 1.
    expect(kpiItems[1].textContent).toContain("61");
  });

  it("shows recent_signals_7d from the summary", () => {
    setupWithData();
    renderTab();
    const kpiItems = screen.getAllByTestId("kpi-item");
    expect(kpiItems[2].textContent).toContain("12");
  });

  it("shows a degraded note when ledger_available is false", () => {
    setupWithData({ summary: { ...SUMMARY_FIXTURE, ledger_available: false } });
    renderTab();
    expect(screen.getByTestId("ledger-degraded-note")).toBeDefined();
  });

  it("does not show a degraded note when the ledger is healthy", () => {
    setupWithData();
    renderTab();
    expect(screen.queryByTestId("ledger-degraded-note")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Taste verdicts panel
// ---------------------------------------------------------------------------

describe("ButlerLifestyleTasteTab — taste verdicts panel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders a chip for each verdict", () => {
    renderTab();
    const chips = screen.getAllByTestId("taste-chip");
    expect(chips.length).toBe(2);
  });

  it("renders verdict text within the chips container", () => {
    renderTab();
    const chipsContainer = screen.getByTestId("taste-chips");
    expect(chipsContainer.textContent).toContain("loves jazz");
    expect(chipsContainer.textContent).toContain("Japanese");
  });
});

// ---------------------------------------------------------------------------
// Tests: Recent works panel
// ---------------------------------------------------------------------------

describe("ButlerLifestyleTasteTab — recent works panel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the recent works list", () => {
    renderTab();
    expect(screen.getByTestId("recent-works-list")).toBeDefined();
  });

  it("renders an item per fetched work", () => {
    renderTab();
    const items = screen.getAllByTestId("recent-work-item");
    expect(items.length).toBe(2);
  });

  it("renders work titles within the list", () => {
    renderTab();
    const list = screen.getByTestId("recent-works-list");
    expect(list.textContent).toContain("Song A");
    expect(list.textContent).toContain("Song B");
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

  it("shows empty state message for taste verdicts", () => {
    renderTab();
    expect(screen.getByText("No taste verdicts recorded yet.")).toBeDefined();
  });

  it("shows empty state message for recent works", () => {
    renderTab();
    expect(screen.getByText("No works recorded yet.")).toBeDefined();
  });

  it("shows zeroed KPI values", () => {
    renderTab();
    const kpiItems = screen.getAllByTestId("kpi-item");
    expect(kpiItems[0].textContent).toContain("0");
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
    expect(screen.queryByText("No taste verdicts recorded yet.")).toBeNull();
    expect(screen.queryByText("No works recorded yet.")).toBeNull();
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

  it("shows error lines in each panel when all queries fail", () => {
    vi.resetAllMocks();
    setupError();
    renderTab();
    const errorLines = screen.getAllByTestId("error-state-line");
    // KPI strip + taste verdicts + recent works each render an error line.
    expect(errorLines.length).toBeGreaterThanOrEqual(3);
  });
});
