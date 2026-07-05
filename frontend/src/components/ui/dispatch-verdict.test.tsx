// @vitest-environment jsdom
/**
 * Tests for the shared <DispatchVerdict> page-opener primitive (bu-qvnce.9,
 * JARVIS pursuit move 9).
 *
 * Covers the three variants (skeleton / all-clear / clauses) and the
 * isError-suppression contract: a source that has errored must always
 * contribute its own named clause and must never allow the calm `allClear`
 * line to render alongside it.
 */

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { DispatchVerdict, type VerdictClause, type VerdictSource } from "@/components/ui/dispatch-verdict";

function render(ui: React.ReactElement): string {
  return renderToStaticMarkup(<MemoryRouter>{ui}</MemoryRouter>);
}

const HEALTHY_SOURCE: VerdictSource = { label: "fleet status", isLoading: false, isError: false };

describe("DispatchVerdict -- skeleton", () => {
  it("renders the skeleton when any source is still loading, even if others have data", () => {
    const html = render(
      <DispatchVerdict
        testId="qa"
        landmarkLabel="QA verdict"
        sources={[HEALTHY_SOURCE, { label: "QA summary", isLoading: true, isError: false }]}
        clauses={[]}
        allClear="QA staffer healthy"
      />,
    );
    expect(html).toContain('data-testid="qa-verdict-skeleton"');
    expect(html).toContain("Loading QA verdict");
    expect(html).not.toContain("QA staffer healthy");
  });
});

describe("DispatchVerdict -- all-clear", () => {
  it("renders the calm line when there are no clauses and no source errors", () => {
    const html = render(
      <DispatchVerdict
        testId="qa"
        landmarkLabel="QA verdict"
        sources={[HEALTHY_SOURCE]}
        clauses={[]}
        allClear="QA staffer healthy: last patrol 6m ago, next in 24m"
      />,
    );
    expect(html).toContain('data-testid="qa-verdict-all-clear"');
    expect(html).toContain("QA staffer healthy: last patrol 6m ago, next in 24m");
    expect(html).toContain('role="status"');
    expect(html).not.toContain('data-testid="qa-verdict-clauses"');
  });
});

describe("DispatchVerdict -- clauses", () => {
  it("renders each clause and turns clauses with an href into real doors", () => {
    const clauses: VerdictClause[] = [
      { key: "waiting", text: "3 waiting", href: "/approvals" },
      { key: "nearest", text: "nearest expires in 40m", href: "/approvals/abc" },
      { key: "stalled", text: "one approved action never ran" },
    ];
    const html = render(
      <DispatchVerdict
        testId="approvals"
        landmarkLabel="Approvals verdict"
        sources={[HEALTHY_SOURCE]}
        clauses={clauses}
        allClear="No approvals waiting."
      />,
    );
    expect(html).toContain('data-testid="approvals-verdict-clauses"');
    expect(html).toContain("3 waiting");
    expect(html).toContain('href="/approvals"');
    expect(html).toContain("nearest expires in 40m");
    expect(html).toContain('href="/approvals/abc"');
    expect(html).toContain("one approved action never ran");
    expect(html).not.toContain("No approvals waiting.");
  });

  it("uses a default clauses aria-label derived from landmarkLabel when none is given", () => {
    const html = render(
      <DispatchVerdict
        testId="approvals"
        landmarkLabel="Approvals verdict"
        sources={[HEALTHY_SOURCE]}
        clauses={[{ key: "waiting", text: "3 waiting" }]}
        allClear="No approvals waiting."
      />,
    );
    expect(html).toContain('aria-label="Approvals verdict needs attention"');
  });
});

describe("DispatchVerdict -- isError-suppression contract (bu-qvnce.1 / move 1)", () => {
  it("contributes a named clause for an errored source and never renders the all-clear alongside it", () => {
    const html = render(
      <DispatchVerdict
        testId="spend"
        landmarkLabel="Spend verdict"
        sources={[{ label: "spend summary", isLoading: false, isError: true }]}
        clauses={[]}
        allClear="On pace: $12.00/day"
      />,
    );
    expect(html).toContain('data-testid="spend-verdict-clauses"');
    expect(html).toContain("spend summary unavailable");
    expect(html).not.toContain("On pace: $12.00/day");
    expect(html).not.toContain('data-testid="spend-verdict-all-clear"');
  });

  it("prepends source-error clauses ahead of caller-provided clauses", () => {
    const html = render(
      <DispatchVerdict
        testId="spend"
        landmarkLabel="Spend verdict"
        sources={[{ label: "spend summary", isLoading: false, isError: true }]}
        clauses={[{ key: "over-ceiling", text: "projected spend exceeds the ceiling" }]}
        allClear="On pace"
      />,
    );
    const errorIdx = html.indexOf("spend summary unavailable");
    const clauseIdx = html.indexOf("projected spend exceeds the ceiling");
    expect(errorIdx).toBeGreaterThan(-1);
    expect(clauseIdx).toBeGreaterThan(errorIdx);
  });

  it("still surfaces a healthy source's real clauses even while a sibling source is errored", () => {
    const html = render(
      <DispatchVerdict
        testId="spend"
        landmarkLabel="Spend verdict"
        sources={[
          { label: "forecast", isLoading: false, isError: false },
          { label: "prior window", isLoading: false, isError: true },
        ]}
        clauses={[{ key: "over-ceiling", text: "projected spend exceeds the ceiling" }]}
        allClear="On pace"
      />,
    );
    expect(html).toContain("prior window unavailable");
    expect(html).toContain("projected spend exceeds the ceiling");
  });
});
