/**
 * Tests for DashboardPage (editorial archetype, bu-1fpvp.2 / bu-bm58r.1).
 *
 * Verifies the editorial-archetype layout:
 * - Briefing surface: DateEyebrow, BriefingStatus pill, Headline, Elaboration
 * - AttentionList with items and empty-state fallback
 * - RuntimeSummaryKpi cells (total / healthy / sessions_24h / pending approvals)
 * - ButlerIndex rows
 * - NextList (pending approvals)
 * - Five state_class values render without crashing
 *
 * Prior test contracts (Vertical-D hero/secondary regions) are replaced by
 * the editorial layout.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import DashboardPage from "@/pages/DashboardPage";

// ---------------------------------------------------------------------------
// Mock all hooks used by DashboardPage (and RuntimeSummaryKpi)
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-briefing", () => ({ useBriefing: vi.fn() }));
vi.mock("@/hooks/use-butlers", () => ({ useButlers: vi.fn() }));
vi.mock("@/hooks/use-costs", () => ({ useCostSummary: vi.fn() }));
vi.mock("@/hooks/use-issues", () => ({ useIssues: vi.fn() }));
vi.mock("@/hooks/use-approvals", () => ({ useApprovalMetrics: vi.fn() }));
vi.mock("@/hooks/use-qa", () => ({ useQaSummary: vi.fn() }));

// ---------------------------------------------------------------------------
// Imports after mocks are registered
// ---------------------------------------------------------------------------

import { useBriefing } from "@/hooks/use-briefing";
import { useButlers } from "@/hooks/use-butlers";
import { useCostSummary } from "@/hooks/use-costs";
import { useIssues } from "@/hooks/use-issues";
import { useApprovalMetrics } from "@/hooks/use-approvals";
import { useQaSummary } from "@/hooks/use-qa";

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

function makeQaSummaryResponse(overrides: Record<string, unknown> = {}) {
  return {
    data: {
      data: {
        staffer_status: "running",
        last_patrol_at: null,
        next_patrol_at: null,
        last_patrol: null,
        stats_24h: {
          patrols: 0,
          findings: 0,
          novel: 0,
          dispatched: 0,
          prs_opened: 0,
        },
        stats_all_time: {
          prs_merged: 0,
          prs_failed: 0,
          success_rate: 1,
        },
        kpis: {
          prs_landed_24h: 0,
          mttr_24h_seconds: null,
          self_resolved_7d_pct: 0,
          active_cases_now: 0,
        },
        active_breakdown: {
          awaiting_ci: 0,
          escalated: 0,
        },
        active_sources: [],
        circuit_breaker: {
          tripped: false,
          consecutive_failures: 0,
        },
        credentials_status: {
          gh_token_present: true,
          provisioning_hint: null,
        },
        ...overrides,
      },
      meta: {},
    },
    isLoading: false,
    isError: false,
    error: null,
  };
}

function setDefaultData(stateClass = "quiet", headline = "Everything is in hand.") {
  vi.mocked(useBriefing).mockReturnValue(makeBriefing(stateClass, "llm", headline) as AnyMock);
  vi.mocked(useButlers).mockReturnValue({
    data: {
      data: [
        { name: "general", status: "ok", port: 40101, type: "butler", sessions_24h: 3 },
        { name: "health", status: "ok", port: 40102, type: "butler", sessions_24h: 2 },
      ],
      meta: {},
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as AnyMock);
  vi.mocked(useCostSummary).mockReturnValue({
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
  vi.mocked(useQaSummary).mockReturnValue(makeQaSummaryResponse() as AnyMock);
}

function renderPage({ basename }: { basename?: string } = {}): string {
  const queryClient = new QueryClient();
  const initialEntries = basename ? [`${basename}/`] : undefined;
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
    vi.resetAllMocks();
    setDefaultData();
  });

  it("renders 'Nothing waiting.' when there are no issues", () => {
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
  });

  it("renders Healthy KPI cell", () => {
    const html = renderPage();
    expect(html).toContain("Healthy");
  });

  it("renders Sessions · 24h KPI cell", () => {
    const html = renderPage();
    expect(html).toContain("Sessions");
  });

  it("renders Pending approvals KPI cell", () => {
    const html = renderPage();
    expect(html).toContain("Pending approvals");
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

  it("renders the Butlers section eyebrow", () => {
    const html = renderPage();
    // The eyebrow text is "Butlers" in HTML; CSS text-transform uppercase
    // applies visually but does not change the serialized string.
    expect(html).toContain("Butlers");
  });
});

// ---------------------------------------------------------------------------
// QA staffer widget
// ---------------------------------------------------------------------------

describe("DashboardPage -- QA staffer widget", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setDefaultData();
  });

  it("renders dispatch summary rows with click-through when QA is active", () => {
    vi.mocked(useQaSummary).mockReturnValue(
      makeQaSummaryResponse({
        last_patrol_at: "2026-05-15T01:23:00Z",
        last_patrol: {
          id: "patrol-123",
          started_at: "2026-05-15T01:23:00Z",
          completed_at: "2026-05-15T01:24:00Z",
          status: "clean",
          findings_count: 0,
          novel_count: 0,
          dispatched_count: 0,
          log_lookback_minutes: 60,
          sources_polled: ["sessions"],
          error_detail: null,
        },
        stats_24h: {
          patrols: 1,
          findings: 0,
          novel: 0,
          dispatched: 0,
          prs_opened: 0,
        },
        kpis: {
          prs_landed_24h: 0,
          mttr_24h_seconds: null,
          self_resolved_7d_pct: 0,
          active_cases_now: 3,
        },
        active_breakdown: {
          awaiting_ci: 1,
          escalated: 0,
        },
        active_sources: ["sessions"],
      }) as AnyMock,
    );

    const html = renderPage();
    expect(html).toContain("QA staffer");
    expect(html).toContain("running");
    expect(html).toMatch(/\d{2}:\d{2} · clean/);
    expect(html).toContain("active cases · now");
    expect(html).toContain(">3<");
    expect(html).toContain('href="/qa"');
    expect(html).not.toContain("recharts");
  });

  it("keeps the QA click-through under the router basename", () => {
    vi.mocked(useQaSummary).mockReturnValue(
      makeQaSummaryResponse({
        last_patrol_at: "2026-05-15T01:23:00Z",
        last_patrol: {
          id: "patrol-123",
          started_at: "2026-05-15T01:23:00Z",
          completed_at: "2026-05-15T01:24:00Z",
          status: "clean",
          findings_count: 0,
          novel_count: 0,
          dispatched_count: 0,
          log_lookback_minutes: 60,
          sources_polled: ["sessions"],
          error_detail: null,
        },
      }) as AnyMock,
    );

    const html = renderPage({ basename: "/butlers-dev" });
    expect(html).toContain('href="/butlers-dev/qa"');
  });

  it("renders the serif inactive line and hides count when QA is stopped", () => {
    vi.mocked(useQaSummary).mockReturnValue(
      makeQaSummaryResponse({
        staffer_status: "stopped",
        last_patrol_at: "2026-05-15T01:23:00Z",
        last_patrol: {
          id: "patrol-123",
          started_at: "2026-05-15T01:23:00Z",
          completed_at: "2026-05-15T01:24:00Z",
          status: "clean",
          findings_count: 0,
          novel_count: 0,
          dispatched_count: 0,
          log_lookback_minutes: 60,
          sources_polled: ["sessions"],
          error_detail: null,
        },
      }) as AnyMock,
    );

    const html = renderPage();
    expect(html).toContain("QA staffer not active");
    expect(html).not.toContain("active cases · now");
  });

  it("renders the inactive line when no patrol records exist", () => {
    const html = renderPage();
    expect(html).toContain("QA staffer not active");
    expect(html).not.toContain("active cases · now");
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
