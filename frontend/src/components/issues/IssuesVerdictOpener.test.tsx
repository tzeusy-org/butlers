// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { IssuesVerdictOpener } from "@/components/issues/IssuesVerdictOpener";

function render(ui: React.ReactElement): string {
  return renderToStaticMarkup(<MemoryRouter>{ui}</MemoryRouter>);
}

const criticalIssue = {
  severity: "critical",
  issue_key: "critical-1",
} as never;

describe("IssuesVerdictOpener", () => {
  it("turns a critical count into its existing URL-backed severity filter", () => {
    const html = render(
      <IssuesVerdictOpener
        issues={[criticalIssue]}
        isLoading={false}
        isError={false}
        activeWindow="7d"
        showDismissed={false}
        sourcesDegraded={[]}
        auditGroupsTruncated={false}
      />,
    );

    expect(html).toContain("1 critical issue need review");
    expect(html).toContain('href="/issues?window=7d&amp;severity=critical"');
  });

  it("names a partial issue feed and suppresses the calm empty verdict", () => {
    const html = render(
      <IssuesVerdictOpener
        issues={[]}
        isLoading={false}
        isError={false}
        activeWindow="7d"
        showDismissed={false}
        sourcesDegraded={["audit-groups"]}
        auditGroupsTruncated={false}
      />,
    );

    expect(html).toContain("audit-groups unavailable; issue feed may be incomplete");
    expect(html).not.toContain("issues-verdict-all-clear");
  });
});
