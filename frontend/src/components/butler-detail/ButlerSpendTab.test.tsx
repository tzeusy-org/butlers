// @vitest-environment jsdom
/**
 * ButlerSpendTab — RTL tests pinning KPI strip, trend panel, and model breakdown.
 *
 * Tests cover:
 *  - All 3 range toggle states (24h / 7d / 30d) wired through UI
 *  - Empty butler (no data in by_butler)
 *  - Model breakdown rendering
 *  - Error state for each panel
 *  - Loading state (all panels)
 *
 * ?butler= filter note:
 *   The backend does not yet scope /api/costs by butler (bu-iuol4.12 open).
 *   The spend tab derives per-butler cost via by_butler[butlerName] where
 *   possible. Trend and model breakdown panels show all-butler data.
 *
 * bead: bu-iuol4.19
 */

import { createElement } from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-costs", () => ({
  useCostSummary: vi.fn(),
  useDailyCosts: vi.fn(),
  formatCostDate: vi.fn((d: Date) => d.toISOString().slice(0, 10)),
}));

vi.mock("@/hooks/use-time-window", () => ({
  OWNER_TZ_DEFAULT: "UTC",
}));

vi.mock("@/components/chronicles/tz-format", () => ({
  startOfDayInTz: (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()),
  endOfDayInTz: (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate(), 23, 59, 59, 999),
}));

// Mock DayBars to avoid DOM complexity
vi.mock("@/components/butlers/DayBars", () => ({
  DayBars: ({ data, className }: { data: number[]; className?: string }) =>
    createElement("div", {
      "data-testid": "day-bars",
      className,
      "data-length": data.length,
    }),
}));

// Mock RangeToggle to be a controlled wrapper we can interact with
vi.mock("@/components/ui/range-toggle", () => {
  const RANGE_OPTIONS = [
    { value: "24h", label: "24H" },
    { value: "7d", label: "7D" },
    { value: "30d", label: "30D" },
  ];
  return {
    RangeToggle: ({
      value,
      onChange,
      disabled,
    }: {
      value: string;
      onChange: (v: string) => void;
      disabled?: boolean;
    }) =>
      createElement(
        "div",
        { "data-testid": "range-toggle" },
        RANGE_OPTIONS.map(({ value: v, label }) =>
          createElement(
            "button",
            {
              key: v,
              "data-testid": `range-btn-${v}`,
              "aria-pressed": value === v,
              disabled,
              onClick: () => onChange(v),
            },
            label,
          ),
        ),
      ),
  };
});

import { useCostSummary, useDailyCosts } from "@/hooks/use-costs";
import ButlerSpendTab from "./ButlerSpendTab";

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

const BUTLER_NAME = "test-butler";

const COST_SUMMARY_TODAY = {
  data: {
    period: "today",
    total_cost_usd: 0.52,
    total_sessions: 8,
    total_input_tokens: 45_000,
    total_output_tokens: 12_000,
    by_butler: {
      "test-butler": 0.18,
      "other-butler": 0.34,
    },
    by_model: {
      "claude-sonnet-4-5": 0.45,
      "claude-haiku-3": 0.07,
    },
  },
};

const COST_SUMMARY_30D = {
  data: {
    period: "30d",
    total_cost_usd: 12.44,
    total_sessions: 180,
    total_input_tokens: 1_200_000,
    total_output_tokens: 340_000,
    by_butler: {
      "test-butler": 4.80,
      "other-butler": 7.64,
    },
    by_model: {
      "claude-sonnet-4-5": 10.22,
      "claude-haiku-3": 2.22,
    },
  },
};

const DAILY_COSTS = {
  data: [
    { date: "2026-05-05", cost_usd: 0.42, sessions: 6, input_tokens: 30000, output_tokens: 8000 },
    { date: "2026-05-06", cost_usd: 0.37, sessions: 5, input_tokens: 25000, output_tokens: 7000 },
    { date: "2026-05-07", cost_usd: 0.55, sessions: 9, input_tokens: 40000, output_tokens: 11000 },
    { date: "2026-05-08", cost_usd: 0.48, sessions: 7, input_tokens: 35000, output_tokens: 9000 },
    { date: "2026-05-09", cost_usd: 0.30, sessions: 4, input_tokens: 20000, output_tokens: 6000 },
    { date: "2026-05-10", cost_usd: 0.52, sessions: 8, input_tokens: 42000, output_tokens: 12000 },
    { date: "2026-05-11", cost_usd: 0.18, sessions: 3, input_tokens: 15000, output_tokens: 4000 },
  ],
};

// ---------------------------------------------------------------------------
// Render helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderTab(butlerName = BUTLER_NAME) {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <ButlerSpendTab butlerName={butlerName} />
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Mock setup helpers
// ---------------------------------------------------------------------------

function setupWithData() {
  vi.mocked(useCostSummary).mockImplementation((period?: string) => {
    if (period === "today") {
      return {
        data: COST_SUMMARY_TODAY,
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useCostSummary>;
    }
    return {
      data: COST_SUMMARY_30D,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useCostSummary>;
  });

  vi.mocked(useDailyCosts).mockReturnValue({
    data: DAILY_COSTS,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useDailyCosts>);
}

function setupEmpty() {
  vi.mocked(useCostSummary).mockReturnValue({
    data: {
      data: {
        period: "today",
        total_cost_usd: 0,
        total_sessions: 0,
        total_input_tokens: 0,
        total_output_tokens: 0,
        by_butler: {},
        by_model: {},
      },
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useCostSummary>);

  vi.mocked(useDailyCosts).mockReturnValue({
    data: { data: [] },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useDailyCosts>);
}

function setupLoading() {
  vi.mocked(useCostSummary).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useCostSummary>);

  vi.mocked(useDailyCosts).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useDailyCosts>);
}

function setupError() {
  vi.mocked(useCostSummary).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useCostSummary>);

  vi.mocked(useDailyCosts).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useDailyCosts>);
}

// ---------------------------------------------------------------------------
// Tests: outer container and sections present
// ---------------------------------------------------------------------------

describe("ButlerSpendTab — sections present", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the outer spend tab container", () => {
    renderTab();
    expect(screen.getByTestId("spend-tab")).toBeDefined();
  });

  it("renders the KPI strip section", () => {
    renderTab();
    expect(screen.getByTestId("spend-kpi-strip")).toBeDefined();
  });

  it("renders the trend panel section", () => {
    renderTab();
    expect(screen.getByTestId("spend-trend-section")).toBeDefined();
  });

  it("renders the model breakdown panel section", () => {
    renderTab();
    expect(screen.getByTestId("spend-model-breakdown-section")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: KPI strip
// ---------------------------------------------------------------------------

describe("ButlerSpendTab — KPI strip labels and values", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders four KPI value cells", () => {
    renderTab();
    const cells = screen.getAllByTestId("kpi-value");
    expect(cells.length).toBeGreaterThanOrEqual(4);
  });

  it("renders 'Spend today' label", () => {
    renderTab();
    expect(screen.getByText("Spend today")).toBeDefined();
  });

  it("renders 'Spend 30d' label", () => {
    renderTab();
    expect(screen.getByText("Spend 30d")).toBeDefined();
  });

  it("renders 'Cost / session · 30d' label", () => {
    renderTab();
    expect(screen.getByText("Cost / session · 30d")).toBeDefined();
  });

  it("renders 'Tokens today' label", () => {
    renderTab();
    expect(screen.getByText("Tokens today")).toBeDefined();
  });

  it("shows per-butler today spend formatted as currency", () => {
    renderTab();
    const strip = screen.getByTestId("spend-kpi-strip");
    // COST_SUMMARY_TODAY by_butler[test-butler] = 0.18 → "$0.18"
    expect(strip.textContent).toContain("$0.18");
  });

  it("shows per-butler 30d spend formatted as currency", () => {
    renderTab();
    const strip = screen.getByTestId("spend-kpi-strip");
    // COST_SUMMARY_30D by_butler[test-butler] = 4.80
    expect(strip.textContent).toContain("$4.80");
  });
});

// ---------------------------------------------------------------------------
// Tests: RangeToggle — all 3 ranges
// ---------------------------------------------------------------------------

describe("ButlerSpendTab — range toggle interaction", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the range toggle inside the trend panel", () => {
    renderTab();
    expect(screen.getByTestId("range-toggle")).toBeDefined();
  });

  it("defaults to 7d range (aria-pressed=true on 7D button)", () => {
    renderTab();
    const btn7d = screen.getByTestId("range-btn-7d");
    expect(btn7d.getAttribute("aria-pressed")).toBe("true");
  });

  it("clicking 24H button changes active range to 24h", () => {
    renderTab();
    const btn24h = screen.getByTestId("range-btn-24h");
    act(() => fireEvent.click(btn24h));
    expect(btn24h.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("range-btn-7d").getAttribute("aria-pressed")).toBe("false");
  });

  it("clicking 30D button changes active range to 30d", () => {
    renderTab();
    const btn30d = screen.getByTestId("range-btn-30d");
    act(() => fireEvent.click(btn30d));
    expect(btn30d.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("range-btn-7d").getAttribute("aria-pressed")).toBe("false");
  });

  it("renders DayBars when trend data is available", () => {
    renderTab();
    expect(screen.getByTestId("day-bars")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: Model breakdown rendering
// ---------------------------------------------------------------------------

describe("ButlerSpendTab — model breakdown", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("renders the model breakdown list", () => {
    renderTab();
    expect(screen.getByTestId("model-breakdown-list")).toBeDefined();
  });

  it("renders a row for each model in by_model", () => {
    renderTab();
    const rows = screen.getAllByTestId("model-breakdown-row");
    expect(rows.length).toBe(2); // claude-sonnet-4-5 + claude-haiku-3
  });

  it("shows model name in the breakdown row", () => {
    renderTab();
    expect(screen.getByText("claude-sonnet-4-5")).toBeDefined();
    expect(screen.getByText("claude-haiku-3")).toBeDefined();
  });

  it("shows formatted cost for the top model", () => {
    renderTab();
    const list = screen.getByTestId("model-breakdown-list");
    // claude-sonnet-4-5 cost is $10.22
    expect(list.textContent).toContain("$10.22");
  });

  it("shows percentage share in the model row", () => {
    renderTab();
    const list = screen.getByTestId("model-breakdown-list");
    // 10.22 / 12.44 ≈ 82.2%
    expect(list.textContent).toContain("%");
  });
});

// ---------------------------------------------------------------------------
// Tests: Empty butler (no spend data for this butler)
// ---------------------------------------------------------------------------

describe("ButlerSpendTab — empty butler (no spend data)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupEmpty();
  });
  afterEach(() => cleanup());

  it("shows $0.00 for spend today when butler has no cost data (missing = zero)", () => {
    renderTab("unknown-butler");
    const strip = screen.getByTestId("spend-kpi-strip");
    // by_butler has no entry for "unknown-butler"; backend omits zeros, so
    // missing entry is treated as $0.00 (not "—") once data loads.
    expect(strip.textContent).toContain("$0.00");
  });

  it("shows empty state for model breakdown when no models", () => {
    renderTab();
    expect(screen.queryByTestId("model-breakdown-list")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });

  it("shows empty state for trend when no daily data", () => {
    renderTab();
    expect(screen.queryByTestId("day-bars")).toBeNull();
    const emptyLines = screen.getAllByTestId("empty-state-line");
    expect(emptyLines.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Tests: Loading state
// ---------------------------------------------------------------------------

describe("ButlerSpendTab — loading state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupLoading();
  });
  afterEach(() => cleanup());

  it("shows loading placeholders while queries are pending", () => {
    renderTab();
    const loadingLines = screen.getAllByTestId("loading-line");
    expect(loadingLines.length).toBeGreaterThanOrEqual(1);
  });

  it("shows '...' in KPI cells while loading", () => {
    renderTab();
    const strip = screen.getByTestId("spend-kpi-strip");
    expect(strip.textContent).toContain("...");
  });

  it("does not show error state while loading", () => {
    renderTab();
    expect(screen.queryByTestId("error-state-line")).toBeNull();
  });

  it("does not render day-bars while loading", () => {
    renderTab();
    expect(screen.queryByTestId("day-bars")).toBeNull();
  });

  it("does not render model breakdown list while loading", () => {
    renderTab();
    expect(screen.queryByTestId("model-breakdown-list")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: Error state
// ---------------------------------------------------------------------------

describe("ButlerSpendTab — error state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupError();
  });
  afterEach(() => cleanup());

  it("shows error lines when queries fail", () => {
    renderTab();
    const errorLines = screen.getAllByTestId("error-state-line");
    expect(errorLines.length).toBeGreaterThanOrEqual(1);
  });

  it("shows '—' in KPI cells on error", () => {
    renderTab();
    const strip = screen.getByTestId("spend-kpi-strip");
    expect(strip.textContent).toContain("—");
  });

  it("does not show loading placeholders on error", () => {
    renderTab();
    expect(screen.queryByTestId("loading-line")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: "all butlers" degraded note in panel subtitles
// ---------------------------------------------------------------------------

describe("ButlerSpendTab — degraded 'all butlers' notes", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupWithData();
  });
  afterEach(() => cleanup());

  it("shows 'all butlers' note on trend panel subtitle", () => {
    renderTab();
    const trendPanel = screen.getByTestId("spend-trend-section");
    expect(trendPanel.textContent).toContain("all butlers");
  });

  it("shows 'all butlers' note on model breakdown panel subtitle", () => {
    renderTab();
    const modelPanel = screen.getByTestId("spend-model-breakdown-section");
    expect(modelPanel.textContent).toContain("all butlers");
  });
});

// ---------------------------------------------------------------------------
// Tests: spend tab is part of resident base tabs
// ---------------------------------------------------------------------------

import { getAllTabs, isValidTab } from "@/pages/butler-detail-tabs";

describe("ButlerDetailPage — spend tab present in all butlers (resident mode)", () => {
  it("resident base tabs include 'spend'", () => {
    expect(getAllTabs("general", "resident")).toContain("spend");
  });

  it("'spend' is valid for any butler in resident mode", () => {
    expect(isValidTab("spend", "general", "resident")).toBe(true);
    expect(isValidTab("spend", "finance", "resident")).toBe(true);
    expect(isValidTab("spend", "health", "resident")).toBe(true);
  });

  it("'spend' is NOT in operator base tabs", () => {
    // 'spend' is a resident-mode tab; operator mode uses 'sessions' instead
    expect(getAllTabs("general", "operator")).not.toContain("spend");
  });
});
