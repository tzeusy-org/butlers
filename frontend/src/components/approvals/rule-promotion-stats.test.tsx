// @vitest-environment jsdom
/**
 * Tests for <RulePromotionStatsTile> (bu-hb61f, bead 6).
 *
 * Covers:
 * - The three stat blocks render their numbers (sessions avoided labelled est.).
 * - A degraded source renders its SourceDegradedNote instead of a fabricated
 *   zero, and the other blocks still render.
 * - The drift ("Drifting rules") cell renders regardless of value.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import type { RulePromotionStats } from "@/api/types";
import { RulePromotionStatsTile } from "@/components/approvals/rule-promotion-stats.tsx";

afterEach(cleanup);

const STATS: RulePromotionStats = {
  suggestions_pending: 3,
  suggestions_confirmed: 7,
  suggestions_dismissed: 2,
  suggestions_superseded: 4,
  promoted_rules_active: 5,
  promoted_rule_matches: 128,
  llm_sessions_avoided_estimate: 128,
  demotion_pending: 1,
  promoted_rule_spot_checks: 40,
};

describe("RulePromotionStatsTile", () => {
  it("renders all stat blocks with their numbers", () => {
    render(<RulePromotionStatsTile stats={STATS} />);
    expect(screen.getByTestId("rule-promotion-stats")).toBeTruthy();
    // Savings block: sessions-avoided equals matches by design, so "128"
    // renders in both cells.
    expect(screen.getByText("Sessions avoided (est.)")).toBeTruthy();
    expect(screen.getAllByText("128")).toHaveLength(2);
    expect(screen.getByText("Spot-checks")).toBeTruthy();
    expect(screen.getByText("40")).toBeTruthy();
    // Promoted rules + suggestion lifecycle
    expect(screen.getByText("Promoted rules")).toBeTruthy();
    expect(screen.getByText("Pending")).toBeTruthy();
    expect(screen.getByText("Superseded")).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
    expect(screen.getByText("Drifting rules")).toBeTruthy();
    // The estimate caveat is present (honest labelling).
    expect(screen.getByText(/Sessions avoided is an estimate/)).toBeTruthy();
  });

  it("flags a degraded verdict block instead of showing a zero", () => {
    render(
      <RulePromotionStatsTile
        stats={{ ...STATS, promoted_rule_matches: 0, llm_sessions_avoided_estimate: 0 }}
        degraded={["verdict_metrics"]}
      />,
    );
    // The savings block is replaced by a degraded note (no fabricated 0 savings).
    expect(screen.getByTestId("rule-promotion-stats-verdict-degraded")).toBeTruthy();
    expect(screen.queryByText("Sessions avoided (est.)")).toBeNull();
    // Other blocks still render.
    expect(screen.getByText("Promoted rules")).toBeTruthy();
    expect(screen.getByText("Pending")).toBeTruthy();
  });

  it("flags a degraded suggestion-counts block independently", () => {
    render(<RulePromotionStatsTile stats={STATS} degraded={["suggestion_counts"]} />);
    expect(screen.getByTestId("rule-promotion-stats-suggestions-degraded")).toBeTruthy();
    expect(screen.queryByText("Pending")).toBeNull();
    // Savings + promoted-rules blocks unaffected.
    expect(screen.getByText("Sessions avoided (est.)")).toBeTruthy();
    expect(screen.getByText("Promoted rules")).toBeTruthy();
  });
});
