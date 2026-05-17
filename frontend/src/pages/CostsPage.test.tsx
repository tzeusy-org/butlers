// @vitest-environment jsdom

/**
 * Tests for CostsPage workspace upgrade (bu-e8b5w.5).
 *
 * Covers:
 * 1. Workspace archetype — uses Page with archetype="workspace"
 * 2. TimeWindowPicker renders (toolbar row)
 * 3. Summary stats cards render with data
 * 4. CostStripeChart renders
 * 5. CostBreakdownTable renders
 * 6. Loading state: workspace skeleton shown
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import CostsPage from "@/pages/CostsPage";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-spend", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-spend")>();
  return {
    ...actual,
    useSpendSummary: vi.fn(),
    useDailySpend: vi.fn(),
  };
});

// Mock CostStripeChart — avoids recharts SSR complexity
vi.mock("@/components/costs/CostStripeChart", () => ({
  CostStripeChart: (props: { isLoading?: boolean; isError?: boolean }) => (
    <div
      data-testid={
        props.isLoading
          ? "cost-stripe-skeleton"
          : props.isError
            ? "cost-stripe-error"
            : "cost-stripe-chart"
      }
    />
  ),
}));

// Mock Scrubber — avoids range input SSR complexity
vi.mock("@/components/workspace/Scrubber", () => ({
  Scrubber: () => <div data-testid="scrubber" />,
}));

// ---------------------------------------------------------------------------
// Imports after mocks
// ---------------------------------------------------------------------------

import { useSpendSummary, useDailySpend } from "@/hooks/use-spend";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const SUMMARY_DATA = {
  total_cost_usd: 1.23,
  total_sessions: 42,
  total_input_tokens: 100_000,
  total_output_tokens: 50_000,
  by_butler: { general: 0.80, memory: 0.43 },
  by_model: { "claude-sonnet-4-5": 1.23 },
};

const DAILY_DATA = [
  { date: "2026-04-24", cost_usd: 0.60, sessions: 20, input_tokens: 50_000, output_tokens: 25_000 },
  { date: "2026-04-25", cost_usd: 0.63, sessions: 22, input_tokens: 50_000, output_tokens: 25_000 },
];

function setLoading() {
  vi.mocked(useSpendSummary).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
    error: null,
  } as AnyMock);
  vi.mocked(useDailySpend).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
    error: null,
  } as AnyMock);
}

function setSuccess() {
  vi.mocked(useSpendSummary).mockReturnValue({
    data: { data: SUMMARY_DATA, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
  } as AnyMock);
  vi.mocked(useDailySpend).mockReturnValue({
    data: { data: DAILY_DATA, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
  } as AnyMock);
}

function renderPage(initialUrl = "/"): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialUrl]}>
        <CostsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CostsPage — loading state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setLoading();
  });

  it("shows the workspace loading skeleton (not the page content)", () => {
    const html = renderPage();
    // Page archetype=workspace renders a WorkspaceSkeleton while loading
    expect(html).toContain("Loading");
  });
});

describe("CostsPage — success state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setSuccess();
  });

  it("renders the page title", () => {
    const html = renderPage();
    expect(html).toContain("Costs &amp; Usage");
  });

  it("renders the TimeWindowPicker toolbar", () => {
    const html = renderPage();
    // TimeWindowPicker renders preset buttons
    expect(html).toContain("Today");
    expect(html).toContain("Last 7 days");
  });

  it("renders total cost stat card", () => {
    const html = renderPage();
    expect(html).toContain("Total Cost");
    expect(html).toContain("$1.23");
  });

  it("renders session count stat card", () => {
    const html = renderPage();
    expect(html).toContain("Total Sessions");
    expect(html).toContain("42");
  });

  it("renders input/output token stat cards", () => {
    const html = renderPage();
    expect(html).toContain("Input Tokens");
    expect(html).toContain("Output Tokens");
    expect(html).toContain("100.0K");
    expect(html).toContain("50.0K");
  });

  it("renders CostStripeChart (primary chart)", () => {
    const html = renderPage();
    expect(html).toContain('data-testid="cost-stripe-chart"');
  });

  it("renders Scrubber over the chart", () => {
    const html = renderPage();
    expect(html).toContain('data-testid="scrubber"');
  });

  it("renders Spending Over Time heading", () => {
    const html = renderPage();
    expect(html).toContain("Spending Over Time");
  });

  it("renders Cost by Butler breakdown table", () => {
    const html = renderPage();
    expect(html).toContain("Cost by Butler");
    expect(html).toContain("general");
    expect(html).toContain("memory");
  });
});

describe("CostsPage — URL-driven time window", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setSuccess();
  });

  it("renders without crashing when URL has from/to params", () => {
    const html = renderPage("/?from=2026-04-01&to=2026-04-30");
    expect(html).toContain("Costs &amp; Usage");
  });
});
