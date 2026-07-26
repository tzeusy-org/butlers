import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import type { SessionSummary } from "@/api/types";
import { SessionTable } from "@/components/sessions/SessionTable";

function makeSession(overrides: Partial<SessionSummary>): SessionSummary {
  return {
    id: "sess-abc123",
    butler: "switchboard",
    prompt: "Summarize today's routing failures",
    trigger_source: "telegram",
    request_id: null,
    success: true,
    started_at: "2026-03-12T00:00:00Z",
    completed_at: "2026-03-12T00:00:02Z",
    duration_ms: 2000,
    input_tokens: 100,
    output_tokens: 200,
    cancelled_by_owner: false,
    model: null,
    complexity: null,
    ...overrides,
  };
}

function renderTable(sessions: SessionSummary[], showButlerColumn = false): string {
  return renderToStaticMarkup(
    <SessionTable sessions={sessions} isLoading={false} showButlerColumn={showButlerColumn} />,
  );
}

describe("SessionTable model and complexity columns", () => {
  it("renders Model and Complexity column headers", () => {
    const html = renderTable([makeSession({})]);
    expect(html).toContain("Model");
    expect(html).toContain("Complexity");
  });

  it("shows model alias when model field is populated", () => {
    const html = renderTable([makeSession({ model: "claude-3-5-sonnet" })]);
    expect(html).toContain("claude-3-5-sonnet");
  });

  it("renders em-dash when model is null", () => {
    const html = renderTable([makeSession({ model: null })]);
    // em-dash as unicode entity or character
    expect(html).toMatch(/—|&#x2014;|\u2014/);
  });

  it("renders a ComplexityBadge for known complexity tiers", () => {
    // Current canonical tiers: reasoning/workhorse/cheap/specialty/local/legacy
    const tiers = ["reasoning", "workhorse", "cheap", "specialty", "local", "legacy"] as const;
    for (const tier of tiers) {
      const html = renderTable([makeSession({ complexity: tier })]);
      // Badge text for that tier should appear (label matches capitalized tier name)
      expect(html.toLowerCase()).toContain(tier);
    }
  });

  it("renders em-dash when complexity is null", () => {
    const html = renderTable([makeSession({ complexity: null })]);
    expect(html).toMatch(/—|&#x2014;|\u2014|&mdash;/);
  });

  it("shows complexity badge label for workhorse tier", () => {
    const html = renderTable([makeSession({ complexity: "workhorse" })]);
    expect(html).toContain("Workhorse");
  });

  it("shows complexity badge label for reasoning tier", () => {
    const html = renderTable([makeSession({ complexity: "reasoning" })]);
    expect(html).toContain("Reasoning");
  });

  it("shows complexity badge label for cheap tier", () => {
    const html = renderTable([makeSession({ complexity: "cheap" })]);
    expect(html).toContain("Cheap");
  });

  it("shows complexity badge label for specialty tier", () => {
    const html = renderTable([makeSession({ complexity: "specialty" })]);
    expect(html).toContain("Specialty");
  });
});

describe("SessionTable cancellation status", () => {
  it("renders Cancelled for an owner-cancelled summary without receiving error text", () => {
    const html = renderTable([
      makeSession({ success: false, cancelled_by_owner: true }),
    ]);

    expect(html).toContain("Cancelled");
    expect(html).not.toContain("Failed");
  });

  it("keeps an ordinary unsuccessful summary as Failed", () => {
    const html = renderTable([
      makeSession({ success: false, cancelled_by_owner: false }),
    ]);

    expect(html).toContain("Failed");
    expect(html).not.toContain("Cancelled");
  });

  it("keeps a non-terminal summary Running even when a stale indicator is present", () => {
    const html = renderTable([
      makeSession({ success: null, cancelled_by_owner: true }),
    ]);

    expect(html).toContain("Running");
    expect(html).not.toContain("Cancelled");
  });
});

// ---------------------------------------------------------------------------
// Cost column (bu-ptaub)
// ---------------------------------------------------------------------------

describe("SessionTable cost column", () => {
  it("renders a Cost column header", () => {
    const html = renderTable([makeSession({})]);
    expect(html).toContain("Cost");
  });

  it("formats a nonzero cost_usd via the shared formatCostUsd convention", () => {
    const html = renderTable([makeSession({ cost_usd: 0.018 })]);
    expect(html).toContain("$0.02");
  });

  it("renders em-dash when cost_usd is null", () => {
    const html = renderTable([makeSession({ cost_usd: null })]);
    expect(html).toMatch(/—|&#x2014;|\u2014/);
  });

  it("renders em-dash when cost_usd is absent (older fixture/mock shape)", () => {
    const html = renderTable([makeSession({})]);
    expect(html).toMatch(/—|&#x2014;|\u2014/);
  });

  it("never renders a nonzero cost as the literal '$0.00' (sub-cent floor)", () => {
    const html = renderTable([makeSession({ cost_usd: 0.001 })]);
    // renderToStaticMarkup HTML-escapes "<" in text content.
    expect(html).toContain("&lt;$0.01");
  });
});

describe("SessionTable degraded-source consumption (bu-hmdqz.12)", () => {
  it("names dropped pools above the table when rows are present", () => {
    const html = renderToStaticMarkup(
      <SessionTable
        sessions={[makeSession({})]}
        isLoading={false}
        sourcesDegraded={["atlas"]}
      />,
    );
    expect(html).toContain("sessions-list-degraded");
    expect(html).toContain("atlas");
    // Rows still render — a partial page is shown, just annotated.
    expect(html).toContain('data-testid="session-row"');
  });

  it("gates the calm empty state: a degraded ZERO renders the note, not 'No sessions found'", () => {
    const html = renderToStaticMarkup(
      <SessionTable sessions={[]} isLoading={false} sourcesDegraded={["atlas"]} />,
    );
    expect(html).toContain("sessions-list-degraded");
    expect(html).toContain("atlas");
    expect(html).not.toContain("No sessions found");
  });

  it("still shows the calm empty state when zero AND no source is degraded", () => {
    const html = renderToStaticMarkup(
      <SessionTable sessions={[]} isLoading={false} sourcesDegraded={[]} />,
    );
    expect(html).toContain("No sessions found");
    expect(html).not.toContain("sessions-list-degraded");
  });
});
