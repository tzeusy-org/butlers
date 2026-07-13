// @vitest-environment jsdom
/**
 * Tests for <RulePromotionBanner> (bu-o62bc, bead 4).
 *
 * Covers:
 * - Pending cards render with Confirm/Dismiss; the callbacks fire with the id.
 * - Auto-applied items render informationally (no Confirm button) with a
 *   reversible Disable/Re-enable affordance that fires onSetEnabled with the
 *   toggled state.
 * - Returns null when there is nothing pending AND nothing auto-applied.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import type { RulePromotionAutoApplied, RulePromotionSuggestion } from "@/api/types";
import { RulePromotionBanner } from "@/components/approvals/rule-promotion-banner.tsx";

afterEach(cleanup);

const PENDING: RulePromotionSuggestion = {
  id: "s1",
  sender_key: "invoices@acme.com",
  source_channel: "gmail",
  proposed_rule_type: "sender_address",
  proposed_condition: { address: "invoices@acme.com" },
  proposed_action: "route_to:finance",
  evidence_count: 4,
  is_clearly_automated: false,
  first_evidence_at: "2026-07-01T00:00:00Z",
  last_evidence_at: "2026-07-05T00:00:00Z",
  created_at: "2026-07-05T00:00:00Z",
};

const AUTO: RulePromotionAutoApplied = {
  id: "a1",
  sender_key: "noreply@acme.com",
  source_channel: "gmail",
  proposed_action: "metadata_only",
  evidence_count: 6,
  created_rule_id: "r1",
  rule_enabled: true,
  decided_at: "2026-07-06T00:00:00Z",
  decided_by: "auto:promotion",
};

const noop = () => {};

describe("RulePromotionBanner", () => {
  it("renders nothing when empty", () => {
    const { container } = render(
      <RulePromotionBanner
        pending={[]}
        autoApplied={[]}
        onConfirm={noop}
        onDismiss={noop}
        onSetEnabled={noop}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders a pending card and fires confirm/dismiss with the id", () => {
    const onConfirm = vi.fn();
    const onDismiss = vi.fn();
    render(
      <RulePromotionBanner
        pending={[PENDING]}
        autoApplied={[]}
        onConfirm={onConfirm}
        onDismiss={onDismiss}
        onSetEnabled={noop}
      />,
    );
    expect(screen.getByTestId("rule-promotion-banner")).toBeTruthy();
    expect(screen.getByText(/route_to:finance/)).toBeTruthy();

    fireEvent.click(screen.getByText("Confirm rule"));
    expect(onConfirm).toHaveBeenCalledWith("s1");
    fireEvent.click(screen.getByText("Dismiss"));
    expect(onDismiss).toHaveBeenCalledWith("s1");
  });

  it("renders an auto-applied item with a reversible disable (no confirm button)", () => {
    const onSetEnabled = vi.fn();
    render(
      <RulePromotionBanner
        pending={[]}
        autoApplied={[AUTO]}
        onConfirm={noop}
        onDismiss={noop}
        onSetEnabled={onSetEnabled}
      />,
    );
    expect(screen.getByText("Auto-applied rule")).toBeTruthy();
    // Auto-applied is informational: no confirm affordance.
    expect(screen.queryByText("Confirm rule")).toBeNull();

    // enabled=true -> the toggle disables (flips to false).
    fireEvent.click(screen.getByText("Disable rule"));
    expect(onSetEnabled).toHaveBeenCalledWith("a1", false);
  });

  it("shows Re-enable for an already-disabled auto-applied rule", () => {
    const onSetEnabled = vi.fn();
    render(
      <RulePromotionBanner
        pending={[]}
        autoApplied={[{ ...AUTO, rule_enabled: false }]}
        onConfirm={noop}
        onDismiss={noop}
        onSetEnabled={onSetEnabled}
      />,
    );
    fireEvent.click(screen.getByText("Re-enable rule"));
    expect(onSetEnabled).toHaveBeenCalledWith("a1", true);
  });
});
