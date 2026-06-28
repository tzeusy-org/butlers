/**
 * Tests for DashboardPage (editorial archetype, bu-1fpvp.2 / bu-bm58r.1).
 *
 * Verifies the editorial-archetype layout:
 * - Briefing surface: DateEyebrow, BriefingStatus pill, Headline, Elaboration
 * - AttentionList with items and empty-state fallback
 * - RuntimeSummaryKpi cells (total / healthy / sessions_24h / pending approvals)
 * - ButlerIndex rows
 * - OperationsNowList (pending approvals, QA state, notifications, recent activity)
 * - Five state_class values render without crashing
 *
 * Prior test contracts (Vertical-D hero/secondary regions) are replaced by
 * the editorial layout.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import DashboardPage from "@/pages/DashboardPage";

// ---------------------------------------------------------------------------
// Mock all hooks used by DashboardPage (and RuntimeSummaryKpi)
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-briefing", () => ({ useBriefing: vi.fn() }));
vi.mock("@/hooks/use-butlers", () => ({ useButlers: vi.fn() }));
vi.mock("@/hooks/use-spend", () => ({ useSpendSummary: vi.fn(), useTopSessions: vi.fn() }));
vi.mock("@/hooks/use-issues", () => ({ useIssues: vi.fn() }));
vi.mock("@/hooks/use-approvals", () => ({ useApprovalMetrics: vi.fn() }));
vi.mock("@/hooks/use-system", () => ({ useButlerHeartbeats: vi.fn() }));
vi.mock("@/hooks/use-notifications", () => ({ useNotificationStats: vi.fn() }));
vi.mock("@/hooks/use-qa", () => ({ useQaSummary: vi.fn() }));
vi.mock("@/hooks/use-timeline", () => ({ useTimeline: vi.fn() }));

// ---------------------------------------------------------------------------
// Imports after mocks are registered
// ---------------------------------------------------------------------------

import { useBriefing } from "@/hooks/use-briefing";
import { useButlers } from "@/hooks/use-butlers";
import { useSpendSummary, useTopSessions } from "@/hooks/use-spend";
import { useIssues } from "@/hooks/use-issues";
import { useApprovalMetrics } from "@/hooks/use-approvals";
import { useButlerHeartbeats } from "@/hooks/use-system";
import { useNotificationStats } from "@/hooks/use-notifications";
import { useQaSummary } from "@/hooks/use-qa";
import { useTimeline } from "@/hooks/use-timeline";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

/** A briefing for a given state_class. */
function makeBriefing(
  stateClass: string,
  source: "llm" | "fallback" = "llm",
  headline = "Everything is in hand.",
) {
  return {
    data: {
      greet: "Good morning.",
      headline,
      elaboration: "The system is operating normally.",
      source,
      state_class: stateClass,
      generated_at: new Date().toISOString(),
    },
    isFetching: false,
    refetch: vi.fn(),
  };
}

function setDefaultData(stateClass = "quiet", headline = "Everything is in hand.") {
  vi.mocked(useBriefing).mockReturnValue(makeBriefing(stateClass, "llm", headline) as AnyMock);
  vi.mocked(useButlers).mockReturnValue({
    data: {
      data: [
        { name: "general", status: "ok", port: 40101, type: "butler", sessions_24h: 3 },
        {
          name: "health",
          status: "ok",
          port: 40102,
          type: "butler",
          sessions_24h: 2,
          last_session_started_at: null,
        },
      ],
      meta: {},
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as AnyMock);
  vi.mocked(useSpendSummary).mockReturnValue({
    data: {
      data: {
        total_cost_usd: 0.42,
        total_sessions: 5,
        total_input_tokens: 1000,
        total_output_tokens: 500,
        by_butler: { general: 0.30, health: 0.12 },
        by_model: {},
      },
      meta: {},
    },
    isLoading: false,
    isError: false,
    error: null,
  } as AnyMock);
  vi.mocked(useTopSessions).mockReturnValue({
    data: {
      data: [
        {
          session_id: "s1",
          butler: "health",
          cost_usd: 0.31,
          input_tokens: 50_000,
          output_tokens: 12_000,
          model: "claude-opus-4",
          started_at: "2026-05-14T11:50:00.000Z",
        },
      ],
      meta: {},
    },
    isLoading: false,
    isError: false,
    error: null,
  } as AnyMock);
  vi.mocked(useIssues).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
    isError: false,
    error: null,
  } as AnyMock);
  vi.mocked(useApprovalMetrics).mockReturnValue({
    data: { data: { total_pending: 0 }, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
  } as AnyMock);
  vi.mocked(useButlerHeartbeats).mockReturnValue({
    data: {
      data: {
        butlers: [
          {
            name: "general",
            last_heartbeat_at: "2026-05-14T11:59:00.000Z",
            last_session_at: "2026-05-14T11:55:00.000Z",
            active_session_count: 1,
            heartbeat_age_seconds: 30,
          },
          {
            name: "health",
            last_heartbeat_at: "2026-05-14T11:40:00.000Z",
            last_session_at: "2026-05-14T11:30:00.000Z",
            active_session_count: 0,
            heartbeat_age_seconds: 1_200,
          },
        ],
      },
      meta: {},
    },
    isLoading: false,
    isError: false,
    error: null,
  } as AnyMock);
  vi.mocked(useNotificationStats).mockReturnValue({
    data: { data: { total: 0, sent: 0, failed: 0, by_channel: {}, by_butler: {} }, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
  } as AnyMock);
  vi.mocked(useQaSummary).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
  } as AnyMock);
  vi.mocked(useTimeline).mockReturnValue({
    data: { data: [], meta: { cursor: null, has_more: false } },
    isLoading: false,
    isError: false,
    error: null,
  } as AnyMock);
}

function renderPage({ basename = "" }: { basename?: string } = {}): string {
  const queryClient = new QueryClient();
  const initialEntries = basename ? [`${basename}/`] : ["/"];
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter basename={basename} initialEntries={initialEntries}>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Briefing surface
// ---------------------------------------------------------------------------

describe("DashboardPage -- briefing surface", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setDefaultData();
  });

  it("renders the greet line", () => {
    const html = renderPage();
    expect(html).toContain("Good morning.");
  });

  it("renders the headline", () => {
    const html = renderPage();
    expect(html).toContain("Everything is in hand.");
  });

  it("renders the elaboration paragraph", () => {
    const html = renderPage();
    expect(html).toContain("The system is operating normally.");
  });

  it("renders a BriefingStatus pill", () => {
    const html = renderPage();
    // BriefingStatus renders an aria-label containing "Briefing status"
    expect(html).toContain("Briefing status");
  });

  it("renders llm status label when source is llm", () => {
    const html = renderPage();
    expect(html).toContain("llm");
  });
});

// ---------------------------------------------------------------------------
// Fallback path (source === "fallback", i.e. templated)
// ---------------------------------------------------------------------------

describe("DashboardPage -- fallback / templated path", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setDefaultData();
    // Override briefing source to fallback
    vi.mocked(useBriefing).mockReturnValue(
      makeBriefing("quiet", "fallback", "Everything is in hand.") as AnyMock,
    );
  });

  it("renders 'templated' in the status pill when source is fallback", () => {
    const html = renderPage();
    expect(html).toContain("templated");
  });
});

// ---------------------------------------------------------------------------
// Composing state (isFetching)
// ---------------------------------------------------------------------------

describe("DashboardPage -- composing state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setDefaultData();
    vi.mocked(useBriefing).mockReturnValue({
      ...makeBriefing("quiet"),
      isFetching: true,
    } as AnyMock);
  });

  it("renders 'composing' label while isFetching", () => {
    const html = renderPage();
    expect(html).toContain("composing");
  });
});

// ---------------------------------------------------------------------------
// Five state_class values render without errors
// ---------------------------------------------------------------------------

describe("DashboardPage -- state_class variants", () => {
  const STATE_CLASSES: Array<{ stateClass: string; headline: string }> = [
    { stateClass: "quiet", headline: "Everything is in hand." },
    { stateClass: "mild", headline: "Things are quiet, with 1 exception." },
    { stateClass: "busy", headline: "Things are busy with 5 items waiting." },
    { stateClass: "degraded-quiet", headline: "Quiet, but 1 butler is degraded." },
    { stateClass: "urgent", headline: "One thing needs you now." },
  ];

  for (const { stateClass, headline } of STATE_CLASSES) {
    it(`renders state_class="${stateClass}" without errors`, () => {
      vi.resetAllMocks();
      setDefaultData(stateClass, headline);
      const html = renderPage();
      expect(html).toContain(headline);
    });
  }
});

// ---------------------------------------------------------------------------
// AttentionList
// ---------------------------------------------------------------------------

describe("DashboardPage -- AttentionList", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-14T12:00:00.000Z"));
    vi.resetAllMocks();
    setDefaultData();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders 'Nothing waiting.' when there are no current attention rows", () => {
    vi.mocked(useButlerHeartbeats).mockReturnValue({
      data: {
        data: {
          butlers: [
            {
              name: "general",
              last_heartbeat_at: "2026-05-14T11:59:00.000Z",
              last_session_at: "2026-05-14T11:55:00.000Z",
              active_session_count: 1,
              heartbeat_age_seconds: 30,
            },
            {
              name: "health",
              last_heartbeat_at: "2026-05-14T11:59:00.000Z",
              last_session_at: "2026-05-14T11:30:00.000Z",
              active_session_count: 0,
              heartbeat_age_seconds: 30,
            },
          ],
        },
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);
    const html = renderPage();
    expect(html).toContain("Nothing waiting.");
  });

  it("renders issue descriptions when issues are present", () => {
    vi.mocked(useIssues).mockReturnValue({
      data: {
        data: [
          {
            severity: "high",
            type: "error",
            butler: "general",
            description: "Session failed unexpectedly.",
            link: null,
            first_seen_at: "2026-05-14T10:00:00.000Z",
            last_seen_at: "2026-05-14T11:00:00.000Z",
            occurrences: 1,
          },
        ],
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);
    const html = renderPage();
    expect(html).toContain("Session failed unexpectedly.");
  });

  it("renders capped recency-aware issue rows and summarizes old groups under the router basename", () => {
    vi.mocked(useIssues).mockReturnValue({
      data: {
        data: [
          {
            severity: "high",
            type: "session",
            butler: "general",
            butlers: ["general", "health"],
            description: "Current grouped failure.",
            link: "/issues?group=current",
            first_seen_at: "2026-05-14T09:00:00.000Z",
            last_seen_at: "2026-05-14T11:00:00.000Z",
            occurrences: 2,
          },
          {
            severity: "medium",
            type: "audit",
            butler: "finance",
            description: "Old audit group.",
            link: "/issues?group=old",
            first_seen_at: "2026-05-12T09:00:00.000Z",
            last_seen_at: "2026-05-12T11:00:00.000Z",
            occurrences: 5,
          },
        ],
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);

    const html = renderPage({ basename: "/butlers-dev" });
    expect(html).toContain("Current grouped failure.");
    expect(html).toContain("general and health");
    expect(html).toContain("2 occurrences");
    expect(html).toContain("last seen 1h ago");
    expect(html).toContain('href="/butlers-dev/issues?group=current"');
    expect(html).toContain("1 older issue group");
    expect(html).toContain('href="/butlers-dev/issues"');
    expect(html).not.toContain("Old audit group.");
  });
});

// ---------------------------------------------------------------------------
// RuntimeSummaryKpi strip (bu-bm58r.1)
// ---------------------------------------------------------------------------

describe("DashboardPage -- RuntimeSummaryKpi", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setDefaultData();
  });

  it("renders Total butlers KPI cell", () => {
    const html = renderPage();
    expect(html).toContain("Total butlers");
    expect(html).toContain(">2<");
  });

  it("renders Healthy KPI cell", () => {
    const html = renderPage();
    expect(html).toContain("Healthy");
    expect(html).toContain(">2<");
  });

  it("renders Sessions · 24h KPI cell", () => {
    const html = renderPage();
    expect(html).toContain("Sessions");
  });

  it("renders Pending approvals KPI cell", () => {
    const html = renderPage();
    expect(html).toContain("Pending approvals");
  });

  it("renders stale heartbeat attention outside the KPI strip", () => {
    const html = renderPage();
    expect(html).toContain("Needs attention");
    expect(html).toContain("health heartbeat is stale");
  });
});

// ---------------------------------------------------------------------------
// ButlerIndex
// ---------------------------------------------------------------------------

describe("DashboardPage -- ButlerIndex", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setDefaultData();
  });

  it("renders butler names in the index", () => {
    const html = renderPage();
    expect(html).toContain("general");
    expect(html).toContain("health");
  });

  it("renders session count and heartbeat-derived last activity metadata", () => {
    const html = renderPage();
    expect(html).toContain("active");
    expect(html).toContain("last");
  });

  it("renders the Operations section eyebrow", () => {
    const html = renderPage();
    // The eyebrow text is "Operations" in HTML; CSS text-transform uppercase
    // applies visually but does not change the serialized string.
    expect(html).toContain("Operations");
  });

  it("renders the Now section eyebrow", () => {
    const html = renderPage();
    expect(html).toContain("Now");
  });
});

// ---------------------------------------------------------------------------
// Cost surface — CostWidget + TopSessionsTable (bu-6o2eu)
//
// Spec dashboard-domain-pages requires the overview to mount the CostWidget
// ("Cost Today" aggregate) and the TopSessionsTable ("Most Expensive
// Sessions"). Both were previously orphaned (imported by no page).
// ---------------------------------------------------------------------------

describe("DashboardPage -- cost surface", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setDefaultData();
  });

  it("mounts the CostWidget with the aggregate cost-today total", () => {
    const html = renderPage();
    expect(html).toContain("Cost Today");
    // total_cost_usd 0.42 -> "$0.42"
    expect(html).toContain("$0.42");
  });

  it("shows the most-expensive butler derived from the by_butler breakdown", () => {
    const html = renderPage();
    // by_butler { general: 0.30, health: 0.12 } -> top is general at $0.30
    expect(html).toContain("Top: general");
    expect(html).toContain("$0.30");
  });

  it("mounts the TopSessionsTable with formatted token counts", () => {
    const html = renderPage();
    expect(html).toContain("Most Expensive Sessions");
    // 50_000 / 12_000 input/output tokens -> "50.0K / 12.0K"
    expect(html).toContain("50.0K");
    expect(html).toContain("12.0K");
  });
});

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------

describe("DashboardPage -- loading state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setDefaultData();
    // Briefing not yet loaded
    vi.mocked(useBriefing).mockReturnValue({
      data: undefined,
      isFetching: true,
      refetch: vi.fn(),
    } as AnyMock);
  });

  it("renders default fallback headline when briefing is loading", () => {
    const html = renderPage();
    // Falls back to "Checking in."
    expect(html).toContain("Checking in.");
  });
});

// ---------------------------------------------------------------------------
// OperationsNowList
// ---------------------------------------------------------------------------

describe("DashboardPage -- OperationsNowList", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setDefaultData();
  });

  it("renders 'Nothing scheduled.' when no now signals are active", () => {
    const html = renderPage();
    expect(html).toContain("Nothing scheduled.");
  });

  it("renders pending approvals row when approvals are pending", () => {
    vi.mocked(useApprovalMetrics).mockReturnValue({
      data: { data: { total_pending: 2 }, meta: {} },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);
    const html = renderPage();
    expect(html).toContain("2 pending approvals");
    expect(html).toContain('href="/approvals"');
  });

  it("renders failed notification row when notifications have failures", () => {
    vi.mocked(useNotificationStats).mockReturnValue({
      data: {
        data: { total: 5, sent: 4, failed: 1, by_channel: {}, by_butler: {} },
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);
    const html = renderPage();
    expect(html).toContain("1 failed notification");
    expect(html).toContain('href="/notifications"');
  });

  it("renders QA row when patrol fails", () => {
    vi.mocked(useQaSummary).mockReturnValue({
      data: {
        data: {
          staffer_status: "healthy",
          last_patrol_at: null,
          next_patrol_at: null,
          last_patrol: {
            id: "p1",
            started_at: "2026-05-14T11:00:00.000Z",
            completed_at: "2026-05-14T11:01:00.000Z",
            status: "failed",
            findings_count: 0,
            novel_count: 0,
            dispatched_count: 0,
            log_lookback_minutes: 60,
            sources_polled: [],
            error_detail: "scanner failed",
          },
          stats_24h: {
            patrols_completed: 0,
            total_findings: 0,
            novel_findings: 0,
            dispatched_investigations: 0,
            prs_opened: 0,
          },
          stats_all_time: {
            total_patrols: 1,
            total_findings: 0,
            novel_findings: 0,
            dispatched_investigations: 0,
            prs_merged: 0,
            prs_failed: 0,
            success_rate: 0,
          },
          kpis: {
            prs_landed_24h: 0,
            mttr_24h_seconds: null,
            self_resolved_7d_pct: 0,
            active_cases_now: 0,
          },
          active_breakdown: { awaiting_ci: 0, escalated_open_cases: 0 },
          active_sources: [],
          circuit_breaker: { tripped: false, consecutive_failures: 0 },
          credentials_status: { gh_token_present: null, git_author_name_present: null, git_author_email_present: null, provisioning_hint: null },
          port: null,
          model: null,
          patrol_interval_minutes: null,
        },
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);
    const html = renderPage();
    expect(html).toContain("QA patrol failed");
    expect(html).toContain('href="/qa"');
  });

  it("renders recent timeline activity rows", () => {
    vi.mocked(useTimeline).mockReturnValue({
      data: {
        data: [
          {
            id: "evt-1",
            type: "session",
            butler: "general",
            timestamp: "2026-05-14T11:55:00.000Z",
            summary: "general ran health check",
            data: {},
          },
        ],
        meta: { cursor: null, has_more: false },
      },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);
    const html = renderPage();
    expect(html).toContain("general ran health check");
    expect(html).toContain('href="/timeline"');
  });

  it("renders a named 'QA status: unavailable' error row when qaSummary query fails", () => {
    vi.mocked(useQaSummary).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Network error"),
    } as AnyMock);
    const html = renderPage();
    expect(html).toContain("QA status: unavailable");
    expect(html).toContain('href="/qa"');
  });

  it("renders a named 'Notification status: unavailable' error row when notificationStats query fails", () => {
    vi.mocked(useNotificationStats).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Network error"),
    } as AnyMock);
    const html = renderPage();
    expect(html).toContain("Notification status: unavailable");
    expect(html).toContain('href="/notifications"');
  });

  it("renders a named 'Timeline: unavailable' error row when timeline query fails", () => {
    vi.mocked(useTimeline).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Network error"),
    } as AnyMock);
    const html = renderPage();
    expect(html).toContain("Timeline: unavailable");
    expect(html).toContain('href="/timeline"');
  });
});

// ---------------------------------------------------------------------------
// Butler-health source failure (bu-k5d8c)
//
// Regression guard: a failing GET /api/butlers must NOT render as a serene,
// healthy-looking empty page ("No butlers active."). It must surface a
// degraded state — both in the ButlerIndex empty slot and as a named Now
// error row — mirroring how the sibling sources surface their failures.
// ---------------------------------------------------------------------------

describe("DashboardPage -- butler-health source failure", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setDefaultData();
    // Heartbeats also depend on the same dead source in practice; clear them
    // so the page genuinely has no butler rows to fall back on.
    vi.mocked(useButlerHeartbeats).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Network error"),
    } as AnyMock);
    vi.mocked(useButlers).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Network error"),
    } as AnyMock);
  });

  it("does NOT render the healthy-looking 'No butlers active.' empty state", () => {
    const html = renderPage();
    expect(html).not.toContain("No butlers active.");
  });

  it("surfaces a degraded 'Butler health source unavailable.' state in the index", () => {
    const html = renderPage();
    expect(html).toContain("Butler health source unavailable.");
  });

  it("renders a named 'Butler health: unavailable' Now error row when butlers query fails", () => {
    const html = renderPage();
    expect(html).toContain("Butler health: unavailable");
    expect(html).toContain('href="/system"');
  });
});
