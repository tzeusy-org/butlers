// @vitest-environment jsdom
/**
 * Tests for <DecisionsVerdictOpener> (bu-ckkpz.2).
 *
 * Verifies the /decisions opener composes the open-decisions digest into
 * "N decisions waiting, oldest Xd" (matching the backend's own
 * _compose_weekly_digest_message vocabulary), names escalated decisions, and
 * honors the degraded-envelope + isError-suppression contracts.
 */

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { DecisionsVerdictOpener } from "@/components/decisions/decisions-verdict-opener.tsx";
import type { DecisionBeadSummary } from "@/api/index.ts";

function render(ui: React.ReactElement): string {
  return renderToStaticMarkup(<MemoryRouter>{ui}</MemoryRouter>);
}

function decision(overrides: Partial<DecisionBeadSummary>): DecisionBeadSummary {
  return {
    id: "bu-a1",
    title: "DECISION REQUIRED (owner): pick one",
    priority: 1,
    created_at: "2026-07-01T00:00:00Z",
    age_hours: 240,
    escalated: false,
    ...overrides,
  };
}

describe("DecisionsVerdictOpener -- all clear", () => {
  it("renders the calm line when nothing is waiting", () => {
    const html = render(
      <DecisionsVerdictOpener
        decisions={[]}
        isLoading={false}
        isError={false}
        decisionsAvailable={true}
      />,
    );
    expect(html).toContain('data-testid="decisions-verdict-all-clear"');
    expect(html).toContain("No decisions waiting.");
  });
});

describe("DecisionsVerdictOpener -- clauses", () => {
  it("composes count + oldest age exactly matching the backend digest vocabulary", () => {
    const decisions = [
      decision({ id: "bu-older", age_hours: 240 }), // 10d, oldest-first
      decision({ id: "bu-newer", age_hours: 48 }),
    ];
    const html = render(
      <DecisionsVerdictOpener
        decisions={decisions}
        isLoading={false}
        isError={false}
        decisionsAvailable={true}
      />,
    );
    expect(html).toContain('data-testid="decisions-verdict-clauses"');
    expect(html).toContain("2 decisions waiting, oldest 10d");
  });

  it("singularizes for exactly one decision", () => {
    const html = render(
      <DecisionsVerdictOpener
        decisions={[decision({ age_hours: 5 })]}
        isLoading={false}
        isError={false}
        decisionsAvailable={true}
      />,
    );
    expect(html).toContain("1 decision waiting, oldest 5h");
  });

  it("names escalated decisions as a second clause", () => {
    const decisions = [
      decision({ id: "bu-a", age_hours: 240, escalated: true }),
      decision({ id: "bu-b", age_hours: 48, escalated: false }),
    ];
    const html = render(
      <DecisionsVerdictOpener
        decisions={decisions}
        isLoading={false}
        isError={false}
        decisionsAvailable={true}
      />,
    );
    expect(html).toContain("1 blocking a P1 bug or deploy");
  });

  it("pluralizes the escalated clause when more than one has escalated", () => {
    const decisions = [
      decision({ id: "bu-a", age_hours: 240, escalated: true }),
      decision({ id: "bu-b", age_hours: 48, escalated: true }),
    ];
    const html = render(
      <DecisionsVerdictOpener
        decisions={decisions}
        isLoading={false}
        isError={false}
        decisionsAvailable={true}
      />,
    );
    expect(html).toContain("2 blocking a P1 bug or deploy");
  });
});

describe("DecisionsVerdictOpener -- degraded-envelope honesty", () => {
  it("names the unavailable digest and suppresses the all-clear when decisionsAvailable is false", () => {
    // decisionsAvailable=false + empty decisions must NOT read as "No
    // decisions waiting." -- the beads export could not be read at all.
    const html = render(
      <DecisionsVerdictOpener
        decisions={[]}
        isLoading={false}
        isError={false}
        decisionsAvailable={false}
      />,
    );
    expect(html).toContain('data-testid="decisions-verdict-clauses"');
    expect(html).toContain("decision digest unavailable");
    expect(html).not.toContain("No decisions waiting.");
    expect(html).not.toContain('data-testid="decisions-verdict-all-clear"');
  });

  it("keeps the honest all-clear when decisionsAvailable is true and the list is genuinely empty", () => {
    const html = render(
      <DecisionsVerdictOpener
        decisions={[]}
        isLoading={false}
        isError={false}
        decisionsAvailable={true}
      />,
    );
    expect(html).toContain('data-testid="decisions-verdict-all-clear"');
    expect(html).not.toContain("unavailable");
  });
});

describe("DecisionsVerdictOpener -- isError-suppression contract", () => {
  it("renders the skeleton while loading", () => {
    const html = render(
      <DecisionsVerdictOpener
        decisions={[]}
        isLoading
        isError={false}
        decisionsAvailable={undefined}
      />,
    );
    expect(html).toContain('data-testid="decisions-verdict-skeleton"');
  });

  it("names an errored source and never renders the all-clear alongside it", () => {
    const html = render(
      <DecisionsVerdictOpener
        decisions={[]}
        isLoading={false}
        isError
        decisionsAvailable={undefined}
      />,
    );
    expect(html).toContain('data-testid="decisions-verdict-clauses"');
    expect(html).toContain("decision digest unavailable");
    expect(html).not.toContain("No decisions waiting.");
  });
});
