/**
 * ButlerOverviewTab — cost card and recent sessions card regression tests.
 *
 * Pins the contracts for both cards:
 *   Cost card:
 *     1. Renders "Today" and "Last 7d" labels with monospace values.
 *     2. Shows "$0.00" when the butler has no spend in the period.
 *     3. Shows "No cost data" when both periods return no data.
 *     4. Shows a skeleton when isLoading.
 *
 *   Recent sessions card:
 *     1. Renders last 5 sessions with prompt + status badge.
 *     2. Shows "No sessions yet" when the list is empty.
 *     3. Shows a skeleton when isLoading.
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

vi.mock("@/hooks/use-costs", () => ({
  useCostSummary: vi.fn(),
}));

vi.mock("@/hooks/use-notifications", () => ({
  useButlerNotifications: vi.fn(() => ({ data: null, isLoading: false })),
}));

vi.mock("@/hooks/use-sessions", () => ({
  useButlerSessions: vi.fn(),
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
import { useCostSummary } from "@/hooks/use-costs";
import { useButlerSessions } from "@/hooks/use-sessions";
import type { ButlerDetail, SessionSummary } from "@/api/types";

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

const SESSION_SUCCESS: SessionSummary = {
  id: "session-1",
  butler: "general",
  prompt: "Check my email",
  trigger_source: "scheduler",
  success: true,
  started_at: "2026-05-10T08:00:00Z",
  completed_at: "2026-05-10T08:01:30Z",
  duration_ms: 90000,
  input_tokens: 1200,
  output_tokens: 400,
};

const SESSION_FAILED: SessionSummary = {
  id: "session-2",
  butler: "general",
  prompt: "Send weekly report",
  trigger_source: "manual",
  success: false,
  started_at: "2026-05-10T09:15:00Z",
  completed_at: "2026-05-10T09:15:45Z",
  duration_ms: 45000,
  input_tokens: 800,
  output_tokens: 100,
};

const SESSION_RUNNING: SessionSummary = {
  id: "session-3",
  butler: "general",
  prompt: "Process new messages",
  trigger_source: "scheduler",
  success: null,
  started_at: "2026-05-10T10:00:00Z",
  completed_at: null,
  duration_ms: null,
  input_tokens: null,
  output_tokens: null,
};

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
  costSummary7d = makeCostSummary(0.35),
  costLoading = false,
  sessions = [SESSION_SUCCESS],
  sessionsLoading = false,
}: {
  costSummary24h?: CostSummaryData | null;
  costSummary7d?: CostSummaryData | null;
  costLoading?: boolean;
  sessions?: SessionSummary[];
  sessionsLoading?: boolean;
} = {}) {
  vi.mocked(useButler).mockReturnValue({
    data: { data: BASE_BUTLER, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn().mockResolvedValue(undefined),
  } as ReturnType<typeof useButler>);

  // useCostSummary is called twice: once with "today" and once with "7d".
  // Use mockImplementation to discriminate by argument instead of relying on
  // call-order queues, which are consumed across test invocations.
  vi.mocked(useCostSummary).mockImplementation((period) => {
    const summary = period === "7d" ? costSummary7d : costSummary24h;
    return {
      data: summary ? { data: summary, meta: {} } : undefined,
      isLoading: costLoading,
    } as ReturnType<typeof useCostSummary>;
  });

  vi.mocked(useButlerSessions).mockReturnValue({
    data: { data: sessions, meta: { total: sessions.length, offset: 0, limit: 5 } },
    isLoading: sessionsLoading,
    isError: false,
    error: null,
  } as ReturnType<typeof useButlerSessions>);
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
// Cost card tests
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab — cost card", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders 'Today' and 'Last 7d' labels", () => {
    setupDefaultMocks();
    const html = renderTab();
    expect(html).toContain("Today");
    expect(html).toContain("Last 7d");
  });

  it("renders today cost in monospace with dollar sign", () => {
    setupDefaultMocks({ costSummary24h: makeCostSummary(0.05) });
    const html = renderTab();
    // $0.05 should appear with monospace class (font-mono)
    expect(html).toContain("$0.05");
    expect(html).toContain("font-mono");
  });

  it("renders 7d cost value", () => {
    setupDefaultMocks({ costSummary7d: makeCostSummary(0.35) });
    const html = renderTab();
    expect(html).toContain("$0.35");
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
      costSummary7d: {
        total_cost_usd: 5.0,
        total_sessions: 20,
        total_input_tokens: 20000,
        total_output_tokens: 8000,
        by_butler: { "other-butler": 5.0 },
        by_model: {},
      },
    });
    const html = renderTab();
    // Butler "general" has no entry — should show $0.00
    expect(html).toContain("$0.00");
  });

  it("renders 'No cost data' when both cost summaries are unavailable", () => {
    setupDefaultMocks({ costSummary24h: null, costSummary7d: null });
    const html = renderTab();
    expect(html).toContain("No cost data");
  });

  it("renders loading skeleton when cost is loading", () => {
    setupDefaultMocks({ costLoading: true });
    const html = renderTab();
    // In loading state the card title is still present but no data rows
    expect(html).not.toContain("Last 24h");
    expect(html).not.toContain("Last 7d");
    // There should be skeleton elements (class="...animate-pulse...")
    expect(html.toLowerCase()).toContain("skeleton");
  });

  it("renders the cost card aria-label", () => {
    setupDefaultMocks();
    const html = renderTab();
    expect(html).toContain('aria-label="Cost summary"');
  });

  it("renders global total and percentage share when butler is a fraction of total", () => {
    // Butler spent $0.05 out of a $0.20 global total → 25.0%
    setupDefaultMocks({
      costSummary24h: makeCostSummary(0.05, "general", 0.20),
    });
    const html = renderTab();
    expect(html).toContain("Share (today)");
    expect(html).toContain("$0.05");
    expect(html).toContain("$0.20");
    expect(html).toContain("25.0%");
  });

  it("renders share row when butler is sole contributor (100%)", () => {
    // Butler spent $0.10, global total is also $0.10 → 100.0%
    setupDefaultMocks({
      costSummary24h: makeCostSummary(0.10, "general", 0.10),
    });
    const html = renderTab();
    expect(html).toContain("100.0%");
  });

  it("omits share row when global total is zero", () => {
    // Global total is zero: division guard should suppress the share row
    setupDefaultMocks({
      costSummary24h: {
        total_cost_usd: 0,
        total_sessions: 0,
        total_input_tokens: 0,
        total_output_tokens: 0,
        by_butler: {},
        by_model: {},
      },
    });
    const html = renderTab();
    expect(html).not.toContain("Share (today)");
  });

  it("omits share row when cost data is unavailable", () => {
    setupDefaultMocks({ costSummary24h: null, costSummary7d: null });
    const html = renderTab();
    expect(html).not.toContain("Share (today)");
  });
});

// ---------------------------------------------------------------------------
// Recent sessions card tests
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab — recent sessions card", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders the recent sessions card heading", () => {
    setupDefaultMocks({ sessions: [SESSION_SUCCESS] });
    const html = renderTab();
    expect(html).toContain("Recent Sessions");
  });

  it("renders session prompt text", () => {
    setupDefaultMocks({ sessions: [SESSION_SUCCESS] });
    const html = renderTab();
    expect(html).toContain("Check my email");
  });

  it("renders 'Success' badge for a successful session", () => {
    setupDefaultMocks({ sessions: [SESSION_SUCCESS] });
    const html = renderTab();
    expect(html).toContain("Success");
  });

  it("renders 'Failed' badge for a failed session", () => {
    setupDefaultMocks({ sessions: [SESSION_FAILED] });
    const html = renderTab();
    expect(html).toContain("Failed");
  });

  it("renders 'Running' badge for an in-progress session", () => {
    setupDefaultMocks({ sessions: [SESSION_RUNNING] });
    const html = renderTab();
    expect(html).toContain("Running");
  });

  it("renders all five sessions when five are provided", () => {
    const fiveSessions: SessionSummary[] = Array.from({ length: 5 }, (_, i) => ({
      id: `session-${i + 1}`,
      butler: "general",
      prompt: `Task number ${i + 1}`,
      trigger_source: "scheduler",
      success: true,
      started_at: "2026-05-10T08:00:00Z",
      completed_at: "2026-05-10T08:01:00Z",
      duration_ms: 60000,
      input_tokens: 500,
      output_tokens: 100,
    }));
    setupDefaultMocks({ sessions: fiveSessions });
    const html = renderTab();
    for (let i = 1; i <= 5; i++) {
      expect(html).toContain(`Task number ${i}`);
    }
  });

  it("renders 'No sessions yet' when the sessions list is empty", () => {
    setupDefaultMocks({ sessions: [] });
    const html = renderTab();
    expect(html).toContain("No sessions yet");
  });

  it("renders loading skeletons when sessions are loading", () => {
    setupDefaultMocks({ sessionsLoading: true });
    const html = renderTab();
    // In loading state the prompt text should not appear
    expect(html).not.toContain("Check my email");
    // Skeleton elements are present
    expect(html.toLowerCase()).toContain("skeleton");
  });

  it("renders the sessions list aria-label when sessions exist", () => {
    setupDefaultMocks({ sessions: [SESSION_SUCCESS] });
    const html = renderTab();
    expect(html).toContain('aria-label="sessions list"');
  });

  it("renders a 'View all' link to the butler sessions page", () => {
    setupDefaultMocks({ sessions: [SESSION_SUCCESS] });
    const html = renderTab();
    expect(html).toContain("/butlers/general/sessions");
    expect(html).toContain("View all");
  });

  it("renders the session card aria-label", () => {
    setupDefaultMocks({ sessions: [SESSION_SUCCESS] });
    const html = renderTab();
    expect(html).toContain('aria-label="Recent sessions"');
  });

  it("renders the started_at timestamp via Time component", () => {
    setupDefaultMocks({ sessions: [SESSION_SUCCESS] });
    const html = renderTab();
    // The stubbed Time component renders the raw ISO value as dateTime attr
    expect(html).toContain("2026-05-10T08:00:00Z");
  });
});
