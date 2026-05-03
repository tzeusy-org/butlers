/**
 * Tests for DashboardPage.
 *
 * Focused on verifying the post-Vertical-D information hierarchy:
 * - TopologyGraph is absent (moved to /system)
 * - Hero (Sessions) and secondary (Recent Activity) regions are present
 * - Stat strip is present
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import DashboardPage from "@/pages/DashboardPage";

// ---------------------------------------------------------------------------
// Mock all hooks used by DashboardPage
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-butlers", () => ({ useButlers: vi.fn() }));
vi.mock("@/hooks/use-costs", () => ({ useCostSummary: vi.fn() }));
vi.mock("@/hooks/use-sessions", () => ({ useSessions: vi.fn() }));
vi.mock("@/hooks/use-issues", () => ({ useIssues: vi.fn() }));
vi.mock("@/hooks/use-notifications", () => ({ useNotifications: vi.fn() }));
vi.mock("@/hooks/use-approvals", () => ({ useApprovalMetrics: vi.fn() }));
vi.mock("@/hooks/use-qa", () => ({ useQaSummary: vi.fn() }));

// DashboardPage renders SessionStripeChart and RecentMoments -- mock them to
// keep the test hermetic (they have their own hooks/deps).
vi.mock("@/components/dashboard/SessionStripeChart", () => ({
  SessionStripeChart: () => <div data-testid="session-stripe-chart" />,
}));
vi.mock("@/components/dashboard/RecentMoments", () => ({
  RecentMoments: () => <div data-testid="recent-moments" />,
}));

// ---------------------------------------------------------------------------
// Imports after mocks are registered
// ---------------------------------------------------------------------------

import { useButlers } from "@/hooks/use-butlers";
import { useCostSummary } from "@/hooks/use-costs";
import { useSessions } from "@/hooks/use-sessions";
import { useIssues } from "@/hooks/use-issues";
import { useNotifications } from "@/hooks/use-notifications";
import { useApprovalMetrics } from "@/hooks/use-approvals";
import { useQaSummary } from "@/hooks/use-qa";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

function setAllLoading() {
  vi.mocked(useButlers).mockReturnValue({ data: undefined, isLoading: true, isError: false, error: null, refetch: vi.fn() } as AnyMock);
  vi.mocked(useCostSummary).mockReturnValue({ data: undefined, isLoading: true, isError: false, error: null } as AnyMock);
  vi.mocked(useSessions).mockReturnValue({ data: undefined, isLoading: true, isError: false, error: null } as AnyMock);
  vi.mocked(useIssues).mockReturnValue({ data: undefined, isLoading: true, isError: false, error: null } as AnyMock);
  vi.mocked(useNotifications).mockReturnValue({ data: undefined, isLoading: true, isError: false, error: null } as AnyMock);
  vi.mocked(useApprovalMetrics).mockReturnValue({ data: undefined, isLoading: true, isError: false, error: null } as AnyMock);
  vi.mocked(useQaSummary).mockReturnValue({ data: undefined, isLoading: true, isError: false, error: null } as AnyMock);
}

function setAllSuccess() {
  vi.mocked(useButlers).mockReturnValue({
    data: { data: [{ name: "general", status: "ok", port: 40101, type: "butler" }], meta: {} },
    isLoading: false, isError: false, error: null, refetch: vi.fn(),
  } as AnyMock);
  vi.mocked(useCostSummary).mockReturnValue({
    data: { data: { total_cost_usd: 0.42 }, meta: {} },
    isLoading: false, isError: false, error: null,
  } as AnyMock);
  vi.mocked(useSessions).mockReturnValue({
    data: { data: [], meta: { total: 3 } },
    isLoading: false, isError: false, error: null,
  } as AnyMock);
  vi.mocked(useIssues).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false, isError: false, error: null,
  } as AnyMock);
  vi.mocked(useNotifications).mockReturnValue({
    data: { data: [], meta: { total: 0 } },
    isLoading: false, isError: false, error: null,
  } as AnyMock);
  vi.mocked(useApprovalMetrics).mockReturnValue({
    data: { data: { total_pending: 0 }, meta: {} },
    isLoading: false, isError: false, error: null,
  } as AnyMock);
  vi.mocked(useQaSummary).mockReturnValue({
    data: { data: { last_patrol: null, stats_24h: { patrols_completed: 0, total_findings: 0, novel_findings: 0, dispatched_investigations: 0 } }, meta: {} },
    isLoading: false, isError: false, error: null,
  } as AnyMock);
}

function renderPage(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("DashboardPage -- information hierarchy", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setAllSuccess();
  });

  it("renders the Sessions (hero) region", () => {
    const html = renderPage();
    expect(html).toContain("Sessions");
  });

  it("renders the Recent Activity (secondary) region", () => {
    const html = renderPage();
    expect(html).toContain("Recent Activity");
  });

  it("does NOT render Ecosystem Topology on the home page", () => {
    // TopologyGraph has been moved to /system per bu-2okpr.5
    const html = renderPage();
    expect(html).not.toContain("Ecosystem Topology");
  });
});

describe("DashboardPage -- stat strip", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setAllSuccess();
  });

  it("renders sessions today stat", () => {
    const html = renderPage();
    expect(html).toContain("sessions today");
  });

  it("renders estimated cost today stat", () => {
    const html = renderPage();
    expect(html).toContain("est. cost today");
  });

  it("renders pending approvals stat", () => {
    const html = renderPage();
    expect(html).toContain("pending approvals");
  });
});

describe("DashboardPage -- loading state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setAllLoading();
  });

  it("renders stat strip skeleton while butlers are loading", () => {
    const html = renderPage();
    expect(html).toContain('aria-label="Loading stats"');
  });
});
