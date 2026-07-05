// @vitest-environment jsdom
/**
 * Tests for <ApprovalsVerdictOpener> (bu-qvnce.9, JARVIS pursuit move 9
 * slice 2).
 *
 * Verifies the /approvals opener composes the already-fetched pending queue
 * + decided-history data into "N waiting; nearest expires in Xm; N approved
 * actions never ran" -- and honors the isError-suppression contract.
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
        history={[]}
        historyLoading={false}
        historyError={false}
      />,
    );
    expect(html).toContain('data-testid="approvals-verdict-all-clear"');
    expect(html).toContain("No approvals waiting.");
  });
});

describe("ApprovalsVerdictOpener -- clauses", () => {
  it("composes waiting count, nearest expiry, and stalled-approval count as doors", () => {
    const pending = [
      summary({ id: "a-1", expires_at: "2026-07-05T05:40:00Z" }),
      summary({ id: "a-2", expires_at: "2026-07-06T05:00:00Z" }),
      summary({ id: "a-3", expires_at: null }),
    ];
    const history = [summary({ id: "h-1", status: "approved" }), summary({ id: "h-2", status: "executed" })];
    const html = render(
      <ApprovalsVerdictOpener
        pending={pending}
        pendingLoading={false}
        pendingError={false}
        history={history}
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
    expect(html).toContain('href="/approvals/h-1"');
  });

  it("never renders the literal word 'approved' -- this page renders that status as 'stalled' everywhere (bu-86c4c.12 doctrine)", () => {
    const history = [summary({ id: "h-1", status: "approved" })];
    const html = render(
      <ApprovalsVerdictOpener
        pending={[]}
        pendingLoading={false}
        pendingError={false}
        history={history}
        historyLoading={false}
        historyError={false}
      />,
    );
    expect(html).not.toContain("approved");
    expect(html).toContain("stalled");
  });

  it("pluralizes the stalled-approval clause when more than one never ran", () => {
    const history = [
      summary({ id: "h-1", status: "approved" }),
      summary({ id: "h-2", status: "approved" }),
    ];
    const html = render(
      <ApprovalsVerdictOpener
        pending={[]}
        pendingLoading={false}
        pendingError={false}
        history={history}
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
        history={[]}
        historyLoading={false}
        historyError={false}
      />,
    );
    expect(html).toContain("1 waiting");
    expect(html).not.toContain("nearest expires");
  });
});

describe("ApprovalsVerdictOpener -- isError-suppression contract", () => {
  it("renders the skeleton while either source is loading", () => {
    const html = render(
      <ApprovalsVerdictOpener
        pending={[]}
        pendingLoading
        pendingError={false}
        history={[]}
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
        history={[]}
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
        history={[]}
        historyLoading={false}
        historyError
      />,
    );
    expect(html).toContain("approval history unavailable");
  });
});
