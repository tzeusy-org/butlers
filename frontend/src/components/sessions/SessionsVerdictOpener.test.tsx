// @vitest-environment jsdom
/**
 * Tests for <SessionsVerdictOpener> (bu-y0v0c, JARVIS pursuit move 9
 * slice 3).
 *
 * Verifies the /sessions opener composes the window-scoped failure
 * aggregate + nearest-running-session data into "N sessions failed in the
 * last 24h, clustered on X; nearest running session Ym elapsed" -- and
 * honors the isError-suppression contract.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import {
  SessionsVerdictOpener,
  SESSIONS_VERDICT_WINDOW_HOURS,
} from "@/components/sessions/SessionsVerdictOpener";
import type { SessionAggregate, SessionSummary } from "@/api/index.ts";

function render(ui: React.ReactElement): string {
  return renderToStaticMarkup(<MemoryRouter>{ui}</MemoryRouter>);
}

function aggregate(overrides: Partial<SessionAggregate> = {}): SessionAggregate {
  return {
    total: 0,
    success_count: 0,
    failed_count: 0,
    running_count: 0,
    success_rate: null,
    input_tokens: 0,
    output_tokens: 0,
    by_butler: [],
    by_trigger_source: [],
    ...overrides,
  };
}

function runningSession(overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    id: "s-1",
    butler: "chronicler",
    prompt: "p",
    trigger_source: "schedule",
    success: null,
    started_at: "2026-07-05T04:48:00Z",
    completed_at: null,
    duration_ms: null,
    input_tokens: null,
    output_tokens: null,
    ...overrides,
  };
}

const NOW = new Date("2026-07-05T05:00:00Z");

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});
afterEach(() => {
  vi.useRealTimers();
});

describe("SessionsVerdictOpener -- all clear", () => {
  it("renders the calm line when nothing failed and nothing is running", () => {
    const html = render(
      <SessionsVerdictOpener
        failedAggregate={aggregate()}
        failedLoading={false}
        failedError={false}
        runningSessions={[]}
        runningLoading={false}
        runningError={false}
      />,
    );
    expect(html).toContain('data-testid="sessions-verdict-all-clear"');
    expect(html).toContain(`No sessions failed in the last ${SESSIONS_VERDICT_WINDOW_HOURS}h.`);
  });

  it("folds the running-session note into the calm line when nothing failed", () => {
    const html = render(
      <SessionsVerdictOpener
        failedAggregate={aggregate()}
        failedLoading={false}
        failedError={false}
        runningSessions={[runningSession({ started_at: "2026-07-05T04:48:00Z" })]}
        runningLoading={false}
        runningError={false}
      />,
    );
    expect(html).toContain('data-testid="sessions-verdict-all-clear"');
    expect(html).toContain(
      `No sessions failed in the last ${SESSIONS_VERDICT_WINDOW_HOURS}h; nearest running session 12m elapsed`,
    );
  });
});

describe("SessionsVerdictOpener -- clauses", () => {
  it("composes failed count, dominant butler cluster, and nearest running session as doors", () => {
    const agg = aggregate({
      total: 5,
      failed_count: 5,
      by_butler: [
        { butler: "chronicler", count: 4 },
        { butler: "finance", count: 1 },
      ],
      by_trigger_source: [{ trigger_source: "schedule", count: 3 }],
    });
    const html = render(
      <SessionsVerdictOpener
        failedAggregate={agg}
        failedLoading={false}
        failedError={false}
        runningSessions={[runningSession({ id: "s-9", started_at: "2026-07-05T04:48:00Z" })]}
        runningLoading={false}
        runningError={false}
      />,
    );
    expect(html).toContain('data-testid="sessions-verdict-clauses"');
    expect(html).toContain(
      `5 sessions failed in the last ${SESSIONS_VERDICT_WINDOW_HOURS}h, clustered on chronicler`,
    );
    expect(html).toContain('href="/sessions?status=failed&amp;butler=chronicler"');
    expect(html).toContain("nearest running session 12m elapsed");
    expect(html).toContain('href="/sessions/s-9"');
  });

  it("clusters on trigger_source when it is more concentrated than any single butler", () => {
    const agg = aggregate({
      total: 6,
      failed_count: 6,
      by_butler: [
        { butler: "chronicler", count: 3 },
        { butler: "finance", count: 3 },
      ],
      by_trigger_source: [{ trigger_source: "cron:daily-digest", count: 5 }],
    });
    const html = render(
      <SessionsVerdictOpener
        failedAggregate={agg}
        failedLoading={false}
        failedError={false}
        runningSessions={[]}
        runningLoading={false}
        runningError={false}
      />,
    );
    expect(html).toContain("clustered on cron:daily-digest");
    expect(html).toContain('href="/sessions?status=failed&amp;trigger=cron%3Adaily-digest"');
  });

  it("pluralizes correctly for a single failure and omits the running clause with no running session", () => {
    const agg = aggregate({
      total: 1,
      failed_count: 1,
      by_butler: [{ butler: "finance", count: 1 }],
    });
    const html = render(
      <SessionsVerdictOpener
        failedAggregate={agg}
        failedLoading={false}
        failedError={false}
        runningSessions={[]}
        runningLoading={false}
        runningError={false}
      />,
    );
    expect(html).toContain(`1 session failed in the last ${SESSIONS_VERDICT_WINDOW_HOURS}h`);
    expect(html).not.toContain("nearest running session");
  });
});

describe("SessionsVerdictOpener -- degraded fan-out (bu-tpudw.2)", () => {
  it("names the degraded pools and suppresses the all-clear when sources_degraded is set", () => {
    // The reachable pools report zero failures, but a pool dropped from the
    // fan-out (meta.sources_degraded). An empty window here is NOT a truthful
    // all-clear — the down pool is named and the calm line is suppressed.
    const html = render(
      <SessionsVerdictOpener
        failedAggregate={aggregate()}
        failedLoading={false}
        failedError={false}
        failedSourcesDegraded={["finance", "health"]}
        runningSessions={[]}
        runningLoading={false}
        runningError={false}
      />,
    );
    expect(html).toContain('data-testid="sessions-verdict-clauses"');
    expect(html).toContain("finance, health unreachable — some failures may be missing");
    expect(html).not.toContain("No sessions failed");
  });

  it("keeps the calm all-clear when sources_degraded is empty", () => {
    const html = render(
      <SessionsVerdictOpener
        failedAggregate={aggregate()}
        failedLoading={false}
        failedError={false}
        failedSourcesDegraded={[]}
        runningSessions={[]}
        runningLoading={false}
        runningError={false}
      />,
    );
    expect(html).toContain('data-testid="sessions-verdict-all-clear"');
    expect(html).not.toContain("unreachable");
  });

  it("prepends the degraded clause ahead of a real failure clause", () => {
    const agg = aggregate({
      total: 2,
      failed_count: 2,
      by_butler: [{ butler: "chronicler", count: 2 }],
    });
    const html = render(
      <SessionsVerdictOpener
        failedAggregate={agg}
        failedLoading={false}
        failedError={false}
        failedSourcesDegraded={["finance"]}
        runningSessions={[]}
        runningLoading={false}
        runningError={false}
      />,
    );
    const degradedIdx = html.indexOf("finance unreachable");
    const failedIdx = html.indexOf("2 sessions failed");
    expect(degradedIdx).toBeGreaterThanOrEqual(0);
    expect(failedIdx).toBeGreaterThanOrEqual(0);
    expect(degradedIdx).toBeLessThan(failedIdx);
  });
});

describe("SessionsVerdictOpener -- isError-suppression contract", () => {
  it("renders the skeleton while either source is loading", () => {
    const html = render(
      <SessionsVerdictOpener
        failedAggregate={undefined}
        failedLoading
        failedError={false}
        runningSessions={[]}
        runningLoading={false}
        runningError={false}
      />,
    );
    expect(html).toContain('data-testid="sessions-verdict-skeleton"');
  });

  it("names an errored failure-aggregate source and never renders the all-clear alongside it", () => {
    const html = render(
      <SessionsVerdictOpener
        failedAggregate={undefined}
        failedLoading={false}
        failedError
        runningSessions={[]}
        runningLoading={false}
        runningError={false}
      />,
    );
    expect(html).toContain('data-testid="sessions-verdict-clauses"');
    expect(html).toContain("session failures unavailable");
    expect(html).not.toContain("No sessions failed");
  });

  it("names an errored running-sessions source independently", () => {
    const html = render(
      <SessionsVerdictOpener
        failedAggregate={aggregate()}
        failedLoading={false}
        failedError={false}
        runningSessions={[]}
        runningLoading={false}
        runningError
      />,
    );
    expect(html).toContain("running sessions unavailable");
  });
});
