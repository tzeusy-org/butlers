// @vitest-environment jsdom
/**
 * Tests for <QaVerdictOpener> (bu-qvnce.9, JARVIS pursuit move 9 slice 2).
 *
 * Verifies the /qa page opener composes GET /api/qa/summary's
 * staffer_status/circuit_breaker/credentials_status fields -- previously
 * fetched but never rendered (QaOverviewPage.tsx:239-247 before this
 * change) -- and honors the isError-suppression contract via DispatchVerdict.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { QaVerdictOpener } from "@/components/qa/QaVerdictOpener";
import type { QaSummary } from "@/api/types";

function render(ui: React.ReactElement): string {
  return renderToStaticMarkup(<MemoryRouter>{ui}</MemoryRouter>);
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

function summaryQuery(data: QaSummary | undefined, opts?: { isLoading?: boolean; isError?: boolean }) {
  return {
    data: data ? { data } : undefined,
    isLoading: opts?.isLoading ?? false,
    isError: opts?.isError ?? false,
  } as AnyMock;
}

const HEALTHY_SUMMARY: QaSummary = {
  staffer_status: "healthy",
  last_patrol_at: "2026-07-05T04:54:00Z",
  next_patrol_at: "2026-07-05T05:24:00Z",
  last_patrol: null,
  stats_24h: { patrols_completed: 1, total_findings: 0, novel_findings: 0, dispatched_investigations: 0, prs_opened: 0 },
  stats_all_time: {
    total_patrols: 10,
    total_findings: 2,
    novel_findings: 1,
    dispatched_investigations: 1,
    prs_merged: 1,
    prs_failed: 0,
    success_rate: 1,
  },
  kpis: {
    prs_landed_24h: 1,
    mttr_24h_seconds: 60,
    self_resolved_7d_pct: 100,
    active_cases_now: 0,
    failed_24h: 0,
    prs_landed_prior_24h: 0,
    mttr_prior_24h_seconds: null,
    self_resolved_prior_7d_pct: null,
    failed_prior_24h: 0,
  },
  active_breakdown: { awaiting_ci: 0, escalated_open_cases: 0 },
  active_sources: [],
  circuit_breaker: { tripped: false, consecutive_failures: 0, threshold: 5 },
  credentials_status: {
    gh_token_present: true,
    git_author_name_present: true,
    git_author_email_present: true,
    provisioning_hint: null,
  },
  port: 41110,
  model: "claude-sonnet-4-5",
  patrol_interval_minutes: 30,
  runtime_credential_alert: null,
};

// (2026-07-05T05:04:00Z is "now" for the relative-time assertions below.)
// formatRelativeCompact reads Date.now() directly; freeze it for deterministic output.
const NOW = new Date("2026-07-05T05:04:00Z");

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});
afterEach(() => {
  vi.useRealTimers();
});

describe("QaVerdictOpener -- all clear", () => {
  it("renders a calm line with last/next patrol relative times when nothing is wrong", () => {
    const html = render(<QaVerdictOpener summary={summaryQuery(HEALTHY_SUMMARY)} />);
    expect(html).toContain('data-testid="qa-verdict-all-clear"');
    expect(html).toContain("QA staffer healthy");
    expect(html).toContain("last patrol 10m ago");
    expect(html).toContain("next patrol in 20m");
  });
});

describe("QaVerdictOpener -- clauses", () => {
  it("names a tripped circuit breaker with its consecutive-failure count", () => {
    const summary: QaSummary = {
      ...HEALTHY_SUMMARY,
      staffer_status: "circuit_breaker_tripped",
      circuit_breaker: { tripped: true, consecutive_failures: 5 },
    };
    const html = render(<QaVerdictOpener summary={summaryQuery(summary)} />);
    expect(html).toContain('data-testid="qa-verdict-clauses"');
    expect(html).toContain("circuit breaker tripped after 5 consecutive failures");
  });

  it("names a failed last patrol and doors to its patrol detail page", () => {
    const summary: QaSummary = {
      ...HEALTHY_SUMMARY,
      staffer_status: "error",
      last_patrol: {
        id: "patrol-1",
        started_at: "2026-07-05T04:00:00Z",
        completed_at: "2026-07-05T04:05:00Z",
        status: "error",
        findings_count: 0,
        novel_count: 0,
        dispatched_count: 0,
        log_lookback_minutes: 60,
        sources_polled: [],
        error_detail: "boom",
      },
    };
    const html = render(<QaVerdictOpener summary={summaryQuery(summary)} />);
    expect(html).toContain("last patrol failed");
    expect(html).toContain('href="/qa/patrols/patrol-1"');
  });

  it("names an unknown persisted patrol status instead of rendering all clear", () => {
    const summary: QaSummary = {
      ...HEALTHY_SUMMARY,
      staffer_status: "unknown_patrol_status",
      last_patrol: {
        id: "patrol-future-status",
        started_at: "2026-07-05T04:00:00Z",
        completed_at: "2026-07-05T04:05:00Z",
        status: "future_status",
        findings_count: 0,
        novel_count: 0,
        dispatched_count: 0,
        log_lookback_minutes: 60,
        sources_polled: [],
        error_detail: null,
      },
    };

    const html = render(<QaVerdictOpener summary={summaryQuery(summary)} />);

    expect(html).toContain('data-testid="qa-verdict-clauses"');
    expect(html).toContain("latest patrol reported an unknown status");
    expect(html).toContain('href="/qa/patrols/patrol-future-status"');
    expect(html).not.toContain('data-testid="qa-verdict-all-clear"');
    expect(html).not.toContain("future_status");
  });

  it("names missing credentials using the backend's own provisioning hint", () => {
    const summary: QaSummary = {
      ...HEALTHY_SUMMARY,
      credentials_status: {
        gh_token_present: false,
        git_author_name_present: true,
        git_author_email_present: true,
        provisioning_hint: "BUTLERS_QA_GH_TOKEN is missing. Provision via: butler secrets set BUTLERS_QA_GH_TOKEN <token>",
      },
    };
    const html = render(<QaVerdictOpener summary={summaryQuery(summary)} />);
    expect(html).toContain("BUTLERS_QA_GH_TOKEN is missing");
  });

  it("names missing git author identity even when the GitHub token is present", () => {
    const summary: QaSummary = {
      ...HEALTHY_SUMMARY,
      credentials_status: {
        gh_token_present: true,
        git_author_name_present: false,
        git_author_email_present: true,
        provisioning_hint: null,
      },
    };
    const html = render(<QaVerdictOpener summary={summaryQuery(summary)} />);
    expect(html).toContain("git author identity missing");
  });

  it("does not treat an unrecognized/future staffer_status value as a problem", () => {
    const summary: QaSummary = { ...HEALTHY_SUMMARY, staffer_status: "claude-sonnet-4-5" };
    const html = render(<QaVerdictOpener summary={summaryQuery(summary)} />);
    expect(html).toContain('data-testid="qa-verdict-all-clear"');
  });

  it("names a pre-trip failure streak before the breaker actually opens", () => {
    const summary: QaSummary = {
      ...HEALTHY_SUMMARY,
      circuit_breaker: { tripped: false, consecutive_failures: 3, threshold: 5 },
    };
    const html = render(<QaVerdictOpener summary={summaryQuery(summary)} />);
    expect(html).toContain('data-testid="qa-verdict-clauses"');
    expect(html).toContain("3 consecutive failures, breaker opens at 5");
  });

  it("falls back to the known default threshold when the wire omits it", () => {
    const summary: QaSummary = {
      ...HEALTHY_SUMMARY,
      circuit_breaker: { tripped: false, consecutive_failures: 1 },
    };
    const html = render(<QaVerdictOpener summary={summaryQuery(summary)} />);
    expect(html).toContain("1 consecutive failure, breaker opens at 5");
  });

  it("names an overdue patrol from last_patrol_at + 2x the interval", () => {
    const summary: QaSummary = {
      ...HEALTHY_SUMMARY,
      // NOW is 2026-07-05T05:04:00Z; interval 30m means overdue past 1h --
      // 2h stale clears that bar.
      last_patrol_at: "2026-07-05T03:04:00Z",
      patrol_interval_minutes: 30,
    };
    const html = render(<QaVerdictOpener summary={summaryQuery(summary)} />);
    expect(html).toContain('data-testid="qa-verdict-clauses"');
    expect(html).toContain("patrol overdue");
  });

  it("does not name an overdue patrol inside the 2x interval window", () => {
    const summary: QaSummary = {
      ...HEALTHY_SUMMARY,
      last_patrol_at: "2026-07-05T04:34:00Z", // 30m ago, interval 30m -> not yet 2x
      patrol_interval_minutes: 30,
    };
    const html = render(<QaVerdictOpener summary={summaryQuery(summary)} />);
    expect(html).toContain('data-testid="qa-verdict-all-clear"');
  });

  it("names the runtime credential alert as the watcher-death signal", () => {
    const summary: QaSummary = {
      ...HEALTHY_SUMMARY,
      runtime_credential_alert: "refresh token was revoked",
    };
    const html = render(<QaVerdictOpener summary={summaryQuery(summary)} />);
    expect(html).toContain('data-testid="qa-verdict-clauses"');
    expect(html).toContain("runtime CLI credential may be unhealthy: refresh token was revoked");
  });
});

describe("QaVerdictOpener -- isError-suppression contract", () => {
  it("renders the skeleton while the summary is loading", () => {
    const html = render(<QaVerdictOpener summary={summaryQuery(undefined, { isLoading: true })} />);
    expect(html).toContain('data-testid="qa-verdict-skeleton"');
  });

  it("names the summary source as unavailable and never renders the all-clear when it errors", () => {
    const html = render(<QaVerdictOpener summary={summaryQuery(undefined, { isError: true })} />);
    expect(html).toContain('data-testid="qa-verdict-clauses"');
    expect(html).toContain("QA summary unavailable");
    expect(html).not.toContain("QA staffer healthy");
  });
});
