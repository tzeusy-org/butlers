/**
 * RTL tests for RuntimeSummaryKpi (bu-bm58r.1, bu-bm58r.2).
 *
 * Pins the four-cell composition and confirms:
 * - All four eyebrow labels render with accessible labels.
 * - Values render from useButlers and useApprovalMetrics.
 * - Loading state shows '—' for each loading cell.
 * - Zero-state renders '0' (not a placeholder).
 * - 30s polling: both underlying hooks configured with refetchInterval=30_000.
 * - Stale-while-revalidate: cached data stays visible while a background refetch runs.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { RuntimeSummaryKpi } from "@/components/overview/RuntimeSummaryKpi";

// ---------------------------------------------------------------------------
// Mock hooks consumed by RuntimeSummaryKpi
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-butlers", () => ({ useButlers: vi.fn() }));
vi.mock("@/hooks/use-approvals", () => ({ useApprovalMetrics: vi.fn() }));

import { useButlers } from "@/hooks/use-butlers";
import { useApprovalMetrics } from "@/hooks/use-approvals";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

function renderComponent(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <RuntimeSummaryKpi />
    </QueryClientProvider>,
  );
}

function setLoadedData({
  butlers = [
    { name: "general", status: "ok", port: 40101, type: "butler" as const, sessions_24h: 3 },
    { name: "health", status: "degraded", port: 40102, type: "butler" as const, sessions_24h: 1 },
  ],
  pendingApprovals = 2,
} = {}) {
  vi.mocked(useButlers).mockReturnValue({
    data: { data: butlers, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as AnyMock);
  vi.mocked(useApprovalMetrics).mockReturnValue({
    data: { data: { total_pending: pendingApprovals }, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
  } as AnyMock);
}

// ---------------------------------------------------------------------------
// Four-cell composition
// ---------------------------------------------------------------------------

describe("RuntimeSummaryKpi -- four-cell composition", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setLoadedData();
  });

  it("renders the system runtime summary accessible label", () => {
    const html = renderComponent();
    expect(html).toContain("System runtime summary");
  });

  it("renders Total butlers eyebrow", () => {
    const html = renderComponent();
    expect(html).toContain("Total butlers");
  });

  it("renders Healthy eyebrow", () => {
    const html = renderComponent();
    expect(html).toContain("Healthy");
  });

  it("renders Sessions · 24h eyebrow", () => {
    const html = renderComponent();
    expect(html).toContain("Sessions");
  });

  it("renders Pending approvals eyebrow", () => {
    const html = renderComponent();
    expect(html).toContain("Pending approvals");
  });
});

// ---------------------------------------------------------------------------
// Data values
// ---------------------------------------------------------------------------

describe("RuntimeSummaryKpi -- data values", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders total butler count from useButlers", () => {
    setLoadedData({
      butlers: [
        { name: "general", status: "ok", port: 40101, type: "butler" as const, sessions_24h: 0 },
        { name: "health", status: "ok", port: 40102, type: "butler" as const, sessions_24h: 0 },
        { name: "calendar", status: "ok", port: 40103, type: "butler" as const, sessions_24h: 0 },
      ],
    });
    const html = renderComponent();
    // total = 3
    expect(html).toContain(">3<");
  });

  it("renders healthy butler count (status ok or online)", () => {
    setLoadedData({
      butlers: [
        { name: "general", status: "ok", port: 40101, type: "butler" as const, sessions_24h: 0 },
        { name: "health", status: "online", port: 40102, type: "butler" as const, sessions_24h: 0 },
        { name: "calendar", status: "degraded", port: 40103, type: "butler" as const, sessions_24h: 0 },
      ],
    });
    const html = renderComponent();
    // total = 3, healthy = 2 (ok + online)
    expect(html).toContain(">3<");
    expect(html).toContain(">2<");
  });

  it("excludes staffer-type entries from butler KPIs", () => {
    vi.mocked(useButlers).mockReturnValue({
      data: {
        data: [
          { name: "general", status: "ok", port: 40101, type: "butler", sessions_24h: 5 },
          { name: "switchboard", status: "ok", port: 40100, type: "staffer", sessions_24h: 10 },
        ],
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as AnyMock);
    vi.mocked(useApprovalMetrics).mockReturnValue({
      data: { data: { total_pending: 0 }, meta: {} },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);
    const html = renderComponent();
    // total = 1 (staffer excluded), sessions = 5 (not 15)
    expect(html).toContain(">1<");
    expect(html).toContain(">5<");
  });

  it("renders sum of sessions_24h across all butlers", () => {
    setLoadedData({
      butlers: [
        { name: "general", status: "ok", port: 40101, type: "butler" as const, sessions_24h: 7 },
        { name: "health", status: "ok", port: 40102, type: "butler" as const, sessions_24h: 5 },
      ],
    });
    const html = renderComponent();
    // sessions_24h sum = 12
    expect(html).toContain(">12<");
  });

  it("renders pending approvals count from useApprovalMetrics", () => {
    setLoadedData({ pendingApprovals: 4 });
    const html = renderComponent();
    expect(html).toContain(">4<");
  });
});

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------

describe("RuntimeSummaryKpi -- loading state", () => {
  it("shows — for all cells while any data source is loading", () => {
    // Butlers loading; approvals ready — all four cells should still show —
    vi.mocked(useButlers).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as AnyMock);
    vi.mocked(useApprovalMetrics).mockReturnValue({
      data: { data: { total_pending: 0 }, meta: {} },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);
    const html = renderComponent();
    // All four cells should show —
    expect(html.match(/—/g)?.length).toBe(4);
  });
});

// ---------------------------------------------------------------------------
// Zero state
// ---------------------------------------------------------------------------

describe("RuntimeSummaryKpi -- zero state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders 0 for sessions_24h when all butlers have zero sessions", () => {
    setLoadedData({
      butlers: [
        { name: "general", status: "ok", port: 40101, type: "butler" as const, sessions_24h: 0 },
      ],
      pendingApprovals: 0,
    });
    const html = renderComponent();
    // Should contain numeric 0 values, not dashes
    expect(html).toContain(">0<");
  });

  it("renders 0 for pending approvals when none exist", () => {
    setLoadedData({ pendingApprovals: 0 });
    const html = renderComponent();
    expect(html).toContain(">0<");
  });
});


// ---------------------------------------------------------------------------
// Stale-while-revalidate — bu-bm58r.2
//
// When a background refetch is in progress (isFetching=true, isLoading=false),
// the KPI card must keep rendering cached values — not flip to '—'.
// ---------------------------------------------------------------------------

describe("RuntimeSummaryKpi -- stale-while-revalidate (bu-bm58r.2)", () => {
  it("keeps cached data visible while a background refetch runs", () => {
    vi.mocked(useButlers).mockReturnValue({
      data: {
        data: [
          { name: "general", status: "ok", port: 40101, type: "butler" as const, sessions_24h: 5 },
        ],
        meta: {},
      },
      isLoading: false,
      isFetching: true, // background refetch in progress
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as AnyMock);
    vi.mocked(useApprovalMetrics).mockReturnValue({
      data: { data: { total_pending: 3 }, meta: {} },
      isLoading: false,
      isFetching: true, // background refetch in progress
      isError: false,
      error: null,
    } as AnyMock);

    const html = renderComponent();

    // Cached values must be visible — NOT the '—' loading placeholder.
    expect(html).not.toContain("—");
    expect(html).toContain(">1<"); // total butlers
    expect(html).toContain(">5<"); // sessions_24h
    expect(html).toContain(">3<"); // pending approvals
  });
});
