/**
 * ButlerOverviewTab — spend and recent activity panel regression tests.
 *
 * Pins the contracts for current overview grid panels:
 *   Spend:
 *     1. Renders today's spend with monospace values.
 *     2. Shows "$0.00" when the butler has no spend today.
 *     3. Shows a skeleton when the cost source is loading.
 *
 *   Recent activity:
 *     1. Renders recent activity events with summary + kind.
 *     2. Shows "no recent events" when the event list is empty.
 *     3. Shows a skeleton while activity is loading.
 *
 * Bead: bu-8hbph.4
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerOverviewTab from "@/components/butler-detail/ButlerOverviewTab";

// ---------------------------------------------------------------------------
// Mock all hooks consumed by ButlerOverviewTab
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-butlers", () => ({
  useButler: vi.fn(),
  useButlerModules: vi.fn(() => ({ data: { data: [], meta: {} }, isLoading: false, isError: false, error: null })),
}));

vi.mock("@/hooks/use-butler-status-board", () => ({
  useButlerStatusBoard: vi.fn(),
}));

vi.mock("@/hooks/use-approvals", () => ({
  useApprovalActions: vi.fn(() => ({ data: { data: [], meta: {} }, isLoading: false, isError: false })),
}));

vi.mock("@/hooks/use-system", () => ({
  useButlerHeartbeats: vi.fn(() => ({
    data: { data: { butlers: [] }, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
  })),
}));

vi.mock("@/hooks/use-general", () => ({
  useRegistry: vi.fn(() => ({ data: null, isLoading: false })),
  useSetEligibility: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("@/hooks/use-spend", () => ({
  useSpendSummary: vi.fn(),
}));

vi.mock("@/hooks/use-notifications", () => ({
  useButlerNotifications: vi.fn(() => ({ data: null, isLoading: false })),
}));

vi.mock("@/hooks/use-sessions", () => ({
  useButlerSessions: vi.fn(),
}));

vi.mock("@/hooks/use-butler-analytics", () => ({
  useButlerActivityFeed: vi.fn(),
}));

// Stub heavy child components not under test here.
vi.mock("@/components/notifications/notification-feed", () => ({
  NotificationFeed: () => <div data-testid="notification-feed" />,
}));

vi.mock("@/components/butler-detail/EligibilityTimeline", () => ({
  default: () => <div data-testid="eligibility-timeline" />,
}));

// Time renders the raw ISO string as the datetime attribute; stub it to keep
// SSR output deterministic without timezone context.
vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => <time dateTime={value}>{value}</time>,
}));

import { useButler } from "@/hooks/use-butlers";
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board";
import { useSpendSummary } from "@/hooks/use-spend";
import { useButlerActivityFeed } from "@/hooks/use-butler-analytics";
import type { ActivityFeed, ButlerDetail } from "@/api/types";

// ---------------------------------------------------------------------------
// Shared fixture data
// ---------------------------------------------------------------------------

const BASE_BUTLER: ButlerDetail = {
  name: "general",
  status: "ok",
  port: 8001,
  type: "butler",
  sessions_24h: 3,
  modules: [],
  schedules: [],
  skills: [],
  process_facts: null,
};

const ACTIVITY_EVENTS: ActivityFeed["events"] = [
  {
    ts: "2026-05-10T08:00:00Z",
    event_type: "session_completed",
    summary: "Completed inbox triage",
    entity_id: "session-1",
    metadata: {},
  },
  {
    ts: "2026-05-10T09:15:00Z",
    event_type: "memory_write",
    summary: "Stored one useful memory",
    entity_id: "memory-1",
    metadata: {},
  },
];

// ---------------------------------------------------------------------------
// Setup helpers
// ---------------------------------------------------------------------------

type CostSummaryData = {
  total_cost_usd: number;
  total_sessions: number;
  total_input_tokens: number;
  total_output_tokens: number;
  by_butler: Record<string, number>;
  by_model: Record<string, number>;
};

function makeCostSummary(
  butlerCost: number,
  butlerName = "general",
  globalTotal?: number,
): CostSummaryData {
  return {
    total_cost_usd: globalTotal ?? butlerCost,
    total_sessions: 1,
    total_input_tokens: 1000,
    total_output_tokens: 300,
    by_butler: { [butlerName]: butlerCost },
    by_model: {},
  };
}

function setupDefaultMocks({
  costSummary24h = makeCostSummary(0.05),
  costLoading = false,
  sessions24h = 3,
  activityEvents = ACTIVITY_EVENTS,
  activityLoading = false,
}: {
  costSummary24h?: CostSummaryData | null;
  costLoading?: boolean;
  sessions24h?: number;
  activityEvents?: ActivityFeed["events"];
  activityLoading?: boolean;
} = {}) {
  vi.mocked(useButler).mockReturnValue({
    data: { data: BASE_BUTLER, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn().mockResolvedValue(undefined),
  } as unknown as ReturnType<typeof useButler>);

  vi.mocked(useButlerStatusBoard).mockReturnValue({
    rows: [
      {
        name: "general",
        type: "butler",
        description: null,
        status: "ok",
        activity: "idle",
        cellTone: "neutral",
        eligibility: "active",
        sessions24h,
        costToday: costSummary24h?.by_butler.general ?? 0,
        loadPct: null,
        lastRunISO: "2026-05-10T08:00:00Z",
        hourlyStripe: Array(24).fill(0),
        hourlyTotal: 0,
        hourlyStripeLoading: false,
        hourlyStripeError: false,
        schemaUnreachable: false,
        heartbeatUnavailable: false,
      },
    ],
    aggregates: {
      total: 1,
      butlerCount: 1,
      stafferCount: 0,
      active: 0,
      offline: 0,
      quarantined: 0,
      totalSessions24h: sessions24h,
      totalSpendToday: costSummary24h?.total_cost_usd ?? 0,
      avgLoadPct: null,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      heartbeatSourceError: false,
      registrySourceError: false,
      eligibilityUnavailable: 0,
      hasPerEntryErrors: false,
      sourcesPartiallyDegraded: false,
    },
  });

  vi.mocked(useSpendSummary).mockReturnValue({
    data: costSummary24h ? { data: costSummary24h, meta: {} } : undefined,
    isLoading: costLoading,
  } as ReturnType<typeof useSpendSummary>);

  vi.mocked(useButlerActivityFeed).mockReturnValue({
    data: { events: activityEvents },
    isLoading: activityLoading,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useButlerActivityFeed>);
}

function renderTab(butlerName = "general"): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ButlerOverviewTab butlerName={butlerName} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Spend panel tests
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab — spend panel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the spend panel title and today label", () => {
    setupDefaultMocks();
    const html = renderTab();
    expect(html).toContain("spend");
    expect(html).toContain("today");
  });

  it("renders today cost in monospace with dollar sign", () => {
    setupDefaultMocks({ costSummary24h: makeCostSummary(0.05) });
    const html = renderTab();
    // $0.05 should appear with monospace class (font-mono)
    expect(html).toContain("$0.05");
    expect(html).toContain("font-mono");
  });

  it("renders per-session spend using the status-board session count", () => {
    setupDefaultMocks({ costSummary24h: makeCostSummary(0.35), sessions24h: 7 });
    const html = renderTab();
    expect(html).toContain("$0.35");
    expect(html).toContain("$0.05 / session");
  });

  it("renders '$0.00' when butler has no spend in the period", () => {
    setupDefaultMocks({
      costSummary24h: {
        total_cost_usd: 1.0,
        total_sessions: 5,
        total_input_tokens: 5000,
        total_output_tokens: 2000,
        by_butler: { "other-butler": 1.0 },
        by_model: {},
      },
    });
    const html = renderTab();
    // Butler "general" has no entry — should show $0.00
    expect(html).toContain("$0.00");
  });

  it("renders '$0.00' when cost data is unavailable", () => {
    setupDefaultMocks({ costSummary24h: null });
    const html = renderTab();
    expect(html).toContain("$0.00");
  });

  it("renders loading skeleton when cost is loading", () => {
    setupDefaultMocks({ costLoading: true });
    const html = renderTab();
    expect(html).toContain('data-testid="panel-spend"');
    expect(html).not.toContain("$0.05");
    // There should be skeleton elements (class="...animate-pulse...")
    expect(html.toLowerCase()).toContain("skeleton");
  });

  it("renders the spend panel testid", () => {
    setupDefaultMocks();
    const html = renderTab();
    expect(html).toContain('data-testid="panel-spend"');
  });
});

// ---------------------------------------------------------------------------
// Recent activity panel tests
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab — recent activity panel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the recent activity panel", () => {
    setupDefaultMocks();
    const html = renderTab();
    expect(html).toContain('data-testid="panel-recent"');
    expect(html).toContain("recent");
  });

  it("renders activity summaries", () => {
    setupDefaultMocks();
    const html = renderTab();
    expect(html).toContain("Completed inbox triage");
    expect(html).toContain("Stored one useful memory");
  });

  it("renders event kind labels", () => {
    setupDefaultMocks();
    const html = renderTab();
    expect(html).toContain("session");
    expect(html).toContain("memory");
  });

  it("renders all provided activity events", () => {
    const fiveEvents: ActivityFeed["events"] = Array.from({ length: 5 }, (_, i) => ({
      ts: `2026-05-10T0${i}:00:00Z`,
      event_type: "session_completed",
      summary: `Activity event ${i + 1}`,
      entity_id: `session-${i + 1}`,
      metadata: {},
    }));
    setupDefaultMocks({ activityEvents: fiveEvents });
    const html = renderTab();
    for (let i = 1; i <= 5; i++) {
      expect(html).toContain(`Activity event ${i}`);
    }
  });

  it("renders 'no recent events' when the event list is empty", () => {
    setupDefaultMocks({ activityEvents: [] });
    const html = renderTab();
    expect(html).toContain("no recent events");
  });

  it("renders loading skeletons when recent activity is loading", () => {
    setupDefaultMocks({ activityLoading: true });
    const html = renderTab();
    expect(html).not.toContain("Completed inbox triage");
    // Skeleton elements are present
    expect(html.toLowerCase()).toContain("skeleton");
  });

  it("renders the activity list testid when events exist", () => {
    setupDefaultMocks();
    const html = renderTab();
    expect(html).toContain('data-testid="activity-feed-list"');
  });

  it("renders the activity timestamp via Time component", () => {
    setupDefaultMocks();
    const html = renderTab();
    // The stubbed Time component renders the raw ISO value as dateTime attr.
    expect(html).toContain("2026-05-10T08:00:00Z");
  });
});
