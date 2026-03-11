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
    const tiers = ["trivial", "medium", "high", "extra_high"] as const;
    for (const tier of tiers) {
      const html = renderTable([makeSession({ complexity: tier })]);
      // Badge text for that tier should appear
      expect(html.toLowerCase()).toContain(tier.replace("_", " ").replace("extra high", "extra high"));
    }
  });

  it("renders em-dash when complexity is null", () => {
    const html = renderTable([makeSession({ complexity: null })]);
    expect(html).toMatch(/—|&#x2014;|\u2014|&mdash;/);
  });

  it("shows complexity badge label for medium tier", () => {
    const html = renderTable([makeSession({ complexity: "medium" })]);
    expect(html).toContain("Medium");
  });

  it("shows complexity badge label for high tier", () => {
    const html = renderTable([makeSession({ complexity: "high" })]);
    expect(html).toContain("High");
  });

  it("shows complexity badge label for extra_high tier", () => {
    const html = renderTable([makeSession({ complexity: "extra_high" })]);
    expect(html).toContain("Extra High");
  });

  it("shows complexity badge label for trivial tier", () => {
    const html = renderTable([makeSession({ complexity: "trivial" })]);
    expect(html).toContain("Trivial");
  });
});
