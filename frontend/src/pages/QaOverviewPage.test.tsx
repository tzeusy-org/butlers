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
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

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
  useQaPatrols,
} from "@/hooks/use-qa";
import { useButlers } from "@/hooks/use-butlers";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

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

  it("renders nothing while patrols are loading or on error (no fabricated strip)", () => {
    (useQaPatrols as AnyMock).mockReturnValue({ data: undefined, isLoading: true, isError: false });
    const loadingHtml = renderPage();
    expect(loadingHtml).not.toContain("Recent patrols");

    (useQaPatrols as AnyMock).mockReturnValue({ data: undefined, isLoading: false, isError: true });
    const errorHtml = renderPage();
    expect(errorHtml).not.toContain("Recent patrols");
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
