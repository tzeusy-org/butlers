// @vitest-environment jsdom

/**
 * Tests for QaOverviewPage (dossier shell, bu-21uf7).
 *
 * Verifies:
 * - Page renders the sticky top bar, KPI strip, and two-pane body
 * - URL-driven case selection: ?case=<id> selects that case in CaseList
 * - Empty state renders "Nothing in the dossier." when cases list is empty
 * - Error state renders "Couldn't reach the staffer." on API failure
 * - Severity filter buttons are present and accessible
 * - bu-86c4c.19: severity/since/state/butler filters are all URL-persisted
 *   (folded in from the retired /qa/investigations index), and the patrol
 *   pulse strip links the overview to patrol detail
 * - bu-533qx.4: the Force-patrol control toasts on the honest `triggered`
 *   outcome, not unconditionally on a 2xx accept
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// sonner's real export is a callable toast() carrying .success/.error/.warning
// statics. QaOverviewPage uses the statics; mock them so the force-patrol
// branch can be asserted without a real toast surface.
vi.mock("sonner", () => {
  const toastFn = Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  });
  return { toast: toastFn };
});

import { toast } from "sonner";

import QaOverviewPage from "@/pages/QaOverviewPage";

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-qa", () => ({
  useQaSummary: vi.fn(),
  useQaCases: vi.fn(),
  useQaCase: vi.fn(),
  useQaCaseJournal: vi.fn(),
  useRemoveDismissal: vi.fn(),
  useForceQaPatrol: vi.fn(),
  useResetQaCircuitBreaker: vi.fn(),
  useQaCircuitBreaker: vi.fn(),
  useQaPatrols: vi.fn(),
}));

vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Imports after mocks
// ---------------------------------------------------------------------------

import {
  useQaSummary,
  useQaCases,
  useQaCase,
  useQaCaseJournal,
  useRemoveDismissal,
  useForceQaPatrol,
  useResetQaCircuitBreaker,
  useQaCircuitBreaker,
  useQaPatrols,
} from "@/hooks/use-qa";
import { useButlers } from "@/hooks/use-butlers";
import {
  CommandRegistryProvider,
  useCommandMenuActions,
  type PaletteCommand,
} from "@/lib/command-registry";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

// Opt into React's act() environment so the interactive force-patrol specs
// below (createRoot + act) do not emit "not configured to support act(...)".
(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const MOCK_SUMMARY = {
  staffer_status: "claude-sonnet-4-5",
  last_patrol_at: null,
  next_patrol_at: null,
  last_patrol: null,
  stats_24h: { patrols_completed: 0, total_findings: 0, novel_findings: 0, dispatched_investigations: 0 },
  stats_all_time: { total_patrols: 0, dispatched_investigations: 0 },
  kpis: {
    prs_landed_24h: 3,
    mttr_24h_seconds: 420,
    self_resolved_7d_pct: 85.0,
    active_cases_now: 2,
  },
  active_breakdown: {
    awaiting_ci: 1,
    escalated_open_cases: 0,
  },
  active_sources: [],
  circuit_breaker: { tripped: false, consecutive_failures: 0 },
  credentials_status: { gh_token_present: true, git_author_name_present: true, git_author_email_present: true, provisioning_hint: null },
  port: 41110,
  model: "claude-sonnet-4-5",
  patrol_interval_minutes: 10,
};

const MOCK_BREAKER_CLOSED = {
  tripped: false,
  threshold: 5,
  recent_statuses: [],
  recent_attempts: [],
};

const MOCK_BREAKER_TRIPPED = {
  tripped: true,
  threshold: 5,
  recent_statuses: ["failed", "failed", "failed", "failed", "failed"],
  recent_attempts: [
    { id: "attempt-0001", status: "failed", closed_at: "2026-05-16T00:00:00Z" },
    { id: "attempt-0002", status: "failed", closed_at: "2026-05-16T00:10:00Z" },
    { id: "attempt-0003", status: "timeout", closed_at: "2026-05-16T00:20:00Z" },
    { id: "attempt-0004", status: "failed", closed_at: "2026-05-16T00:30:00Z" },
    { id: "attempt-0005", status: "unfixable", closed_at: "2026-05-16T00:40:00Z" },
  ],
};

const MOCK_CASE_1 = {
  id: "case-uuid-001",
  short_id: "#001",
  sev: "high" as const,
  butler: "chronicler",
  headline: "Spotify ingestion failing",
  detected: "2026-05-16T01:00:00Z",
  age_seconds: 3600,
  state: "diagnose" as const,
  pr_state: null,
  pr_url: null,
};

const MOCK_CASE_2 = {
  id: "case-uuid-002",
  short_id: "#002",
  sev: "medium" as const,
  butler: "general",
  headline: "Calendar sync timeout",
  detected: "2026-05-15T22:00:00Z",
  age_seconds: 14400,
  state: "pr" as const,
  pr_state: "open" as const,
  pr_url: "https://github.com/example/repo/pull/42",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderPage(route = "/qa") {
  (useRemoveDismissal as AnyMock).mockReturnValue({ mutate: vi.fn(), isPending: false });
  (useResetQaCircuitBreaker as AnyMock).mockReturnValue({ mutate: vi.fn(), isPending: false });
  (useQaCase as AnyMock).mockReturnValue({ data: undefined, isLoading: false, isError: false });
  (useQaCaseJournal as AnyMock).mockReturnValue({ data: undefined });

  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderToStaticMarkup(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>
        <QaOverviewPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// Publishes the current command-palette labels via a callback fired in an
// effect (not during render — reassigning an outer variable mid-render trips
// the react-hooks/globals purity rule). Mirrors the onReady-sink pattern in
// use-memory-url-state.back-nav.test.tsx.
function CommandLabelsProbe({ onLabels }: { onLabels: (labels: string[]) => void }) {
  const labels = useCommandMenuActions().map((c: PaletteCommand) => c.label);
  useEffect(() => {
    onLabels(labels);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- labels is a fresh array each render; compare by content via the join key.
  }, [labels.join("|")]);
  return null;
}

// Default the circuit-breaker query to a healthy closed state for every test.
// Runs before each describe's own beforeEach; tests that need tripped/unknown
// override in their own body (which runs after all beforeEach hooks). The
// force-patrol describe clears all mocks in its beforeEach and re-sets this.
beforeEach(() => {
  (useQaCircuitBreaker as AnyMock).mockReturnValue({
    data: { data: MOCK_BREAKER_CLOSED },
    isLoading: false,
    isError: false,
  });
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("QaOverviewPage -- dossier shell", () => {
  beforeEach(() => {
    (useQaSummary as AnyMock).mockReturnValue({
      data: { data: MOCK_SUMMARY },
      isLoading: false,
      isError: false,
    });
    (useForceQaPatrol as AnyMock).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    });
    (useQaPatrols as AnyMock).mockReturnValue({
      data: { data: [] },
      isLoading: false,
      isError: false,
    });
    (useButlers as AnyMock).mockReturnValue({
      data: { data: [{ name: "chronicler" }, { name: "general" }] },
      isLoading: false,
      isError: false,
    });
  });

  it("renders the page header eyebrow and H1", () => {
    (useQaCases as AnyMock).mockReturnValue({
      data: { data: [MOCK_CASE_1] },
      isLoading: false,
      isError: false,
    });
    const html = renderPage();
    expect(html).toContain("QA Staffer · dossier");
    expect(html).toContain("What the staff caught and fixed");
  });

  it("renders port, model, and patrol_interval_minutes in header caption", () => {
    (useQaCases as AnyMock).mockReturnValue({
      data: { data: [MOCK_CASE_1] },
      isLoading: false,
      isError: false,
    });
    const html = renderPage();
    expect(html).toContain("port :41110");
    expect(html).toContain("model claude-sonnet-4-5");
    expect(html).toContain("patrol every 10m");
  });

  it("omits header caption when port/model/patrol_interval_minutes are all null", () => {
    (useQaSummary as AnyMock).mockReturnValue({
      data: {
        data: { ...MOCK_SUMMARY, port: null, model: null, patrol_interval_minutes: null },
      },
      isLoading: false,
      isError: false,
    });
    (useQaCases as AnyMock).mockReturnValue({
      data: { data: [MOCK_CASE_1] },
      isLoading: false,
      isError: false,
    });
    const html = renderPage();
    expect(html).not.toContain("port :");
    expect(html).not.toContain("patrol every");
  });

  it("renders a live 24h clock in the page header", () => {
    (useQaCases as AnyMock).mockReturnValue({
      data: { data: [MOCK_CASE_1] },
      isLoading: false,
      isError: false,
    });
    const html = renderPage();
    // clock-24h-mono renders a <time> element with HH:MM (e.g. "08:30")
    expect(html).toMatch(/<time[^>]*>\d{2}:\d{2}<\/time>/);
  });

  it("renders the KPI strip with prs-landed value", () => {
    (useQaCases as AnyMock).mockReturnValue({
      data: { data: [MOCK_CASE_1] },
      isLoading: false,
      isError: false,
    });
    const html = renderPage();
    expect(html).toContain("prs landed · 24h");
    expect(html).toContain("data-testid=\"qa-kpi-prs-landed-value\"");
  });

  it("renders severity filter buttons", () => {
    (useQaCases as AnyMock).mockReturnValue({
      data: { data: [MOCK_CASE_1] },
      isLoading: false,
      isError: false,
    });
    const html = renderPage();
    expect(html).toContain("All");
    expect(html).toContain("High");
    expect(html).toContain("Medium");
    expect(html).toContain("Low");
  });

  it("renders a closed circuit-breaker button until the breaker is tripped", () => {
    (useQaCases as AnyMock).mockReturnValue({
      data: { data: [MOCK_CASE_1] },
      isLoading: false,
      isError: false,
    });
    const closedHtml = renderPage();
    expect(closedHtml).toContain('aria-label="QA circuit breaker closed"');
    expect(closedHtml).toContain("Circuit breaker closed");
    expect(closedHtml).not.toContain("Reset breaker");

    (useQaSummary as AnyMock).mockReturnValue({
      data: {
        data: {
          ...MOCK_SUMMARY,
          staffer_status: "circuit_breaker_tripped",
          circuit_breaker: { tripped: true, consecutive_failures: 5 },
        },
      },
      isLoading: false,
      isError: false,
    });
    const html = renderPage();
    expect(html).toContain('aria-label="Reset QA circuit breaker"');
    expect(html).toContain("Reset breaker");
    expect(html).not.toContain("Circuit breaker closed");
  });

  it("renders the time-range filter group with the four preset pills", () => {
    (useQaCases as AnyMock).mockReturnValue({
      data: { data: [MOCK_CASE_1] },
      isLoading: false,
      isError: false,
    });
    const html = renderPage();
    expect(html).toContain('aria-label="Time range"');
    // Each preset should appear inside an aria-pressed button.
    expect(html).toMatch(/aria-pressed="(true|false)"[^>]*>24h</);
    expect(html).toMatch(/aria-pressed="(true|false)"[^>]*>7d</);
    expect(html).toMatch(/aria-pressed="(true|false)"[^>]*>30d</);
  });

  it("defaults the case list header to 'Cases · last 7d' and asks the hook for since=7d", () => {
    (useQaCases as AnyMock).mockReturnValue({
      data: { data: [MOCK_CASE_1] },
      isLoading: false,
      isError: false,
    });
    const html = renderPage();
    expect(html).toContain("Cases · last 7d");

    const lastCallArgs = (useQaCases as AnyMock).mock.calls.at(-1)?.[0];
    expect(lastCallArgs).toMatchObject({ since: "7d" });

    // The 7d pill should be the active (pressed) one by default.
    expect(html).toMatch(/aria-pressed="true"[^>]*>7d</);
  });

  it("renders case list rows when cases are present", () => {
    (useQaCases as AnyMock).mockReturnValue({
      data: { data: [MOCK_CASE_1, MOCK_CASE_2] },
      isLoading: false,
      isError: false,
    });
    const html = renderPage();
    expect(html).toContain("Spotify ingestion failing");
    expect(html).toContain("Calendar sync timeout");
  });

  it("renders empty-state when cases list is empty", () => {
    (useQaCases as AnyMock).mockReturnValue({
      data: { data: [] },
      isLoading: false,
      isError: false,
    });
    const html = renderPage();
    expect(html).toContain("Nothing in the dossier.");
  });

  it("renders error state when cases query fails", () => {
    (useQaCases as AnyMock).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    });
    const html = renderPage();
    expect(html).toContain("Couldn&#x27;t reach the staffer.");
  });

  it("selects the case from the URL ?case= param", () => {
    (useQaCases as AnyMock).mockReturnValue({
      data: { data: [MOCK_CASE_1, MOCK_CASE_2] },
      isLoading: false,
      isError: false,
    });
    // The selected case row should have aria-current="true"
    const html = renderPage("/qa?case=case-uuid-002");
    expect(html).toContain('data-testid="qa-case-row-case-uuid-002"');
    // aria-current is only set on the active row
    const selectedRowIdx = html.indexOf('data-testid="qa-case-row-case-uuid-002"');
    const ariaCurrentIdx = html.indexOf('aria-current="true"', selectedRowIdx);
    // The aria-current attribute should be within ~100 chars of the testid
    expect(ariaCurrentIdx).toBeGreaterThan(-1);
    expect(ariaCurrentIdx - selectedRowIdx).toBeLessThan(100);
  });

  it("auto-selects first case when no ?case= param is present", () => {
    (useQaCases as AnyMock).mockReturnValue({
      data: { data: [MOCK_CASE_1, MOCK_CASE_2] },
      isLoading: false,
      isError: false,
    });
    const html = renderPage("/qa");
    // First case row should have aria-current="true" (auto-selected)
    const firstRowIdx = html.indexOf('data-testid="qa-case-row-case-uuid-001"');
    const ariaCurrentIdx = html.indexOf('aria-current="true"', firstRowIdx);
    expect(ariaCurrentIdx).toBeGreaterThan(-1);
    expect(ariaCurrentIdx - firstRowIdx).toBeLessThan(100);
  });

  it("renders loading state while cases are fetching", () => {
    (useQaCases as AnyMock).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    });
    const html = renderPage();
    expect(html).toContain("Loading cases");
  });

  // The /api/qa/cases default limit is 25 (qa.py list_cases); silently
  // showing only the first page implies the rail is complete when it isn't
  // (bu-qvnce.2).
  it("names the truncation when more cases exist than the rail's page returned", () => {
    (useQaCases as AnyMock).mockReturnValue({
      data: {
        data: [MOCK_CASE_1],
        meta: { total: 30, offset: 0, limit: 25, has_more: true },
      },
      isLoading: false,
      isError: false,
    });
    const html = renderPage();
    expect(html).toContain('data-testid="qa-case-list-truncation"');
    expect(html).toContain("Showing 1 of 30");
  });

  it("omits the truncation notice when the rail holds every matching case", () => {
    (useQaCases as AnyMock).mockReturnValue({
      data: {
        data: [MOCK_CASE_1],
        meta: { total: 1, offset: 0, limit: 25, has_more: false },
      },
      isLoading: false,
      isError: false,
    });
    const html = renderPage();
    expect(html).not.toContain('data-testid="qa-case-list-truncation"');
  });
});

// ---------------------------------------------------------------------------
// Folded filters (bu-86c4c.19 — /qa/investigations retired into /qa)
// ---------------------------------------------------------------------------

describe("QaOverviewPage -- folded filters are URL-persisted", () => {
  beforeEach(() => {
    (useQaSummary as AnyMock).mockReturnValue({
      data: { data: MOCK_SUMMARY },
      isLoading: false,
      isError: false,
    });
    (useForceQaPatrol as AnyMock).mockReturnValue({ mutate: vi.fn(), isPending: false });
    (useQaPatrols as AnyMock).mockReturnValue({ data: { data: [] }, isLoading: false, isError: false });
    (useButlers as AnyMock).mockReturnValue({
      data: { data: [{ name: "chronicler" }, { name: "general" }] },
      isLoading: false,
      isError: false,
    });
    (useQaCases as AnyMock).mockReturnValue({
      data: { data: [MOCK_CASE_1, MOCK_CASE_2] },
      isLoading: false,
      isError: false,
    });
  });

  it("reads ?sev= from the URL and marks the matching pill pressed", () => {
    const html = renderPage("/qa?sev=high");
    expect(html).toMatch(/aria-pressed="true"[^>]*>High</);

    const lastCallArgs = (useQaCases as AnyMock).mock.calls.at(-1)?.[0];
    expect(lastCallArgs).toMatchObject({ sev: "high" });
  });

  it("reads ?state= from the URL and passes it through to useQaCases", () => {
    renderPage("/qa?state=escalated");
    const lastCallArgs = (useQaCases as AnyMock).mock.calls.at(-1)?.[0];
    expect(lastCallArgs).toMatchObject({ state: "escalated" });
  });

  it("omits state from the query when ?state= is absent (all states)", () => {
    renderPage("/qa");
    const lastCallArgs = (useQaCases as AnyMock).mock.calls.at(-1)?.[0];
    expect(lastCallArgs).not.toHaveProperty("state");
  });

  it("reads ?butler= (comma-separated) from the URL and passes a sorted array through", () => {
    renderPage("/qa?butler=general,chronicler");
    const lastCallArgs = (useQaCases as AnyMock).mock.calls.at(-1)?.[0];
    expect(lastCallArgs).toMatchObject({ butler: ["chronicler", "general"] });

    const html = renderPage("/qa?butler=general,chronicler");
    expect(html).toContain("2 butlers");
  });

  it("renders the state filter select with all five states plus 'all'", () => {
    const html = renderPage("/qa");
    expect(html).toContain('aria-label="Filter by state"');
    expect(html).toContain("Detect");
    expect(html).toContain("Diagnose");
    expect(html).toContain("PR open");
    expect(html).toContain("Landed");
    expect(html).toContain("Escalated");
  });

  it("does not render a page-local theme toggle (the shell header owns the one toggle)", () => {
    const html = renderPage("/qa");
    expect(html).not.toContain('aria-label="Toggle theme"');
  });
});

// ---------------------------------------------------------------------------
// Patrol pulse strip (bu-86c4c.19 — links patrols from the overview)
// ---------------------------------------------------------------------------

describe("QaOverviewPage -- patrol pulse strip", () => {
  beforeEach(() => {
    (useQaSummary as AnyMock).mockReturnValue({
      data: { data: MOCK_SUMMARY },
      isLoading: false,
      isError: false,
    });
    (useForceQaPatrol as AnyMock).mockReturnValue({ mutate: vi.fn(), isPending: false });
    (useButlers as AnyMock).mockReturnValue({ data: { data: [] }, isLoading: false, isError: false });
    (useQaCases as AnyMock).mockReturnValue({
      data: { data: [MOCK_CASE_1] },
      isLoading: false,
      isError: false,
    });
  });

  it("links each recent patrol to its patrol detail route", () => {
    (useQaPatrols as AnyMock).mockReturnValue({
      data: {
        data: [
          {
            id: "patrol-1",
            started_at: "2026-05-16T00:00:00Z",
            completed_at: "2026-05-16T00:05:00Z",
            status: "clean",
            findings_count: 0,
            novel_count: 0,
            dispatched_count: 0,
            log_lookback_minutes: 15,
            sources_polled: [],
            error_detail: null,
          },
        ],
      },
      isLoading: false,
      isError: false,
    });
    const html = renderPage();
    expect(html).toContain("Recent patrols");
    expect(html).toContain('href="/qa/patrols/patrol-1"');
  });

  it("renders nothing when there are no patrols yet", () => {
    (useQaPatrols as AnyMock).mockReturnValue({ data: { data: [] }, isLoading: false, isError: false });
    const html = renderPage();
    expect(html).not.toContain("Recent patrols");
  });

  it("renders nothing while patrols are loading (no fabricated strip)", () => {
    (useQaPatrols as AnyMock).mockReturnValue({ data: undefined, isLoading: true, isError: false });
    const loadingHtml = renderPage();
    expect(loadingHtml).not.toContain("Recent patrols");
    expect(loadingHtml).not.toContain('data-testid="qa-patrol-strip-source-unavailable"');
  });

  // A patrols-API outage must NOT vanish into the same nothing as "no patrols
  // ran" — that silent conflation is the honesty defect bu-jad4j.6 consumes.
  // The strip names the patrols source with a one-line degraded note instead.
  it("names the patrols source (not the empty state) when the patrols query errors", () => {
    (useQaPatrols as AnyMock).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: vi.fn(),
    });
    const html = renderPage();
    const note = 'data-testid="qa-patrol-strip-source-unavailable"';
    expect(html).toContain(note);
    // The degraded note announces itself to assistive tech and names the
    // source inline with an em-dash qualifier — never a suppressed source.
    const noteIdx = html.indexOf(note);
    const window = html.slice(noteIdx - 200, noteIdx + 300);
    expect(window).toContain('role="alert"');
    expect(html).toContain("Recent patrols");
    expect(html).toContain("patrol source unreachable");
    expect(html).toContain("—");
  });

  // Mutation guard: the degraded note must depend on isError. If isError still
  // returned null (the pre-fix behavior), this fails because the note is absent
  // yet the query is genuinely down.
  it("does not render the degraded note for a reachable-but-empty source", () => {
    (useQaPatrols as AnyMock).mockReturnValue({ data: { data: [] }, isLoading: false, isError: false });
    const html = renderPage();
    // Legitimately-empty source keeps its current hidden strip — no note, no
    // fabricated "Recent patrols" heading.
    expect(html).not.toContain('data-testid="qa-patrol-strip-source-unavailable"');
    expect(html).not.toContain("Recent patrols");
  });

  // The backend only ever emits "findings_dispatched" (qa.py _VALID_PATROL_STATUSES),
  // never "dispatched" -- the stale frontend check never matched, so a patrol
  // that actually dispatched findings rendered as clean-green (bu-qvnce.2).
  it("colors a findings_dispatched patrol amber, not clean green", () => {
    (useQaPatrols as AnyMock).mockReturnValue({
      data: {
        data: [
          {
            id: "patrol-2",
            started_at: "2026-05-16T00:00:00Z",
            completed_at: "2026-05-16T00:05:00Z",
            status: "findings_dispatched",
            findings_count: 3,
            novel_count: 1,
            dispatched_count: 1,
            log_lookback_minutes: 15,
            sources_polled: [],
            error_detail: null,
          },
        ],
      },
      isLoading: false,
      isError: false,
    });
    const html = renderPage();
    const linkIdx = html.indexOf('href="/qa/patrols/patrol-2"');
    expect(linkIdx).toBeGreaterThan(-1);
    const window = html.slice(linkIdx, linkIdx + 300);
    expect(window).toContain("bg-[var(--amber)]");
    expect(window).not.toContain("bg-[var(--green)]");
  });
});

// ---------------------------------------------------------------------------
// Force-patrol toast honesty (bu-533qx.4)
//
// The POST /api/qa/force-patrol endpoint returns HTTP 202 even when no patrol
// actually ran (QA daemon unreachable, or a cycle already in progress). The
// control MUST branch on the response's `triggered` flag: success toast only
// when triggered, warning toast (naming the reason) when suppressed, error
// toast on transport failure.
// ---------------------------------------------------------------------------

describe("QaOverviewPage -- force-patrol toast honesty", () => {
  let container: HTMLDivElement;
  let root: Root;
  let confirmSpy: AnyMock;
  // Captures the callbacks the page hands to forcePatrol.mutate() so the test
  // can drive onSuccess/onError with fabricated responses.
  let mutateArgs: Array<[unknown, { onSuccess?: (r: unknown) => void; onError?: (e: unknown) => void }]>;

  beforeEach(() => {
    vi.clearAllMocks();
    mutateArgs = [];
    const mutate = vi.fn((vars: unknown, opts: AnyMock) => {
      mutateArgs.push([vars, opts]);
    });

    (useQaSummary as AnyMock).mockReturnValue({
      data: { data: MOCK_SUMMARY },
      isLoading: false,
      isError: false,
    });
    (useForceQaPatrol as AnyMock).mockReturnValue({ mutate, isPending: false });
    (useQaPatrols as AnyMock).mockReturnValue({ data: { data: [] }, isLoading: false, isError: false });
    (useButlers as AnyMock).mockReturnValue({ data: { data: [] }, isLoading: false, isError: false });
    (useQaCases as AnyMock).mockReturnValue({
      data: { data: [MOCK_CASE_1] },
      isLoading: false,
      isError: false,
    });
    (useRemoveDismissal as AnyMock).mockReturnValue({ mutate: vi.fn(), isPending: false });
    (useResetQaCircuitBreaker as AnyMock).mockReturnValue({ mutate: vi.fn(), isPending: false });
    (useQaCircuitBreaker as AnyMock).mockReturnValue({
      data: { data: MOCK_BREAKER_CLOSED },
      isLoading: false,
      isError: false,
    });
    (useQaCase as AnyMock).mockReturnValue({ data: undefined, isLoading: false, isError: false });
    (useQaCaseJournal as AnyMock).mockReturnValue({ data: undefined });

    confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => {
      root.render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <MemoryRouter initialEntries={["/qa"]}>
            <QaOverviewPage />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    confirmSpy.mockRestore();
  });

  function clickForcePatrol() {
    const btn = container.querySelector<HTMLButtonElement>('button[aria-label="Force patrol"]');
    expect(btn).not.toBeNull();
    act(() => {
      btn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    // The page calls forcePatrol.mutate(undefined, { onSuccess, onError }).
    expect(mutateArgs).toHaveLength(1);
    return mutateArgs[0][1];
  }

  it("warns (not success) when the response reports the patrol was NOT triggered", () => {
    const opts = clickForcePatrol();
    act(() => {
      opts.onSuccess?.({
        data: {
          accepted: false,
          triggered: false,
          message: "Force patrol unavailable — QA daemon unreachable, no patrol triggered.",
        },
      });
    });

    // Mutation-strength: if the toast stayed unconditional this warning path
    // would never fire and success would fire on a suppressed dispatch.
    expect(toast.warning).toHaveBeenCalledWith(
      "Force patrol unavailable — QA daemon unreachable, no patrol triggered.",
    );
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("warns when `triggered` is absent (treat unknown as not-triggered)", () => {
    const opts = clickForcePatrol();
    act(() => {
      opts.onSuccess?.({ data: { accepted: false, message: "Patrol skipped: already running" } });
    });

    expect(toast.warning).toHaveBeenCalledWith("Patrol skipped: already running");
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("toasts success when the response reports the patrol WAS triggered", () => {
    const opts = clickForcePatrol();
    act(() => {
      opts.onSuccess?.({
        data: { accepted: true, triggered: true, message: "Patrol triggered: clean (0 findings)" },
      });
    });

    expect(toast.success).toHaveBeenCalledWith("Patrol triggered: clean (0 findings)");
    expect(toast.warning).not.toHaveBeenCalled();
  });

  it("toasts an error on transport failure", () => {
    const opts = clickForcePatrol();
    act(() => {
      opts.onError?.(new Error("network down"));
    });

    expect(toast.error).toHaveBeenCalledWith("Force patrol failed: network down");
    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.warning).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Breaker tri-state: closed / tripped / unknown (bu-533qx.2)
//
// The toolbar breaker control must render three honest states, never default a
// dead/loading summary query to the calm "Circuit breaker closed".
// ---------------------------------------------------------------------------

describe("QaOverviewPage -- breaker tri-state", () => {
  beforeEach(() => {
    (useForceQaPatrol as AnyMock).mockReturnValue({ mutate: vi.fn(), isPending: false });
    (useQaPatrols as AnyMock).mockReturnValue({ data: { data: [] }, isLoading: false, isError: false });
    (useButlers as AnyMock).mockReturnValue({ data: { data: [] }, isLoading: false, isError: false });
    (useQaCases as AnyMock).mockReturnValue({
      data: { data: [MOCK_CASE_1] },
      isLoading: false,
      isError: false,
    });
  });

  // Mutation-strength: if breakerState regressed to `tripped ?? false`, an
  // errored summary would paint "Circuit breaker closed" and this fails.
  it("renders UNKNOWN (not closed) when the summary query errors", () => {
    (useQaSummary as AnyMock).mockReturnValue({ data: undefined, isLoading: false, isError: true });
    const html = renderPage();
    expect(html).toContain("Circuit breaker unknown");
    expect(html).toContain('aria-label="QA circuit breaker state unknown"');
    expect(html).not.toContain("Circuit breaker closed");
    expect(html).not.toContain("Reset breaker");
  });

  it("renders UNKNOWN (not closed) while the summary query is loading", () => {
    (useQaSummary as AnyMock).mockReturnValue({ data: undefined, isLoading: true, isError: false });
    const html = renderPage();
    expect(html).toContain("Circuit breaker unknown");
    expect(html).not.toContain("Circuit breaker closed");
    expect(html).not.toContain("Reset breaker");
  });

  it("renders CLOSED (not unknown) when the summary reports a healthy breaker", () => {
    (useQaSummary as AnyMock).mockReturnValue({
      data: { data: MOCK_SUMMARY },
      isLoading: false,
      isError: false,
    });
    const html = renderPage();
    expect(html).toContain("Circuit breaker closed");
    expect(html).not.toContain("Circuit breaker unknown");
    expect(html).not.toContain("Reset breaker");
  });

  it("renders TRIPPED with an enabled reset control when the breaker is open", () => {
    (useQaSummary as AnyMock).mockReturnValue({
      data: {
        data: {
          ...MOCK_SUMMARY,
          circuit_breaker: { tripped: true, consecutive_failures: 5 },
        },
      },
      isLoading: false,
      isError: false,
    });
    const html = renderPage();
    expect(html).toContain("Reset breaker");
    expect(html).toContain('aria-label="Reset QA circuit breaker"');
    expect(html).not.toContain("Circuit breaker unknown");
    expect(html).not.toContain("Circuit breaker closed");
  });
});

// ---------------------------------------------------------------------------
// Evidence-bearing reset confirm + palette verb (bu-533qx.2)
// ---------------------------------------------------------------------------

describe("QaOverviewPage -- evidence-bearing reset + palette verb", () => {
  let container: HTMLDivElement;
  let root: Root;

  function setupTripped() {
    (useQaSummary as AnyMock).mockReturnValue({
      data: {
        data: {
          ...MOCK_SUMMARY,
          staffer_status: "circuit_breaker_tripped",
          circuit_breaker: { tripped: true, consecutive_failures: 5 },
        },
      },
      isLoading: false,
      isError: false,
    });
    (useQaCircuitBreaker as AnyMock).mockReturnValue({
      data: { data: MOCK_BREAKER_TRIPPED },
      isLoading: false,
      isError: false,
    });
  }

  beforeEach(() => {
    vi.clearAllMocks();
    (useForceQaPatrol as AnyMock).mockReturnValue({ mutate: vi.fn(), isPending: false });
    (useResetQaCircuitBreaker as AnyMock).mockReturnValue({ mutate: vi.fn(), isPending: false });
    (useQaPatrols as AnyMock).mockReturnValue({ data: { data: [] }, isLoading: false, isError: false });
    (useButlers as AnyMock).mockReturnValue({ data: { data: [] }, isLoading: false, isError: false });
    (useQaCases as AnyMock).mockReturnValue({
      data: { data: [MOCK_CASE_1] },
      isLoading: false,
      isError: false,
    });
    (useRemoveDismissal as AnyMock).mockReturnValue({ mutate: vi.fn(), isPending: false });
    (useQaCase as AnyMock).mockReturnValue({ data: undefined, isLoading: false, isError: false });
    (useQaCaseJournal as AnyMock).mockReturnValue({ data: undefined });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    document.body.innerHTML = "";
  });

  function mount() {
    act(() => {
      root.render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <MemoryRouter initialEntries={["/qa"]}>
            <QaOverviewPage />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it("shows the five failing attempts in the reset confirm dialog when the breaker is tripped", () => {
    setupTripped();
    mount();

    // Dialog is closed until the operator opens it — no evidence leaks early.
    expect(document.querySelector('[data-testid="qa-breaker-reset-dialog"]')).toBeNull();

    const btn = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Reset QA circuit breaker"]',
    );
    expect(btn).not.toBeNull();
    act(() => {
      btn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // Evidence renders one row per failing attempt (the five that tripped it).
    const attempts = document.querySelectorAll('[data-testid="qa-breaker-reset-attempt"]');
    expect(attempts).toHaveLength(5);
    const evidenceText = document.querySelector(
      '[data-testid="qa-breaker-reset-evidence"]',
    )?.textContent;
    expect(evidenceText).toContain("attempt-0001");
    expect(evidenceText).toContain("unfixable");
  });

  it("does not register the reset palette verb while the breaker is closed", () => {
    (useQaSummary as AnyMock).mockReturnValue({
      data: { data: MOCK_SUMMARY },
      isLoading: false,
      isError: false,
    });
    (useQaCircuitBreaker as AnyMock).mockReturnValue({
      data: { data: MOCK_BREAKER_CLOSED },
      isLoading: false,
      isError: false,
    });

    let labels: string[] = [];
    act(() => {
      root.render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <CommandRegistryProvider>
            <MemoryRouter initialEntries={["/qa"]}>
              <QaOverviewPage />
            </MemoryRouter>
            <CommandLabelsProbe onLabels={(l) => (labels = l)} />
          </CommandRegistryProvider>
        </QueryClientProvider>,
      );
    });
    expect(labels).toContain("Force patrol");
    expect(labels).not.toContain("Reset circuit breaker");
  });

  it("registers the reset palette verb only while the breaker is tripped", () => {
    setupTripped();

    let labels: string[] = [];
    act(() => {
      root.render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <CommandRegistryProvider>
            <MemoryRouter initialEntries={["/qa"]}>
              <QaOverviewPage />
            </MemoryRouter>
            <CommandLabelsProbe onLabels={(l) => (labels = l)} />
          </CommandRegistryProvider>
        </QueryClientProvider>,
      );
    });
    expect(labels).toContain("Reset circuit breaker");
  });
});
