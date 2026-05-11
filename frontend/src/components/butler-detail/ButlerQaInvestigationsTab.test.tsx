// @vitest-environment jsdom
/**
 * ButlerQaInvestigationsTab — RTL tests.
 *
 * Tests cover:
 *  - All four layout sections render (KPI quartet, patrol cadence, investigations
 *    table, selected detail panel)
 *  - KPI values surface correctly from aggregated data
 *  - Severity badges render for high / medium / low severities
 *  - Link targets point to /qa/investigations/{id}
 *  - Empty state when no investigations and no patrols
 *  - Loading skeletons appear during loading; empty-state text does not
 *  - Selecting a row opens the inline detail panel
 *  - Detail panel emits a link to /qa/investigations/{id}
 *  - Circuit breaker chip reflects tripped / closed state
 *
 * bead: bu-iuol4.28
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
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerQaInvestigationsTab from "./ButlerQaInvestigationsTab";

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-qa", () => ({
  useQaSummary: vi.fn(),
  useQaInvestigations: vi.fn(),
  useQaPatrols: vi.fn(),
  useQaCircuitBreaker: vi.fn(),
}));

// Stub <Time> to avoid date-formatting complexity in unit tests
vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => (
    <time dateTime={value}>{value}</time>
  ),
}));

import { useQaSummary, useQaInvestigations, useQaPatrols, useQaCircuitBreaker } from "@/hooks/use-qa";

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
const H1_AGO = new Date(NOW - 60 * 60 * 1000).toISOString();
const H3_AGO = new Date(NOW - 3 * 60 * 60 * 1000).toISOString();
const H23_AGO = new Date(NOW - 23 * 60 * 60 * 1000).toISOString();

const INVESTIGATIONS_DATA = [
  {
    id: "inv-aabbccdd-1111",
    fingerprint: "fp-001",
    butler_name: "chronicler",
    status: "investigating",
    severity: 1, // high
    exception_type: "AttributeError",
    call_site: "chronicler/tools.py:42",
    sanitized_msg: "NoneType has no attribute 'encode'",
    pr_url: null,
    pr_number: null,
    created_at: H3_AGO,
    updated_at: H3_AGO,
    closed_at: null,
  },
  {
    id: "inv-bbccddee-2222",
    fingerprint: "fp-002",
    butler_name: "finance",
    status: "pr_open",
    severity: 2, // medium
    exception_type: "KeyError",
    call_site: "finance/module.py:99",
    sanitized_msg: null,
    pr_url: "https://github.com/org/repo/pull/42",
    pr_number: 42,
    created_at: H3_AGO,
    updated_at: H1_AGO,
    closed_at: null,
  },
  {
    id: "inv-ccddeeff-3333",
    fingerprint: "fp-003",
    butler_name: "qa",
    status: "pr_merged",
    severity: 3, // low
    exception_type: "TimeoutError",
    call_site: "qa/patrol.py:12",
    sanitized_msg: "Connection timed out",
    pr_url: "https://github.com/org/repo/pull/41",
    pr_number: 41,
    created_at: H23_AGO,
    updated_at: H1_AGO,
    closed_at: H1_AGO,
  },
];

const PATROLS_DATA = [
  {
    id: "patrol-aabb-0001",
    started_at: H3_AGO,
    completed_at: new Date(NOW - 2.5 * 60 * 60 * 1000).toISOString(),
    status: "clean",
    findings_count: 0,
    novel_count: 0,
    dispatched_count: 0,
    log_lookback_minutes: 60,
    sources_polled: ["log_scanner"],
    error_detail: null,
  },
  {
    id: "patrol-bbcc-0002",
    started_at: H23_AGO,
    completed_at: new Date(NOW - 22.5 * 60 * 60 * 1000).toISOString(),
    status: "findings_dispatched",
    findings_count: 2,
    novel_count: 1,
    dispatched_count: 1,
    log_lookback_minutes: 60,
    sources_polled: ["log_scanner", "session_records"],
    error_detail: null,
  },
];

const SUMMARY_DATA = {
  last_patrol: PATROLS_DATA[0],
  stats_24h: {
    patrols_completed: 3,
    total_findings: 4,
    novel_findings: 2,
    dispatched_investigations: 1,
  },
  stats_all_time: {
    total_patrols: 100,
    total_findings: 200,
    novel_findings: 50,
    dispatched_investigations: 20,
  },
  active_sources: ["log_scanner"],
};

const CIRCUIT_BREAKER_CLOSED = {
  tripped: false,
  threshold: 5,
  recent_statuses: [],
  recent_attempts: [],
};

const CIRCUIT_BREAKER_TRIPPED = {
  tripped: true,
  threshold: 5,
  recent_statuses: ["failed", "failed", "failed", "failed", "failed"],
  recent_attempts: [],
};

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
        <ButlerQaInvestigationsTab />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Default mock setup: all data loaded
// ---------------------------------------------------------------------------

function setupWithData() {
  vi.mocked(useQaSummary).mockReturnValue({
    data: { data: SUMMARY_DATA },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useQaSummary>);

  vi.mocked(useQaInvestigations).mockReturnValue({
    data: { data: INVESTIGATIONS_DATA, meta: { total: 3, has_more: false, offset: 0, limit: 50 } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useQaInvestigations>);

  vi.mocked(useQaPatrols).mockReturnValue({
    data: { data: PATROLS_DATA, meta: { total: 2, has_more: false, offset: 0, limit: 24 } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useQaPatrols>);

  vi.mocked(useQaCircuitBreaker).mockReturnValue({
    data: { data: CIRCUIT_BREAKER_CLOSED },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useQaCircuitBreaker>);
}

function setupEmpty() {
  vi.mocked(useQaSummary).mockReturnValue({
    data: {
      data: {
        last_patrol: null,
        stats_24h: {
          patrols_completed: 0,
          total_findings: 0,
          novel_findings: 0,
          dispatched_investigations: 0,
        },
        stats_all_time: {
          total_patrols: 0,
          total_findings: 0,
          novel_findings: 0,
          dispatched_investigations: 0,
        },
        active_sources: [],
      },
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useQaSummary>);

  vi.mocked(useQaInvestigations).mockReturnValue({
    data: { data: [], meta: { total: 0, has_more: false, offset: 0, limit: 50 } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useQaInvestigations>);

  vi.mocked(useQaPatrols).mockReturnValue({
    data: { data: [], meta: { total: 0, has_more: false, offset: 0, limit: 24 } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useQaPatrols>);

  vi.mocked(useQaCircuitBreaker).mockReturnValue({
    data: { data: CIRCUIT_BREAKER_CLOSED },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useQaCircuitBreaker>);
}

function setupLoading() {
  vi.mocked(useQaSummary).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useQaSummary>);

  vi.mocked(useQaInvestigations).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useQaInvestigations>);

  vi.mocked(useQaPatrols).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useQaPatrols>);

  vi.mocked(useQaCircuitBreaker).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useQaCircuitBreaker>);
}

function setupTrippedCircuitBreaker() {
  setupWithData();
  vi.mocked(useQaCircuitBreaker).mockReturnValue({
    data: { data: CIRCUIT_BREAKER_TRIPPED },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useQaCircuitBreaker>);
}

// ---------------------------------------------------------------------------
// Tests: Root container + section presence
// ---------------------------------------------------------------------------

describe("ButlerQaInvestigationsTab — all sections present", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the root tab container", () => {
    renderTab();
    expect(screen.getByTestId("qa-investigations-tab")).toBeDefined();
  });

  it("renders the KPI quartet", () => {
    renderTab();
    expect(screen.getByTestId("qa-kpi-quartet")).toBeDefined();
  });

  it("renders the patrol cadence stripe", () => {
    renderTab();
    expect(screen.getByTestId("patrol-cadence-stripe")).toBeDefined();
  });

  it("renders the recent investigations table card", () => {
    renderTab();
    expect(screen.getByTestId("recent-investigations-card")).toBeDefined();
  });

  it("renders the investigations table body", () => {
    renderTab();
    expect(screen.getByTestId("investigations-table")).toBeDefined();
  });

  it("renders patrol rows in the cadence stripe", () => {
    renderTab();
    const rows = screen.getAllByTestId("patrol-stripe-row");
    expect(rows.length).toBeGreaterThanOrEqual(1);
  });

  it("renders investigation rows in the table", () => {
    renderTab();
    const rows = screen.getAllByTestId("investigation-row");
    expect(rows.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: KPI values
// ---------------------------------------------------------------------------

describe("ButlerQaInvestigationsTab — KPI values", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("shows open investigation count (2 open: investigating + pr_open)", () => {
    renderTab();
    const openKpi = screen.getByTestId("kpi-open");
    expect(openKpi.textContent).toBe("2");
  });

  it("shows patrols in 24h from summary stats", () => {
    renderTab();
    const patrolsKpi = screen.getByTestId("kpi-patrols-24h");
    expect(patrolsKpi.textContent).toBe("3");
  });

  it("shows closed in 24h count from merged/closed items", () => {
    renderTab();
    const closedKpi = screen.getByTestId("kpi-closed-24h");
    // pr_merged with closed_at 1h ago (within 24h) = 1
    expect(closedKpi.textContent).toBe("1");
  });

  it("shows MTTR when items were closed within 24h", () => {
    renderTab();
    const mttrKpi = screen.getByTestId("kpi-mttr");
    // Should not be "—" since one item was closed within 24h
    expect(mttrKpi.textContent).not.toBe("—");
    expect(mttrKpi.textContent).not.toBe("…");
  });
});

// ---------------------------------------------------------------------------
// Tests: Severity badges
// ---------------------------------------------------------------------------

describe("ButlerQaInvestigationsTab — severity badges", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders severity badges for all investigation rows", () => {
    renderTab();
    const badges = screen.getAllByTestId("severity-badge");
    expect(badges.length).toBeGreaterThanOrEqual(3);
  });

  it("renders 'high' severity badge for severity=1", () => {
    renderTab();
    const badges = screen.getAllByTestId("severity-badge");
    const labels = badges.map((b) => b.textContent ?? "");
    expect(labels).toContain("high");
  });

  it("renders 'medium' severity badge for severity=2", () => {
    renderTab();
    const badges = screen.getAllByTestId("severity-badge");
    const labels = badges.map((b) => b.textContent ?? "");
    expect(labels).toContain("medium");
  });

  it("renders 'low' severity badge for severity=3", () => {
    renderTab();
    const badges = screen.getAllByTestId("severity-badge");
    const labels = badges.map((b) => b.textContent ?? "");
    expect(labels).toContain("low");
  });
});

// ---------------------------------------------------------------------------
// Tests: Link targets to /qa/investigations/{id}
// ---------------------------------------------------------------------------

describe("ButlerQaInvestigationsTab — link targets", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("does not render the detail panel before a row is selected", () => {
    renderTab();
    expect(screen.queryByTestId("investigation-detail-panel")).toBeNull();
  });

  it("opens the inline detail panel when a row is clicked", () => {
    renderTab();
    const rows = screen.getAllByTestId("investigation-row");
    fireEvent.click(rows[0]);
    expect(screen.getByTestId("investigation-detail-panel")).toBeDefined();
  });

  it("detail panel contains a link to /qa/investigations/{id}", () => {
    renderTab();
    const rows = screen.getAllByTestId("investigation-row");
    fireEvent.click(rows[0]);

    const link = screen.getByTestId("investigation-detail-link") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toContain("/qa/investigations/");
    expect(link.getAttribute("href")).toContain("inv-aabbccdd-1111");
  });

  it("closes the detail panel when the same row is clicked again", () => {
    renderTab();
    const rows = screen.getAllByTestId("investigation-row");
    fireEvent.click(rows[0]);
    expect(screen.getByTestId("investigation-detail-panel")).toBeDefined();
    fireEvent.click(rows[0]);
    expect(screen.queryByTestId("investigation-detail-panel")).toBeNull();
  });

  it("closes the detail panel when the Close button is clicked", () => {
    renderTab();
    const rows = screen.getAllByTestId("investigation-row");
    fireEvent.click(rows[0]);
    const closeBtn = screen.getByRole("button", { name: /close/i });
    fireEvent.click(closeBtn);
    expect(screen.queryByTestId("investigation-detail-panel")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Empty state
// ---------------------------------------------------------------------------

describe("ButlerQaInvestigationsTab — empty state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupEmpty();
  });
  afterEach(() => cleanup());

  it("shows empty state for investigations", () => {
    renderTab();
    expect(screen.getByText("No investigations found.")).toBeDefined();
  });

  it("shows empty state for patrol stripe", () => {
    renderTab();
    expect(
      screen.getByText("No patrols recorded in the last 24 hours."),
    ).toBeDefined();
  });

  it("does not render detail panel in empty state", () => {
    renderTab();
    expect(screen.queryByTestId("investigation-detail-panel")).toBeNull();
  });

  it("shows 0 for open investigation KPI", () => {
    renderTab();
    const openKpi = screen.getByTestId("kpi-open");
    expect(openKpi.textContent).toBe("0");
  });

  it("shows — for MTTR when no closed investigations", () => {
    renderTab();
    const mttrKpi = screen.getByTestId("kpi-mttr");
    expect(mttrKpi.textContent).toBe("—");
  });
});

// ---------------------------------------------------------------------------
// Tests: Loading state
// ---------------------------------------------------------------------------

describe("ButlerQaInvestigationsTab — loading state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupLoading();
  });
  afterEach(() => cleanup());

  it("shows loading skeletons in investigations section", () => {
    renderTab();
    const loadingLines = screen.getAllByTestId("loading-line");
    expect(loadingLines.length).toBeGreaterThanOrEqual(1);
  });

  it("does not show empty-state text while loading", () => {
    renderTab();
    const emptyLines = screen.queryAllByTestId("empty-state-line");
    expect(emptyLines.length).toBe(0);
  });

  it("does not show investigations table while loading", () => {
    renderTab();
    expect(screen.queryByTestId("investigations-table")).toBeNull();
  });

  it("shows '…' in KPI cells while loading", () => {
    renderTab();
    const kpiOpen = screen.getByTestId("kpi-open");
    expect(kpiOpen.textContent).toBe("…");
  });
});

// ---------------------------------------------------------------------------
// Tests: Circuit breaker chip
// ---------------------------------------------------------------------------

describe("ButlerQaInvestigationsTab — circuit breaker", () => {
  afterEach(() => cleanup());

  it("shows 'closed' chip when circuit breaker is not tripped", () => {
    vi.resetAllMocks();
    setupWithData();
    renderTab();
    expect(screen.getByTestId("circuit-breaker-closed")).toBeDefined();
    expect(screen.queryByTestId("circuit-breaker-tripped")).toBeNull();
  });

  it("shows 'open' chip when circuit breaker is tripped", () => {
    vi.resetAllMocks();
    setupTrippedCircuitBreaker();
    renderTab();
    expect(screen.getByTestId("circuit-breaker-tripped")).toBeDefined();
    expect(screen.queryByTestId("circuit-breaker-closed")).toBeNull();
  });
});
