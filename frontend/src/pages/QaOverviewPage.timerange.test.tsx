// @vitest-environment jsdom

/**
 * Interactive tests for QA dossier controls.
 *
 * Verified behaviors:
 *   - Default time range is 7d and is reflected in the case-list label.
 *   - Clicking a different preset re-runs useQaCases with the new `since` value
 *     and updates the case-list header label accordingly.
 *   - Selecting "All" passes the literal string "all" to the hook (the API
 *     accepts since=all per QaCasesParams).
 *   - The circuit-breaker reset button confirms with the operator, then calls
 *     the existing reset mutation.
 *   - The closed circuit-breaker button is visible but inert.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

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

import QaOverviewPage from "@/pages/QaOverviewPage";
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

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

const MOCK_SUMMARY = {
  staffer_status: "claude-sonnet-4-5",
  last_patrol_at: null,
  next_patrol_at: null,
  last_patrol: null,
  stats_24h: {
    patrols_completed: 0,
    total_findings: 0,
    novel_findings: 0,
    dispatched_investigations: 0,
  },
  stats_all_time: { total_patrols: 0, dispatched_investigations: 0 },
  kpis: {
    prs_landed_24h: 0,
    mttr_24h_seconds: 0,
    self_resolved_7d_pct: 0,
    active_cases_now: 0,
  },
  active_breakdown: { awaiting_ci: 0, escalated_open_cases: 0 },
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

const MOCK_CASE = {
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

function renderPage({
  summary = MOCK_SUMMARY,
  resetCircuitBreaker = { mutate: vi.fn(), isPending: false },
  circuitBreaker = MOCK_BREAKER_CLOSED,
}: {
  summary?: typeof MOCK_SUMMARY;
  resetCircuitBreaker?: { mutate: ReturnType<typeof vi.fn>; isPending: boolean };
  circuitBreaker?: typeof MOCK_BREAKER_CLOSED | typeof MOCK_BREAKER_TRIPPED;
} = {}) {
  (useQaSummary as AnyMock).mockReturnValue({
    data: { data: summary },
    isLoading: false,
    isError: false,
  });
  (useQaCases as AnyMock).mockReturnValue({
    data: { data: [MOCK_CASE] },
    isLoading: false,
    isError: false,
  });
  (useQaCase as AnyMock).mockReturnValue({ data: undefined, isLoading: false, isError: false });
  (useQaCaseJournal as AnyMock).mockReturnValue({ data: undefined });
  (useRemoveDismissal as AnyMock).mockReturnValue({ mutate: vi.fn(), isPending: false });
  (useForceQaPatrol as AnyMock).mockReturnValue({ mutate: vi.fn(), isPending: false });
  (useResetQaCircuitBreaker as AnyMock).mockReturnValue(resetCircuitBreaker);
  (useQaCircuitBreaker as AnyMock).mockReturnValue({
    data: { data: circuitBreaker },
    isLoading: false,
    isError: false,
  });
  (useQaPatrols as AnyMock).mockReturnValue({ data: { data: [] }, isLoading: false, isError: false });
  (useButlers as AnyMock).mockReturnValue({ data: { data: [] }, isLoading: false, isError: false });

  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/qa"]}>
        <QaOverviewPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("QaOverviewPage -- time range selector", () => {
  beforeEach(() => {
    (useQaCases as AnyMock).mockClear();
  });

  it("defaults to 7d and labels the case list 'Cases · last 7d'", () => {
    renderPage();
    expect(screen.getByText("Cases · last 7d")).toBeTruthy();

    const lastCallArgs = (useQaCases as AnyMock).mock.calls.at(-1)?.[0];
    expect(lastCallArgs).toMatchObject({ since: "7d" });
  });

  it("switches to 24h, updates the label, and re-queries the hook", () => {
    renderPage();

    const group = screen.getByRole("group", { name: "Time range" });
    fireEvent.click(within(group).getByRole("button", { name: "24h" }));

    expect(screen.getByText("Cases · last 24h")).toBeTruthy();

    const lastCallArgs = (useQaCases as AnyMock).mock.calls.at(-1)?.[0];
    expect(lastCallArgs).toMatchObject({ since: "24h" });
  });

  it("switches to 30d and updates the label and hook", () => {
    renderPage();

    const group = screen.getByRole("group", { name: "Time range" });
    fireEvent.click(within(group).getByRole("button", { name: "30d" }));

    expect(screen.getByText("Cases · last 30d")).toBeTruthy();

    const lastCallArgs = (useQaCases as AnyMock).mock.calls.at(-1)?.[0];
    expect(lastCallArgs).toMatchObject({ since: "30d" });
  });

  it("switches to 'All' and passes since='all' to the hook", () => {
    renderPage();

    const group = screen.getByRole("group", { name: "Time range" });
    fireEvent.click(within(group).getByRole("button", { name: "All" }));

    expect(screen.getByText("Cases · all cases")).toBeTruthy();

    const lastCallArgs = (useQaCases as AnyMock).mock.calls.at(-1)?.[0];
    expect(lastCallArgs).toMatchObject({ since: "all" });
  });
});

describe("QaOverviewPage -- circuit breaker reset", () => {
  it("shows an inert closed-state button when the breaker is closed", () => {
    const mutate = vi.fn();

    renderPage({ resetCircuitBreaker: { mutate, isPending: false } });

    const button = screen.getByRole("button", {
      name: "QA circuit breaker closed",
    }) as HTMLButtonElement;
    expect(button.textContent).toBe("Circuit breaker closed");
    expect(button.disabled).toBe(true);

    fireEvent.click(button);

    // Closed is inert -- no confirm dialog, no mutation.
    expect(screen.queryByTestId("qa-breaker-reset-dialog")).toBeNull();
    expect(mutate).not.toHaveBeenCalled();
  });

  // Reset now opens an evidence-bearing AlertDialog instead of firing a bare
  // window.confirm (bu-533qx.2) -- the operator must see the failing attempts
  // before the mutation dispatches.
  it("opens the evidence-bearing confirm dialog, then dispatches the reset mutation on confirm", () => {
    const mutate = vi.fn();

    renderPage({
      summary: {
        ...MOCK_SUMMARY,
        staffer_status: "circuit_breaker_tripped",
        circuit_breaker: { tripped: true, consecutive_failures: 5 },
      },
      resetCircuitBreaker: { mutate, isPending: false },
      circuitBreaker: MOCK_BREAKER_TRIPPED,
    });

    fireEvent.click(screen.getByRole("button", { name: "Reset QA circuit breaker" }));

    expect(screen.getByTestId("qa-breaker-reset-dialog")).toBeTruthy();
    expect(mutate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("qa-breaker-reset-confirm"));

    expect(mutate).toHaveBeenCalledOnce();
  });
});
