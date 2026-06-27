/**
 * Tests for QaOverviewPage (dossier shell, bu-21uf7).
 *
 * Verifies:
 * - Page renders the sticky top bar, KPI strip, and two-pane body
 * - URL-driven case selection: ?case=<id> selects that case in CaseList
 * - Empty state renders "Nothing in the dossier." when cases list is empty
 * - Error state renders "Couldn't reach the staffer." on API failure
 * - Severity filter buttons are present and accessible
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
}));

vi.mock("@/hooks/useDarkMode", () => ({
  useDarkMode: vi.fn(() => ({
    theme: "dark",
    setTheme: vi.fn(),
    resolvedTheme: "dark",
  })),
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
} from "@/hooks/use-qa";

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
});
