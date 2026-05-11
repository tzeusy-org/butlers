// @vitest-environment jsdom
/**
 * ButlerHealthMeasurementsTab — RTL tests covering the five rows.
 *
 * Tests:
 *  - KPI quartet renders with mock data
 *  - KPI quartet shows placeholders while loading
 *  - Trend panels render with data and show empty states
 *  - Sleep stages bar renders stages with correct structure
 *  - Sources list renders with status and count
 *  - Active medications list renders
 *  - Recent conditions list renders
 *  - Drilldown links resolve to correct routes
 *
 * bead: bu-iuol4.23
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-health", () => ({
  useMeasurementsLatest: vi.fn(),
  useSleepLatest: vi.fn(),
  useMeasurementSources: vi.fn(),
  useMeasurements: vi.fn(),
  useMedications: vi.fn(),
  useConditions: vi.fn(),
}));

// Suppress the timezone context — use UTC for tests
vi.mock("@/components/ui/timezone-context", () => ({
  useTimezone: () => "UTC",
}));

import {
  useMeasurementsLatest,
  useSleepLatest,
  useMeasurementSources,
  useMeasurements,
  useMedications,
  useConditions,
} from "@/hooks/use-health";

import ButlerHealthMeasurementsTab from "./ButlerHealthMeasurementsTab";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const MEASUREMENTS_LATEST = {
  measurements: {
    glucose: {
      measured_at: "2026-05-11T08:00:00Z",
      value: { value: 95 },
      unit: "mg/dL",
      metadata: null,
    },
    hrv: {
      measured_at: "2026-05-11T07:30:00Z",
      value: { value: 42 },
      unit: "ms",
      metadata: null,
    },
    steps: {
      measured_at: "2026-05-10T23:59:00Z",
      value: { value: 8432 },
      unit: null,
      metadata: null,
    },
  },
};

const SLEEP_DATA = {
  session_date: "2026-05-11",
  total_minutes: 450,
  stages: [
    { stage: "awake", duration_minutes: 20, start_time: null },
    { stage: "light", duration_minutes: 200, start_time: null },
    { stage: "deep", duration_minutes: 100, start_time: null },
    { stage: "rem", duration_minutes: 130, start_time: null },
  ],
  source: "oura",
};

const SOURCES = [
  { name: "oura", last_sample_at: "2026-05-11T08:00:00Z", sample_count: 1240 },
  { name: "apple_health", last_sample_at: "2026-05-11T07:00:00Z", sample_count: 5821 },
  { name: "manual", last_sample_at: null, sample_count: 0 },
];

const MEASUREMENTS_TREND = [
  {
    id: "m-1",
    type: "glucose",
    value: { value: 92 },
    measured_at: "2026-05-10T08:00:00Z",
    notes: null,
    created_at: "2026-05-10T08:00:00Z",
  },
  {
    id: "m-2",
    type: "glucose",
    value: { value: 95 },
    measured_at: "2026-05-11T08:00:00Z",
    notes: null,
    created_at: "2026-05-11T08:00:00Z",
  },
];

const MEDICATIONS = [
  {
    id: "med-1",
    name: "Metformin",
    dosage: "500mg",
    frequency: "twice daily",
    schedule: [],
    active: true,
    notes: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "med-2",
    name: "Old Drug",
    dosage: "100mg",
    frequency: "daily",
    schedule: [],
    active: false,
    notes: null,
    created_at: "2025-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

const CONDITIONS = [
  {
    id: "cond-1",
    name: "Type 2 Diabetes",
    status: "managed",
    diagnosed_at: "2022-03-15",
    notes: null,
    created_at: "2022-03-15T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "cond-2",
    name: "Hypertension",
    status: "active",
    diagnosed_at: null,
    notes: null,
    created_at: "2024-06-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

// ---------------------------------------------------------------------------
// Render helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderTab() {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={makeQueryClient()}>
        <ButlerHealthMeasurementsTab />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Mock setup helpers
// ---------------------------------------------------------------------------

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyQueryResult = any;

function setupWithData() {
  vi.mocked(useMeasurementsLatest).mockReturnValue({
    data: MEASUREMENTS_LATEST,
    isLoading: false,
  } as AnyQueryResult);

  vi.mocked(useSleepLatest).mockReturnValue({
    data: SLEEP_DATA,
    isLoading: false,
  } as AnyQueryResult);

  vi.mocked(useMeasurementSources).mockReturnValue({
    data: SOURCES,
    isLoading: false,
    isError: false,
  } as AnyQueryResult);

  vi.mocked(useMeasurements).mockReturnValue({
    data: { data: MEASUREMENTS_TREND, meta: { total: 2, offset: 0, limit: 50 } },
    isLoading: false,
  } as AnyQueryResult);

  vi.mocked(useMedications).mockReturnValue({
    data: { data: MEDICATIONS, meta: { total: 2, offset: 0, limit: 20 } },
    isLoading: false,
  } as AnyQueryResult);

  vi.mocked(useConditions).mockReturnValue({
    data: { data: CONDITIONS, meta: { total: 2, offset: 0, limit: 10 } },
    isLoading: false,
  } as AnyQueryResult);
}

function setupLoading() {
  vi.mocked(useMeasurementsLatest).mockReturnValue({ data: undefined, isLoading: true } as AnyQueryResult);
  vi.mocked(useSleepLatest).mockReturnValue({ data: undefined, isLoading: true } as AnyQueryResult);
  vi.mocked(useMeasurementSources).mockReturnValue({ data: undefined, isLoading: true } as AnyQueryResult);
  vi.mocked(useMeasurements).mockReturnValue({ data: undefined, isLoading: true } as AnyQueryResult);
  vi.mocked(useMedications).mockReturnValue({ data: undefined, isLoading: true } as AnyQueryResult);
  vi.mocked(useConditions).mockReturnValue({ data: undefined, isLoading: true } as AnyQueryResult);
}

function setupEmpty() {
  vi.mocked(useMeasurementsLatest).mockReturnValue({
    data: { measurements: {} },
    isLoading: false,
  } as AnyQueryResult);

  vi.mocked(useSleepLatest).mockReturnValue({
    data: { session_date: null, total_minutes: null, stages: null, source: null },
    isLoading: false,
  } as AnyQueryResult);

  vi.mocked(useMeasurementSources).mockReturnValue({
    data: [],
    isLoading: false,
    isError: false,
  } as AnyQueryResult);

  vi.mocked(useMeasurements).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 50 } },
    isLoading: false,
  } as AnyQueryResult);

  vi.mocked(useMedications).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 20 } },
    isLoading: false,
  } as AnyQueryResult);

  vi.mocked(useConditions).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 10 } },
    isLoading: false,
  } as AnyQueryResult);
}

// ---------------------------------------------------------------------------
// Tests: overall structure
// ---------------------------------------------------------------------------

describe("ButlerHealthMeasurementsTab — overall structure", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the outer tab container", () => {
    renderTab();
    expect(screen.getByTestId("health-measurements-tab")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: KPI quartet
// ---------------------------------------------------------------------------

describe("ButlerHealthMeasurementsTab — KPI quartet", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the KPI quartet", () => {
    renderTab();
    expect(screen.getByTestId("health-kpi-quartet")).toBeDefined();
  });

  it("renders four KPI cells", () => {
    renderTab();
    const cells = screen.getAllByTestId("kpi-cell");
    expect(cells.length).toBe(4);
  });

  it("renders glucose value from latest measurements", () => {
    renderTab();
    // "95" appears at least once in the KPI strip
    const allMatches = screen.getAllByText("95");
    expect(allMatches.length).toBeGreaterThanOrEqual(1);
  });

  it("renders HRV value from latest measurements", () => {
    renderTab();
    expect(screen.getByText("42")).toBeDefined();
  });

  it("renders steps value from latest measurements", () => {
    renderTab();
    expect(screen.getByText("8432")).toBeDefined();
  });

  it("renders sleep duration from sleep/latest", () => {
    renderTab();
    // 450 minutes = 7h 30m
    expect(screen.getByText("7h 30m")).toBeDefined();
  });

  it("renders KPI labels", () => {
    renderTab();
    expect(screen.getByText("Glucose")).toBeDefined();
    expect(screen.getByText("HRV")).toBeDefined();
    expect(screen.getByText("Steps")).toBeDefined();
    expect(screen.getByText("Sleep duration")).toBeDefined();
  });

  it("shows placeholder '…' while loading", () => {
    vi.resetAllMocks();
    setupLoading();
    renderTab();
    const loadingValues = screen.getAllByTestId("kpi-value");
    const placeholders = loadingValues.filter((el) => el.textContent === "…");
    expect(placeholders.length).toBeGreaterThanOrEqual(1);
  });

  it("shows '—' for missing measurement types", () => {
    vi.resetAllMocks();
    setupEmpty();
    renderTab();
    const values = screen.getAllByTestId("kpi-value");
    const dashes = values.filter((el) => el.textContent === "—");
    expect(dashes.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: Trend panels — empty states
// ---------------------------------------------------------------------------

describe("ButlerHealthMeasurementsTab — trend panels empty states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupEmpty();
  });
  afterEach(() => cleanup());

  it("shows empty-state text when no trend data", () => {
    renderTab();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    // Multiple empty states: trends, sleep, sources, meds, conditions
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });

  it("does not render trend-list when empty", () => {
    renderTab();
    expect(screen.queryByTestId("trend-list")).toBeNull();
  });
});

describe("ButlerHealthMeasurementsTab — trend panels with data", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders trend lists", () => {
    renderTab();
    const trendLists = screen.getAllByTestId("trend-list");
    expect(trendLists.length).toBeGreaterThanOrEqual(1);
  });

  it("renders trend rows", () => {
    renderTab();
    const trendRows = screen.getAllByTestId("trend-row");
    expect(trendRows.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: Sleep stages bar
// ---------------------------------------------------------------------------

describe("ButlerHealthMeasurementsTab — sleep stages bar", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the sleep stages panel", () => {
    renderTab();
    expect(screen.getByTestId("sleep-stages-panel")).toBeDefined();
  });

  it("renders the sleep stages bar", () => {
    renderTab();
    expect(screen.getByTestId("sleep-stages-bar")).toBeDefined();
  });

  it("renders a segment for each stage", () => {
    renderTab();
    expect(screen.getByTestId("sleep-stage-awake")).toBeDefined();
    expect(screen.getByTestId("sleep-stage-light")).toBeDefined();
    expect(screen.getByTestId("sleep-stage-deep")).toBeDefined();
    expect(screen.getByTestId("sleep-stage-rem")).toBeDefined();
  });

  it("renders stage rows in the legend", () => {
    renderTab();
    const rows = screen.getAllByTestId("sleep-stage-row");
    expect(rows.length).toBe(4);
  });

  it("shows empty state when no sleep data", () => {
    vi.resetAllMocks();
    setupEmpty();
    renderTab();
    expect(screen.queryByTestId("sleep-stages-bar")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: Sources list
// ---------------------------------------------------------------------------

describe("ButlerHealthMeasurementsTab — sources list", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the sources panel", () => {
    renderTab();
    expect(screen.getByTestId("sources-panel")).toBeDefined();
  });

  it("renders the sources list", () => {
    renderTab();
    expect(screen.getByTestId("sources-list")).toBeDefined();
  });

  it("renders source rows", () => {
    renderTab();
    const rows = screen.getAllByTestId("source-row");
    expect(rows.length).toBe(3);
  });

  it("shows source sample counts", () => {
    renderTab();
    // Use a regex tolerant of locale-specific grouping separators.
    // toLocaleString("en-US", { useGrouping: true }) produces "1,240" in most
    // test environments but runners with non-en-US locale may differ.
    // The regex /1.240/ matches the digit sequence regardless of separator char.
    expect(screen.getByText(/^1.240$/)).toBeDefined();
  });

  it("shows 'No sources connected' when empty", () => {
    vi.resetAllMocks();
    setupEmpty();
    renderTab();
    expect(screen.queryByTestId("sources-list")).toBeNull();
  });

  it("shows error message when sources query fails", () => {
    vi.resetAllMocks();
    setupWithData();
    vi.mocked(useMeasurementSources).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as AnyQueryResult);
    renderTab();
    expect(screen.queryByTestId("sources-list")).toBeNull();
    expect(screen.getByText("Could not load sources.")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Active medications
// ---------------------------------------------------------------------------

describe("ButlerHealthMeasurementsTab — active medications", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the active medications panel", () => {
    renderTab();
    expect(screen.getByTestId("active-medications-panel")).toBeDefined();
  });

  it("renders medications list", () => {
    renderTab();
    expect(screen.getByTestId("medications-list")).toBeDefined();
  });

  it("only shows active medications", () => {
    renderTab();
    // Metformin (active) appears; "Old Drug" (inactive) should not
    expect(screen.getByText("Metformin")).toBeDefined();
    expect(screen.queryByText("Old Drug")).toBeNull();
  });

  it("shows empty state when no medications", () => {
    vi.resetAllMocks();
    setupEmpty();
    renderTab();
    expect(screen.queryByTestId("medications-list")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Recent conditions
// ---------------------------------------------------------------------------

describe("ButlerHealthMeasurementsTab — recent conditions", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the recent conditions panel", () => {
    renderTab();
    expect(screen.getByTestId("recent-conditions-panel")).toBeDefined();
  });

  it("renders conditions list", () => {
    renderTab();
    expect(screen.getByTestId("conditions-list")).toBeDefined();
  });

  it("renders condition names", () => {
    renderTab();
    expect(screen.getByText("Type 2 Diabetes")).toBeDefined();
    expect(screen.getByText("Hypertension")).toBeDefined();
  });

  it("renders condition rows", () => {
    renderTab();
    const rows = screen.getAllByTestId("condition-row");
    expect(rows.length).toBe(2);
  });

  it("shows empty state when no conditions", () => {
    vi.resetAllMocks();
    setupEmpty();
    renderTab();
    expect(screen.queryByTestId("conditions-list")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Drilldown link targets
// ---------------------------------------------------------------------------

describe("ButlerHealthMeasurementsTab — drilldown links", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("provides a drilldown link to /health/measurements", () => {
    renderTab();
    const links = screen.getAllByRole("link");
    const hrefs = links.map((l) => l.getAttribute("href")).filter(Boolean);
    expect(hrefs.some((h) => h === "/health/measurements")).toBe(true);
  });

  it("provides a drilldown link to /health/medications", () => {
    renderTab();
    const links = screen.getAllByRole("link");
    const hrefs = links.map((l) => l.getAttribute("href")).filter(Boolean);
    expect(hrefs.some((h) => h === "/health/medications")).toBe(true);
  });

  it("provides a drilldown link to /health/conditions", () => {
    renderTab();
    const links = screen.getAllByRole("link");
    const hrefs = links.map((l) => l.getAttribute("href")).filter(Boolean);
    expect(hrefs.some((h) => h === "/health/conditions")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Tests: Loading state
// ---------------------------------------------------------------------------

describe("ButlerHealthMeasurementsTab — loading state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupLoading();
  });
  afterEach(() => cleanup());

  it("shows loading indicators while queries are pending", () => {
    renderTab();
    const loadingLines = screen.getAllByTestId("loading-line");
    expect(loadingLines.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: getAllTabs includes health tab
// ---------------------------------------------------------------------------

import { getAllTabs, isValidTab } from "@/pages/butler-detail-tabs";

describe("ButlerDetailPage — health tab in getAllTabs", () => {
  it("health butler has 'health' tab in operator mode", () => {
    expect(getAllTabs("health", "operator")).toContain("health");
  });

  it("health butler has 'health' tab in resident mode", () => {
    expect(getAllTabs("health", "resident")).toContain("health");
  });

  it("'health' is a valid tab for health butler", () => {
    expect(isValidTab("health", "health", "operator")).toBe(true);
    expect(isValidTab("health", "health", "resident")).toBe(true);
  });

  it("'health' is NOT a valid tab for non-health butlers", () => {
    expect(isValidTab("health", "finance", "operator")).toBe(false);
    expect(isValidTab("health", "general", "resident")).toBe(false);
  });
});
