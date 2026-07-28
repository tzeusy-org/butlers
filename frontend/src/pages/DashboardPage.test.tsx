// @vitest-environment jsdom
/**
 * Tests for DashboardPage (editorial archetype, bu-1fpvp.2 / bu-bm58r.1).
 *
 * Verifies the editorial-archetype layout:
 * - Briefing surface: DateEyebrow, BriefingStatus pill, Headline, Elaboration
 * - AttentionList with items and empty-state fallback
 * - RuntimeSummaryKpi cells (total / healthy / sessions_24h / pending approvals)
 * - ButlerIndex rows
 * - OperationsNowList (pending approvals, QA state, notifications, recent activity)
 * - Six state_class values render without crashing
 *
 * Prior test contracts (Vertical-D hero/secondary regions) are replaced by
 * the editorial layout.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import DashboardPage from "@/pages/DashboardPage";
import * as overviewModel from "@/components/overview/model";

// ---------------------------------------------------------------------------
// Mock all hooks used by DashboardPage (and RuntimeSummaryKpi)
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-briefing", () => ({ useBriefing: vi.fn() }));
vi.mock("@/hooks/use-butlers", () => ({ useButlersBoard: vi.fn() }));
vi.mock("@/hooks/use-spend", () => ({
  useSpendSummary: vi.fn(),
  useTopSessions: vi.fn(),
  useDailySpend: vi.fn(),
}));
vi.mock("@/hooks/use-issues", () => ({ useIssues: vi.fn() }));
vi.mock("@/hooks/use-approvals", () => ({
  useApprovalMetrics: vi.fn(),
  usePendingApprovalsFlat: vi.fn(),
}));
vi.mock("@/hooks/use-approval-decisions.ts", () => ({
  useApprovalDecisionMutations: vi.fn(),
  UNDO_WINDOW_MS: 5_000,
}));
vi.mock("@/hooks/use-notifications", () => ({ useNotificationStats: vi.fn() }));
vi.mock("@/hooks/use-qa", () => ({ useQaSummary: vi.fn() }));
vi.mock("@/hooks/use-timeline", () => ({ useTimeline: vi.fn() }));
vi.mock("@/hooks/use-fleet-halt", () => ({ useFleetHaltStatus: vi.fn() }));
vi.mock("@/hooks/use-delegation", () => ({ useStuckDelegations: vi.fn() }));

// ---------------------------------------------------------------------------
// Imports after mocks are registered
// ---------------------------------------------------------------------------

import { useBriefing } from "@/hooks/use-briefing";
import { useButlersBoard } from "@/hooks/use-butlers";
import {
  useSpendSummary,
  useTopSessions,
  useDailySpend,
} from "@/hooks/use-spend";
import { useIssues } from "@/hooks/use-issues";
import {
  useApprovalMetrics,
  usePendingApprovalsFlat,
} from "@/hooks/use-approvals";
import { useApprovalDecisionMutations } from "@/hooks/use-approval-decisions.ts";
import { useNotificationStats } from "@/hooks/use-notifications";
import { useQaSummary } from "@/hooks/use-qa";
import { useTimeline } from "@/hooks/use-timeline";
import { useFleetHaltStatus } from "@/hooks/use-fleet-halt";
import { useStuckDelegations } from "@/hooks/use-delegation";
import {
  ShortcutRegistryProvider,
  useShortcutHintEntries,
} from "@/hooks/use-register-shortcut";
import type { BoardRow } from "@/api/types";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * A GET /api/butlers/board row -- the canonical, cadence-aware liveness
 * verdict (bu-qvnce.4). Defaults to "idle" (a fine, no-attention-needed
 * butler) so overrides only need to set what a given test cares about.
 */
function boardRow(overrides: Partial<BoardRow> = {}): BoardRow {
  return {
    name: "general",
    type: "butler",
    description: null,
    status: "ok",
    activity: "idle",
    cell_tone: "green",
    eligibility: "active",
    quarantine_reason: null,
    quarantined_at: null,
    sessions_24h: 0,
    cost_today: null,
    load_pct: null,
    max_concurrent: null,
    active_session_count: 0,
    last_session_at: null,
    last_heartbeat_at: null,
    heartbeat_age_seconds: null,
    heartbeat_unavailable: false,
    schema_unreachable: false,
    hourly_stripe: [],
    hourly_total: 0,
    cadence_seconds: null,
    cadence_label: null,
    silence_seconds: null,
    cadence_status: "on_schedule",
    ...overrides,
  };
}

/**
 * The default two-butler board fixture: "general" is actively running
 * (healthy, no attention needed), "health" is cadence-overdue (needsAttention
 * -- surfaces both in the attention list AND depresses the Healthy KPI, since
 * bu-qvnce.4 derives both from this exact same per-row verdict).
 */
function defaultBoardRows(): BoardRow[] {
  return [
    boardRow({
      name: "general",
      sessions_24h: 3,
      cost_today: 0.3,
      activity: "running",
      active_session_count: 1,
      last_session_at: "2026-05-14T11:55:00.000Z",
      last_heartbeat_at: "2026-05-14T11:59:00.000Z",
      heartbeat_age_seconds: 30,
    }),
    boardRow({
      name: "health",
      sessions_24h: 2,
      cost_today: 0.12,
      activity: "overdue",
      active_session_count: 0,
      last_session_at: "2026-05-14T11:30:00.000Z",
      last_heartbeat_at: "2026-05-14T11:40:00.000Z",
      heartbeat_age_seconds: 1_200,
      silence_seconds: 1_200,
      cadence_status: "overdue",
    }),
  ];
}

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

function setDefaultData(
  stateClass = "quiet",
  headline = "Everything is in hand.",
) {
  vi.mocked(useBriefing).mockReturnValue(
    makeBriefing(stateClass, "llm", headline) as AnyMock,
  );
  vi.mocked(useButlersBoard).mockReturnValue({
    data: {
      data: {
        rows: defaultBoardRows(),
        aggregates: {},
        generated_at: "2026-05-14T12:00:00.000Z",
      },
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
        by_butler: { general: 0.3, health: 0.12 },
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
  vi.mocked(usePendingApprovalsFlat).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
    isError: false,
    error: null,
  } as AnyMock);
  // scheduleDecision defaults to firing `run()` synchronously (mirrors the
  // real hook's behavior when `undoWindow` isn't opted into) so existing
  // "calls the mutation with the row's id" assertions below stay a click ->
  // immediate-call check; the shared undo-window contract itself is covered
  // by its own describe block further down with the REAL hook.
  vi.mocked(useApprovalDecisionMutations).mockReturnValue({
    approveMut: { mutate: vi.fn(), isPending: false, variables: undefined },
    denyMut: { mutate: vi.fn(), isPending: false, variables: undefined },
    deferMut: { mutate: vi.fn(), isPending: false, variables: undefined },
    scheduledDecisions: new Map(),
    scheduleDecision: vi.fn((_id: string, _verb: string, run: () => void) => {
      run();
      return true;
    }),
    cancelDecision: vi.fn(),
  } as AnyMock);
  vi.mocked(useNotificationStats).mockReturnValue({
    data: {
      data: { total: 0, sent: 0, failed: 0, by_channel: {}, by_butler: {} },
      meta: {},
    },
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
  vi.mocked(useDailySpend).mockReturnValue({
    data: {
      data: [
        {
          date: "2026-05-08",
          cost_usd: 0.31,
          sessions: 4,
          input_tokens: 1000,
          output_tokens: 500,
        },
        {
          date: "2026-05-09",
          cost_usd: 0.42,
          sessions: 5,
          input_tokens: 1000,
          output_tokens: 500,
        },
      ],
      meta: {},
    },
    isLoading: false,
    isError: false,
    error: null,
  } as AnyMock);
  vi.mocked(useFleetHaltStatus).mockReturnValue({
    active: false,
    deniedToday: 0,
    deniedTotal: 0,
    since: null,
    recentAttempts: [],
    isLoading: false,
    isError: false,
  } as AnyMock);
  vi.mocked(useStuckDelegations).mockReturnValue({
    rows: [],
    total: 0,
    isLoading: false,
    isError: false,
  } as AnyMock);
}

function renderPage(
  { basename = "", initialEntry }: { basename?: string; initialEntry?: string } = {},
): string {
  const queryClient = new QueryClient();
  const initialEntries = [initialEntry ?? (basename ? `${basename}/` : "/")];
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter basename={basename} initialEntries={initialEntries}>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Derived-model stability
// ---------------------------------------------------------------------------

describe("DashboardPage -- derived triage model stability", () => {
  let container: HTMLDivElement | undefined;
  let root: Root | undefined;
  let queryClient: QueryClient | undefined;

  beforeEach(() => {
    vi.resetAllMocks();
    setDefaultData();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient();
  });

  afterEach(() => {
    if (root) {
      act(() => {
        root!.unmount();
      });
    }
    container?.remove();
    container = undefined;
    root = undefined;
    queryClient = undefined;
  });

  function renderLive() {
    act(() => {
      root!.render(
        <QueryClientProvider client={queryClient!}>
          <MemoryRouter>
            <DashboardPage />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it("keeps the derived attention rows stable across renders without source changes", () => {
    const derive = vi.spyOn(overviewModel, "deriveOverviewTriageModel");

    renderLive();
    renderLive();

    expect(derive).toHaveBeenCalledTimes(1);
  });
});

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
// Six state_class values render without errors
// ---------------------------------------------------------------------------

describe("DashboardPage -- state_class variants", () => {
  const STATE_CLASSES: Array<{ stateClass: string; headline: string }> = [
    { stateClass: "quiet", headline: "Everything is in hand." },
    { stateClass: "mild", headline: "Things are quiet, with 1 exception." },
    { stateClass: "busy", headline: "Things are busy with 5 items waiting." },
    {
      stateClass: "degraded-quiet",
      headline: "Quiet, but 1 butler is degraded.",
    },
    {
      stateClass: "degraded",
      headline: "One source could not be reached, so this may be incomplete.",
    },
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
    vi.mocked(useButlersBoard).mockReturnValue({
      data: {
        data: {
          rows: [
            boardRow({
              name: "general",
              activity: "running",
              active_session_count: 1,
            }),
            boardRow({ name: "health", activity: "idle" }),
          ],
          aggregates: {},
          generated_at: "2026-05-14T12:00:00.000Z",
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

  it("never renders 'Nothing waiting.' when the issues source has errored (bu-86c4c.2 -- truth amnesty)", () => {
    vi.mocked(useIssues).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("issues fetch failed"),
    } as AnyMock);
    const html = renderPage();
    expect(html).not.toContain("Nothing waiting.");
    expect(html).toContain("Issues feed unavailable");
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

  it("renders Healthy KPI cell derived from the SAME board verdict as the attention list (bu-qvnce.4)", () => {
    // "health" is cadence-overdue in the default fixture (it also shows up
    // in the "Needs attention" list below, per the next test) -- Healthy
    // must therefore read 1, not 2. Before bu-qvnce.4 this KPI was computed
    // from the butler's raw `status` field independently of the attention
    // list's own verdict, so it could -- and did -- disagree with it.
    const html = renderPage();
    expect(html).toContain("Healthy");
    expect(html).toContain(">1<");
  });

  it("renders Sessions · 24h KPI cell", () => {
    const html = renderPage();
    expect(html).toContain("Sessions");
  });

  it("honors a degraded Sessions board source without disabling the other KPI doors", () => {
    vi.mocked(useButlersBoard).mockReturnValue({
      data: {
        data: {
          rows: defaultBoardRows(),
          aggregates: { sessions_source_error: true },
          generated_at: "2026-05-14T12:00:00.000Z",
        },
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as AnyMock);

    const html = renderPage();
    const stripStart = html.indexOf('<section aria-label="System runtime summary">');
    const stripEnd = html.indexOf("</section>", stripStart);
    const kpiStrip = html.slice(stripStart, stripEnd);

    expect(kpiStrip).toContain('class="sr-only"> unavailable</span>');
    expect(kpiStrip).toContain('href="/butlers"');
    expect(kpiStrip).toContain('href="/approvals"');
    expect(kpiStrip).not.toContain('href="/sessions');
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

  it("marks Cost Today unpriced instead of displaying a partial ledger subtotal as a total", () => {
    vi.mocked(useSpendSummary).mockReturnValue({
      data: {
        data: {
          total_cost_usd: 0.42,
          total_sessions: 5,
          total_input_tokens: 1000,
          total_output_tokens: 500,
          by_butler: { general: 0.3, health: 0.12 },
          by_model: {},
          unpriced_models: [
            {
              model: "unknown-executed-model",
              calls: 3,
              input_tokens: 100,
              output_tokens: 50,
              cached_input_tokens: 0,
              cache_creation_tokens: 0,
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

    expect(html).toContain("—/unpriced");
    expect(html).toContain("3 unpriced calls excluded");
    expect(html).not.toContain("Top: general");
  });

  it("treats a summary compatibility zero with source_error as unavailable evidence", () => {
    vi.mocked(useSpendSummary).mockReturnValue({
      data: {
        data: {
          total_cost_usd: 0,
          total_sessions: 0,
          total_input_tokens: 0,
          total_output_tokens: 0,
          by_butler: {},
          by_model: {},
          source_error: true,
        },
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);
    vi.mocked(useDailySpend).mockReturnValue({
      data: { data: [], meta: { source_error: true } },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);

    const html = renderPage();

    expect(html).toContain("Cost source unavailable");
    expect(html).not.toContain("Top: general");
  });

  it("renders a direct summary query failure as unavailable instead of a fallback total", () => {
    vi.mocked(useSpendSummary).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("summary unavailable"),
    } as AnyMock);

    const html = renderPage();

    expect(html).toContain("Cost summary: unavailable");
    expect(html).not.toContain("$0.00");
    expect(html).not.toContain("Top: general");
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

  it("renders a direct top-sessions query failure as unavailable instead of successful-empty copy", () => {
    vi.mocked(useTopSessions).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("top sessions unavailable"),
    } as AnyMock);

    const html = renderPage();

    expect(html).toContain("Top sessions: unavailable");
    expect(html).not.toContain("No session data available");
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
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-14T12:00:00.000Z"));
    vi.resetAllMocks();
    setDefaultData();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders 'Nothing scheduled.' when no now signals are active", () => {
    const html = renderPage();
    expect(html).toContain("Nothing scheduled.");
  });

  it("keeps maintenance out of Now by default and exposes it through the URL-backed Internal lens", () => {
    vi.mocked(useTimeline).mockReturnValue({
      data: {
        data: [
          {
            id: "maintenance-1",
            type: "session",
            butler: "memory",
            timestamp: "2026-05-14T11:59:00.000Z",
            summary: "Scheduled: consolidation",
            machine_class: "maintenance",
            is_heartbeat: false,
            data: { success: true },
          },
          {
            id: "maintenance-2",
            type: "session",
            butler: "memory",
            timestamp: "2026-05-14T11:58:00.000Z",
            summary: "Scheduled: memory decay sweep",
            machine_class: "maintenance",
            is_heartbeat: false,
            data: { success: true },
          },
        ],
        meta: { cursor: null, has_more: false },
      },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);

    const ownerLens = renderPage();
    expect(ownerLens).toContain('data-testid="dashboard-internal-lens"');
    expect(ownerLens).toContain('aria-pressed="false"');
    expect(ownerLens).not.toContain("memory: 2 maintenance runs");

    const internalLens = renderPage({ initialEntry: "/?internal=1" });
    expect(internalLens).toContain('aria-pressed="true"');
    expect(internalLens).toContain("memory: 2 maintenance runs");
    expect(internalLens).toContain('href="/timeline?internal=1"');
  });

  it("renders failed maintenance as a failure in both Dashboard Now lenses", () => {
    vi.mocked(useTimeline).mockReturnValue({
      data: {
        data: [
          {
            id: "maintenance-success",
            type: "session",
            butler: "memory",
            timestamp: "2026-05-14T11:59:00.000Z",
            summary: "Scheduled: consolidation",
            machine_class: "maintenance",
            is_heartbeat: false,
            data: { success: true },
          },
          {
            id: "maintenance-failed",
            type: "error",
            butler: "memory",
            timestamp: "2026-05-14T11:58:00.000Z",
            summary: "Scheduled: memory decay sweep",
            machine_class: "maintenance",
            is_heartbeat: false,
            data: { success: false },
          },
        ],
        meta: { cursor: null, has_more: false },
      },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);

    const ownerLens = renderPage();
    const failedRowStart = ownerLens.indexOf("Scheduled: memory decay sweep");
    expect(failedRowStart).toBeGreaterThan(-1);
    const ownerRowStart = ownerLens.lastIndexOf('role="listitem"', failedRowStart);
    expect(ownerRowStart).toBeGreaterThan(-1);
    const ownerFailure = ownerLens.slice(ownerRowStart, failedRowStart + 240);
    expect(ownerFailure).toContain(">failed<");
    expect(ownerFailure).toContain('role="alert"');
    expect(ownerFailure).toContain("color:var(--destructive)");
    expect(ownerLens).not.toContain("memory: 2 maintenance runs");

    const internalLens = renderPage({ initialEntry: "/?internal=1" });
    const internalRollupStart = internalLens.indexOf("memory: 2 maintenance runs");
    expect(internalRollupStart).toBeGreaterThan(-1);
    const internalRowStart = internalLens.lastIndexOf('role="listitem"', internalRollupStart);
    expect(internalRowStart).toBeGreaterThan(-1);
    const internalFailure = internalLens.slice(internalRowStart, internalRollupStart + 280);
    expect(internalFailure).toContain("1 failed");
    expect(internalFailure).toContain(">failed<");
    expect(internalFailure).toContain('role="alert"');
    expect(internalFailure).toContain("color:var(--destructive)");
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
    expect(html).toContain("1 failed notification in the last 24 hours");
    // Predicate-carrying door: retains both failed status and the 24-hour
    // window that produced this count, rather than opening an all-time feed.
    expect(html).toContain(
      'href="/notifications?status=terminal_failed&amp;since=2026-05-13T12%3A00%3A00.000Z&amp;until=2026-05-14T12%3A00%3A00.000Z"',
    );
    expect(useNotificationStats).toHaveBeenCalledWith({
      since: "2026-05-13T12:00:00.000Z",
      until: "2026-05-14T12:00:00.000Z",
    });
  });

  it("normalizes non-minute notification bounds to the visible filter precision", () => {
    vi.setSystemTime(new Date("2026-05-14T12:00:37.456Z"));
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

    // NotificationsPage's datetime-local controls deliberately show minutes.
    // The Dashboard request and predicate-carrying door must therefore use the
    // same minute-aligned closed interval instead of serializing hidden seconds.
    expect(useNotificationStats).toHaveBeenCalledWith({
      since: "2026-05-13T12:00:00.000Z",
      until: "2026-05-14T12:00:00.000Z",
    });
    expect(html).toContain(
      'href="/notifications?status=terminal_failed&amp;since=2026-05-13T12%3A00%3A00.000Z&amp;until=2026-05-14T12%3A00%3A00.000Z"',
    );
  });

  it("rolls the notification window while the dashboard remains mounted", () => {
    const container = document.createElement("div");
    const root = createRoot(container);
    const queryClient = new QueryClient();
    document.body.appendChild(container);

    try {
      act(() => {
        root.render(
          <QueryClientProvider client={queryClient}>
            <MemoryRouter>
              <DashboardPage />
            </MemoryRouter>
          </QueryClientProvider>,
        );
      });
      expect(useNotificationStats).toHaveBeenLastCalledWith({
        since: "2026-05-13T12:00:00.000Z",
        until: "2026-05-14T12:00:00.000Z",
      });

      act(() => {
        vi.advanceTimersByTime(60_000);
      });
      expect(useNotificationStats).toHaveBeenLastCalledWith({
        since: "2026-05-13T12:01:00.000Z",
        until: "2026-05-14T12:01:00.000Z",
      });
    } finally {
      act(() => {
        root.unmount();
      });
      container.remove();
    }
  });

  it("renders a completed QA dispatch as last-24-hours activity, not active follow-up", () => {
    vi.mocked(useQaSummary).mockReturnValue({
      data: {
        data: {
          circuit_breaker: { tripped: false, consecutive_failures: 0 },
          last_patrol: {
            id: "p-dispatched",
            started_at: "2026-05-14T11:00:00.000Z",
            completed_at: "2026-05-14T11:01:00.000Z",
            status: "findings_dispatched",
            findings_count: 1,
            novel_count: 0,
            dispatched_count: 1,
            log_lookback_minutes: 60,
            sources_polled: [],
            error_detail: null,
          },
          stats_24h: {
            patrols_completed: 1,
            total_findings: 1,
            novel_findings: 0,
            dispatched_investigations: 1,
            prs_opened: 0,
          },
          kpis: { active_cases_now: 0 },
        },
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain(
      "1 QA investigation dispatched in the last 24 hours",
    );
    expect(html).not.toContain("QA has active follow-up work.");
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
            status: "error",
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
          credentials_status: {
            gh_token_present: null,
            git_author_name_present: null,
            git_author_email_present: null,
            provisioning_hint: null,
          },
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
// Regression guard: a failing GET /api/butlers/board must NOT render as a
// serene, healthy-looking empty page ("No butlers active."). It must surface
// a degraded state — both in the ButlerIndex empty slot and as a named Now
// error row — mirroring how the sibling sources surface their failures.
// ---------------------------------------------------------------------------

describe("DashboardPage -- butler-health source failure", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setDefaultData();
    // The board is the single source for both the index rows AND the runtime
    // KPIs/attention list now (bu-qvnce.4) -- one query failing means no
    // butler rows to fall back on anywhere.
    vi.mocked(useButlersBoard).mockReturnValue({
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

// ---------------------------------------------------------------------------
// Inline approve/deny/defer on the Needs-attention list (bu-86c4c.14 -- Act
// loop / hot queue): approve/deny/defer executable from the dashboard
// without leaving the pane.
// ---------------------------------------------------------------------------

describe("DashboardPage -- inline approve/deny/defer on the attention list (bu-86c4c.14)", () => {
  let container: HTMLDivElement | undefined;
  let root: Root | undefined;

  beforeEach(() => {
    vi.resetAllMocks();
    setDefaultData();
  });

  afterEach(() => {
    if (root) {
      act(() => {
        root!.unmount();
      });
    }
    container?.remove();
    container = undefined;
    root = undefined;
  });

  function renderLive() {
    const queryClient = new QueryClient();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    const r = root;
    act(() => {
      r.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <DashboardPage />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  function findButton(label: string): HTMLButtonElement | undefined {
    return Array.from(container!.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === label,
    );
  }

  it("renders one actionable row per pending approval with verb-labeled buttons", () => {
    vi.mocked(usePendingApprovalsFlat).mockReturnValue({
      data: {
        data: [
          {
            id: "a1",
            butler: "general",
            tool_name: "send_email",
            status: "pending",
            created_at: "2026-05-14T10:00:00Z",
            expires_at: null,
            why: null,
          },
        ],
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("send email");
    expect(html).toContain(">Approve<");
    expect(html).toContain(">Deny<");
    expect(html).toContain(">Defer<");
  });

  it("calls the shared approve/deny/defer mutations with the row's approval id", () => {
    const approveMutate = vi.fn();
    const denyMutate = vi.fn();
    const deferMutate = vi.fn();
    vi.mocked(useApprovalDecisionMutations).mockReturnValue({
      approveMut: {
        mutate: approveMutate,
        isPending: false,
        variables: undefined,
      },
      denyMut: { mutate: denyMutate, isPending: false, variables: undefined },
      deferMut: { mutate: deferMutate, isPending: false, variables: undefined },
      scheduledDecisions: new Map(),
      scheduleDecision: (_id: string, _verb: string, run: () => void) => {
        run();
        return true;
      },
      cancelDecision: vi.fn(),
    } as AnyMock);
    vi.mocked(usePendingApprovalsFlat).mockReturnValue({
      data: {
        data: [
          {
            id: "a1",
            butler: "general",
            tool_name: "send_email",
            status: "pending",
            created_at: "2026-05-14T10:00:00Z",
            expires_at: null,
            why: null,
          },
        ],
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);

    renderLive();

    act(() => {
      findButton("Approve")!.click();
    });
    expect(approveMutate).toHaveBeenCalledWith("a1");

    act(() => {
      findButton("Deny")!.click();
    });
    expect(denyMutate).toHaveBeenCalledWith({ id: "a1" });

    act(() => {
      findButton("Defer")!.click();
    });
    expect(deferMutate).toHaveBeenCalledWith({ id: "a1", hours: 24 });
  });

  it("falls back to the aggregate 'N pending approvals' row when the detail list errors", () => {
    vi.mocked(usePendingApprovalsFlat).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("unreachable"),
    } as AnyMock);
    vi.mocked(useApprovalMetrics).mockReturnValue({
      data: { data: { total_pending: 3 }, meta: {} },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("3 pending approvals");
    expect(html).not.toContain(">Approve<");
  });
});

// ---------------------------------------------------------------------------
// j/k list-triage on the Needs-attention list (bu-qvnce.11 slice 4):
// DashboardPage adopts the shared useListTriage hook extracted from
// ApprovalsPage's own former hand-rolled j/k/a/d/x implementation. Only the
// wiring is covered here -- useListTriage's own navigation/act-key
// mechanics are unit-tested directly in use-list-triage.test.tsx.
// ---------------------------------------------------------------------------

describe("DashboardPage -- j/k list-triage on the attention list (bu-qvnce.11 slice 4)", () => {
  let container: HTMLDivElement | undefined;
  let root: Root | undefined;

  beforeEach(() => {
    vi.resetAllMocks();
    setDefaultData();
  });

  afterEach(() => {
    if (root) {
      act(() => {
        root!.unmount();
      });
    }
    container?.remove();
    container = undefined;
    root = undefined;
  });

  function renderLive() {
    const queryClient = new QueryClient();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    const r = root;
    act(() => {
      r.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <DashboardPage />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  function press(key: string) {
    window.dispatchEvent(
      new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }),
    );
  }

  /** Only healthy butler rows (bu-qvnce.4's own "Nothing waiting" fixture)
   *  so the attention list contains exactly the pending approval below --
   *  no runtime/butler-health row competing for the j/k idx-0 slot. */
  function useOnlyHealthyBoardRows() {
    vi.mocked(useButlersBoard).mockReturnValue({
      data: {
        data: {
          rows: [
            boardRow({
              name: "general",
              activity: "running",
              active_session_count: 1,
            }),
            boardRow({ name: "health", activity: "idle" }),
          ],
          aggregates: {},
          generated_at: "2026-05-14T12:00:00.000Z",
        },
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);
  }

  it("j selects the first attention row, moving focus onto it", () => {
    vi.mocked(usePendingApprovalsFlat).mockReturnValue({
      data: {
        data: [
          {
            id: "a1",
            butler: "general",
            tool_name: "send_email",
            status: "pending",
            created_at: "2026-05-14T10:00:00Z",
            expires_at: null,
            why: null,
          },
        ],
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);

    renderLive();
    act(() => press("j"));

    const rows = container!.querySelectorAll('[data-testid="attention-item"]');
    expect(rows.length).toBeGreaterThan(0);
    const first = rows[0] as HTMLElement;
    expect(first.getAttribute("data-item-id")).toBe(
      document.activeElement?.getAttribute("data-item-id"),
    );
  });

  it("a approves the selected row via the shared decision mutation", () => {
    const approveMutate = vi.fn();
    vi.mocked(useApprovalDecisionMutations).mockReturnValue({
      approveMut: {
        mutate: approveMutate,
        isPending: false,
        variables: undefined,
      },
      denyMut: { mutate: vi.fn(), isPending: false, variables: undefined },
      deferMut: { mutate: vi.fn(), isPending: false, variables: undefined },
      scheduledDecisions: new Map(),
      scheduleDecision: (_id: string, _verb: string, run: () => void) => {
        run();
        return true;
      },
      cancelDecision: vi.fn(),
    } as AnyMock);
    useOnlyHealthyBoardRows();
    vi.mocked(usePendingApprovalsFlat).mockReturnValue({
      data: {
        data: [
          {
            id: "a1",
            butler: "general",
            tool_name: "send_email",
            status: "pending",
            created_at: "2026-05-14T10:00:00Z",
            expires_at: null,
            why: null,
          },
        ],
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);

    renderLive();
    act(() => press("j")); // select the (only) row
    act(() => press("a")); // approve it

    expect(approveMutate).toHaveBeenCalledWith("a1");
  });

  it("renders the footer hint strip advertising the exact bound keys", () => {
    useOnlyHealthyBoardRows();
    vi.mocked(usePendingApprovalsFlat).mockReturnValue({
      data: {
        data: [
          {
            id: "a1",
            butler: "general",
            tool_name: "send_email",
            status: "pending",
            created_at: "2026-05-14T10:00:00Z",
            expires_at: null,
            why: null,
          },
        ],
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);

    renderLive();
    act(() => press("j"));

    expect(container!.textContent).toContain("Next item");
    expect(container!.textContent).toContain("Previous item");
    expect(container!.textContent).toContain("Approve selected");
  });

  it("renders no footer hint strip when the attention list is empty", () => {
    useOnlyHealthyBoardRows();
    renderLive();
    expect(
      container!.querySelector(
        '[aria-label="Keyboard shortcuts for this list"]',
      ),
    ).toBeNull();
  });

  it("keeps its shortcut registration stable across an unrelated parent render", () => {
    const seenBindings: unknown[] = [];
    const queryClient = new QueryClient();
    function ShortcutBindingsProbe() {
      seenBindings.push(useShortcutHintEntries());
      return null;
    }
    function App({ tick }: { tick: number }) {
      return (
        <ShortcutRegistryProvider>
          <QueryClientProvider client={queryClient}>
            <MemoryRouter>
              <DashboardPage />
              <span data-testid="parent-render">{tick}</span>
            </MemoryRouter>
          </QueryClientProvider>
          <ShortcutBindingsProbe />
        </ShortcutRegistryProvider>
      );
    }

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    const r = root;
    act(() => r.render(<App tick={0} />));
    const registeredBindings = seenBindings.at(-1);

    act(() => r.render(<App tick={1} />));

    expect(seenBindings.at(-1)).toBe(registeredBindings);
  });
});

// ---------------------------------------------------------------------------
// Shared undo-window contract wiring (bu-qvnce.4): a decision made from the
// dashboard's one-click attention list must be just as undoable as one made
// on /approvals -- clicking Approve/Deny/Defer schedules the decision
// (through the shared useApprovalDecisionMutations hook) rather than firing
// the mutation immediately, and the row shows an inline pending/undo state
// while it's outstanding. The hook's own timer/undo mechanics are unit-tested
// directly in use-approval-decisions.test.tsx; this only verifies DashboardPage
// wires scheduleDecision/scheduledDecisions/cancelDecision correctly.
// ---------------------------------------------------------------------------

describe("DashboardPage -- shared undo-window contract (bu-qvnce.4)", () => {
  let container: HTMLDivElement | undefined;
  let root: Root | undefined;

  beforeEach(() => {
    vi.resetAllMocks();
    setDefaultData();
    vi.mocked(usePendingApprovalsFlat).mockReturnValue({
      data: {
        data: [
          {
            id: "a1",
            butler: "general",
            tool_name: "send_email",
            status: "pending",
            created_at: "2026-05-14T10:00:00Z",
            expires_at: null,
            why: null,
          },
        ],
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);
  });

  afterEach(() => {
    if (root) {
      act(() => {
        root!.unmount();
      });
    }
    container?.remove();
    container = undefined;
    root = undefined;
  });

  function renderLive() {
    const queryClient = new QueryClient();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    const r = root;
    act(() => {
      r.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <DashboardPage />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  function findButton(label: string): HTMLButtonElement | undefined {
    return Array.from(container!.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === label,
    );
  }

  it("clicking Approve schedules the decision instead of calling the mutation directly", () => {
    const approveMutate = vi.fn();
    const scheduleDecision = vi.fn();
    vi.mocked(useApprovalDecisionMutations).mockReturnValue({
      approveMut: {
        mutate: approveMutate,
        isPending: false,
        variables: undefined,
      },
      denyMut: { mutate: vi.fn(), isPending: false, variables: undefined },
      deferMut: { mutate: vi.fn(), isPending: false, variables: undefined },
      scheduledDecisions: new Map(),
      scheduleDecision,
      cancelDecision: vi.fn(),
    } as AnyMock);

    renderLive();
    act(() => {
      findButton("Approve")!.click();
    });

    expect(scheduleDecision).toHaveBeenCalledWith(
      "a1",
      "approve",
      expect.any(Function),
    );
    // The mutation itself must NOT fire on click -- only scheduleDecision's
    // own timer (verified in use-approval-decisions.test.tsx) invokes it.
    expect(approveMutate).not.toHaveBeenCalled();
  });

  it("renders the inline 'Approving in 5s · Undo' state once a decision is scheduled, replacing the verb buttons", () => {
    vi.mocked(useApprovalDecisionMutations).mockReturnValue({
      approveMut: { mutate: vi.fn(), isPending: false, variables: undefined },
      denyMut: { mutate: vi.fn(), isPending: false, variables: undefined },
      deferMut: { mutate: vi.fn(), isPending: false, variables: undefined },
      scheduledDecisions: new Map([["a1", { verb: "approve", timeoutId: 0 }]]),
      scheduleDecision: vi.fn(),
      cancelDecision: vi.fn(),
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("Approving in 5s");
    expect(html).toContain(">Undo<");
    expect(html).not.toContain(">Approve<");
    expect(html).not.toContain(">Deny<");
    expect(html).not.toContain(">Defer<");
  });

  it("clicking Undo on a scheduled row calls cancelDecision with the row's id", () => {
    const cancelDecision = vi.fn();
    vi.mocked(useApprovalDecisionMutations).mockReturnValue({
      approveMut: { mutate: vi.fn(), isPending: false, variables: undefined },
      denyMut: { mutate: vi.fn(), isPending: false, variables: undefined },
      deferMut: { mutate: vi.fn(), isPending: false, variables: undefined },
      scheduledDecisions: new Map([["a1", { verb: "approve", timeoutId: 0 }]]),
      scheduleDecision: vi.fn(),
      cancelDecision,
    } as AnyMock);

    renderLive();
    act(() => {
      findButton("Undo")!.click();
    });

    expect(cancelDecision).toHaveBeenCalledWith("a1");
  });
});
