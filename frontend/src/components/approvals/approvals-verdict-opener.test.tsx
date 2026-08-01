// @vitest-environment jsdom
/**
 * Tests for <ApprovalsVerdictOpener> (bu-qvnce.9, JARVIS pursuit move 9
 * slice 2).
 *
 * Verifies the /approvals opener composes the already-fetched pending queue
 * + whole-population stalled-radar metadata into "N waiting; nearest expires
 * in Xm; N stalled actions never ran" -- and honors the
 * isError-suppression contract.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { ApprovalsVerdictOpener } from "@/components/approvals/approvals-verdict-opener.tsx";
import type { ApprovalSummary } from "@/api/index.ts";

function render(ui: React.ReactElement): string {
  return renderToStaticMarkup(<MemoryRouter>{ui}</MemoryRouter>);
}

function summary(overrides: Partial<ApprovalSummary>): ApprovalSummary {
  return {
    id: "a-1",
    butler: "general",
    tool_name: "send_email",
    status: "pending",
    created_at: "2026-07-05T04:00:00Z",
    expires_at: null,
    why: null,
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

describe("ApprovalsVerdictOpener -- all clear", () => {
  it("renders the calm line when nothing is waiting and nothing is stalled", () => {
    const html = render(
      <ApprovalsVerdictOpener
        pending={[]}
        pendingLoading={false}
        pendingError={false}
        historyLoading={false}
        historyError={false}
      />,
    );
    expect(html).toContain('data-testid="approvals-verdict-all-clear"');
    expect(html).toContain("No approvals waiting.");
  });
});

describe("ApprovalsVerdictOpener -- clauses", () => {
  it("composes waiting count, nearest expiry, and whole-population stalled count", () => {
    const pending = [
      summary({ id: "a-1", expires_at: "2026-07-05T05:40:00Z" }),
      summary({ id: "a-2", expires_at: "2026-07-06T05:00:00Z" }),
      summary({ id: "a-3", expires_at: null }),
    ];
    const html = render(
      <ApprovalsVerdictOpener
        pending={pending}
        pendingLoading={false}
        pendingError={false}
        stalledCount={1}
        historyLoading={false}
        historyError={false}
      />,
    );
    expect(html).toContain('data-testid="approvals-verdict-clauses"');
    expect(html).toContain("3 waiting");
    expect(html).toContain('href="/approvals"');
    // Nearest of the two dated items is a-1 (expires in 40m).
    expect(html).toContain("nearest expires in 40m");
    expect(html).toContain('href="/approvals/a-1"');
    expect(html).toContain("one stalled action never ran");
    // A whole-population aggregate has no fabricated row-level destination.
    expect(html).not.toContain('href="/approvals/h-1"');
  });

  it("renders all-clear when stalled radar metadata is zero", () => {
    const html = render(
      <ApprovalsVerdictOpener
        pending={[]}
        pendingLoading={false}
        pendingError={false}
        stalledCount={0}
        historyLoading={false}
        historyError={false}
      />,
    );
    // The bounded history is not an input to this component: only the
    // server-derived stalled radar covers the full eligible population.
    expect(html).not.toContain("stalled");
    expect(html).toContain("No approvals waiting.");
  });

  it("surfaces a stalled count beyond the bounded history", () => {
    const html = render(
      <ApprovalsVerdictOpener
        pending={[]}
        pendingLoading={false}
        pendingError={false}
        stalledCount={2}
        historyLoading={false}
        historyError={false}
      />,
    );
    expect(html).toContain("2 stalled actions never ran");
    expect(html).not.toContain("No approvals waiting.");
  });

  it("never renders the literal word 'approved' -- this page renders that status as 'stalled' everywhere (bu-86c4c.12 doctrine)", () => {
    const html = render(
      <ApprovalsVerdictOpener
        pending={[]}
        pendingLoading={false}
        pendingError={false}
        stalledCount={1}
        historyLoading={false}
        historyError={false}
      />,
    );
    expect(html).not.toContain("approved");
    expect(html).toContain("stalled");
  });

  it("pluralizes the stalled-approval clause when more than one never ran", () => {
    const html = render(
      <ApprovalsVerdictOpener
        pending={[]}
        pendingLoading={false}
        pendingError={false}
        stalledCount={2}
        historyLoading={false}
        historyError={false}
      />,
    );
    expect(html).toContain("2 stalled actions never ran");
  });

  it("does not surface a nearest-expiry clause when no pending item carries an expiry", () => {
    const pending = [summary({ id: "a-1", expires_at: null })];
    const html = render(
      <ApprovalsVerdictOpener
        pending={pending}
        pendingLoading={false}
        pendingError={false}
        historyLoading={false}
        historyError={false}
      />,
    );
    expect(html).toContain("1 waiting");
    expect(html).not.toContain("nearest expires");
  });
});

describe("ApprovalsVerdictOpener -- degraded-source honesty (bu-jad4j.4)", () => {
  it("names the degraded pools and suppresses the all-clear when sources_degraded is set", () => {
    // Degraded fan-out: the backend answered 200 but dropped one or more butler
    // pools, naming them in meta.sources_degraded. An empty queue here is NOT a
    // truthful "No approvals waiting." -- the opener names the dropped pools
    // instead so a downed pool never reads as a clear queue.
    const html = render(
      <ApprovalsVerdictOpener
        pending={[]}
        pendingLoading={false}
        pendingError={false}
        pendingSourcesDegraded={["finance", "home"]}
        historyLoading={false}
        historyError={false}
      />,
    );
    expect(html).toContain('data-testid="approvals-verdict-clauses"');
    expect(html).toContain("finance, home unreachable");
    expect(html).toContain("some approvals may be missing");
    // The calm all-clear line must NOT render alongside a degraded source.
    expect(html).not.toContain("No approvals waiting.");
    expect(html).not.toContain('data-testid="approvals-verdict-all-clear"');
  });

  it("dedupes queue + history degraded pools into one named clause", () => {
    // A butler down for the queue is almost always down for history too; the
    // opener merges both lists so the same pool is not named twice.
    const html = render(
      <ApprovalsVerdictOpener
        pending={[]}
        pendingLoading={false}
        pendingError={false}
        pendingSourcesDegraded={["finance"]}
        historyLoading={false}
        historyError={false}
        historySourcesDegraded={["finance", "home"]}
      />,
    );
    // "finance" appears once in the clause, not twice.
    const occurrences = html.split("finance").length - 1;
    expect(occurrences).toBe(1);
    expect(html).toContain("finance, home unreachable");
  });

  it("keeps the honest all-clear when no sources are degraded (mutation guard)", () => {
    // The degraded clause must depend on the flag: with every pool answering,
    // an empty queue is a legitimate all-clear.
    const html = render(
      <ApprovalsVerdictOpener
        pending={[]}
        pendingLoading={false}
        pendingError={false}
        pendingSourcesDegraded={[]}
        historyLoading={false}
        historyError={false}
        historySourcesDegraded={[]}
      />,
    );
    expect(html).toContain('data-testid="approvals-verdict-all-clear"');
    expect(html).toContain("No approvals waiting.");
    expect(html).not.toContain("unreachable");
  });

  it("names degraded pools alongside real waiting clauses (both render)", () => {
    // A partial fan-out that still returned rows: the waiting clause and the
    // degraded clause coexist, degraded first (source-health leads).
    const html = render(
      <ApprovalsVerdictOpener
        pending={[summary({ id: "a-1" })]}
        pendingLoading={false}
        pendingError={false}
        pendingSourcesDegraded={["home"]}
        historyLoading={false}
        historyError={false}
      />,
    );
    expect(html).toContain("home unreachable");
    expect(html).toContain("1 waiting");
    // Degraded clause precedes the waiting clause in DOM order.
    expect(html.indexOf("home unreachable")).toBeLessThan(html.indexOf("1 waiting"));
  });
});

describe("ApprovalsVerdictOpener -- isError-suppression contract", () => {
  it("renders the skeleton while either source is loading", () => {
    const html = render(
      <ApprovalsVerdictOpener
        pending={[]}
        pendingLoading
        pendingError={false}
        historyLoading={false}
        historyError={false}
      />,
    );
    expect(html).toContain('data-testid="approvals-verdict-skeleton"');
  });

  it("names an errored queue source and never renders the all-clear alongside it", () => {
    const html = render(
      <ApprovalsVerdictOpener
        pending={[]}
        pendingLoading={false}
        pendingError
        historyLoading={false}
        historyError={false}
      />,
    );
    expect(html).toContain('data-testid="approvals-verdict-clauses"');
    expect(html).toContain("approvals queue unavailable");
    expect(html).not.toContain("No approvals waiting.");
  });

  it("names an errored history source independently of the queue source", () => {
    const html = render(
      <ApprovalsVerdictOpener
        pending={[]}
        pendingLoading={false}
        pendingError={false}
        historyLoading={false}
        historyError
      />,
    );
    expect(html).toContain("approval history unavailable");
  });
});
