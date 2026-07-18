// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { ButlerVerdictOpener } from "@/components/butler-detail/ButlerVerdictOpener";

function render(overrides: Partial<React.ComponentProps<typeof ButlerVerdictOpener>> = {}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <ButlerVerdictOpener
        butlerName="general"
        activity="idle"
        sessions24h={6}
        boardLoading={false}
        boardError={false}
        spendToday={0.41}
        spendLoading={false}
        spendError={false}
        spendSourcesDegraded={[]}
        pendingApprovals={[]}
        pendingTotal={0}
        approvalsLoading={false}
        approvalsError={false}
        failedSessions={0}
        failedSessionsLoading={false}
        failedSessionsError={false}
        approvalSourcesDegraded={[]}
        failureSourcesDegraded={[]}
        {...overrides}
      />
    </MemoryRouter>,
  );
}

describe("ButlerVerdictOpener", () => {
  it("turns failed sessions and a pending approval into real drill-down doors", () => {
    const html = render({
      failedSessions: 2,
      pendingApprovals: [{ id: "approval-1" } as never],
      pendingTotal: 1,
    });

    expect(html).toContain('href="/sessions?status=failed&amp;butler=general"');
    expect(html).toContain('href="/approvals/approval-1"');
    expect(html).toContain("2 sessions failed in the last 24h");
  });

  it("names a degraded approval source and suppresses the nominal line", () => {
    const html = render({ approvalSourcesDegraded: ["relationship"] });

    expect(html).toContain("relationship unavailable; approvals may be incomplete");
    expect(html).not.toContain("butler-detail-verdict-all-clear");
  });
});
