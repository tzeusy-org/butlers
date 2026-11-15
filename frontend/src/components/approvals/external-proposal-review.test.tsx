// @vitest-environment jsdom
/**
 * REQ-dashboard-approvals-904: the typed review of an external MCP proposal.
 * Synthetic data only.
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import { afterEach, describe, expect, it } from "vitest";

import type { ExternalProposalReview } from "@/api/types";
import { ExternalProposalReviewPanel } from "./external-proposal-review";

expect.extend(toHaveNoViolations);
afterEach(cleanup);

const URL_TEXT = "https://example.com/synthetic/evidence?ref=1";
const NOTE = "Synthetic note: see https://example.com/elsewhere and www.example.org.";

function review(overrides: Partial<ExternalProposalReview> = {}): ExternalProposalReview {
  return {
    proposal_id: "11111111-1111-4111-8111-111111111111",
    client_label: "synthetic-client",
    subject: {
      id: "22222222-2222-4222-8222-222222222222",
      name: "Synthetic Person A",
      entity_type: "person",
      status: "live",
    },
    predicate: "works-at",
    object: {
      id: "33333333-3333-4333-8333-333333333333",
      name: "Synthetic Org B",
      entity_type: "organization",
      status: "live",
    },
    note: NOTE,
    evidence_url: URL_TEXT,
    authority_statement: "An external AI client proposed this fact; neither you nor your butlers did.",
    unresolvable_entity: false,
    approve_unavailable_reason: null,
    displayed_call: { tool_name: "relationship_assert_fact", args_digest: "a".repeat(64) },
    ...overrides,
  };
}

describe("ExternalProposalReviewPanel", () => {
  // Scenario: The owner sees who and what before approving.
  it("names both entities by name, type and ID, the predicate and the client label", () => {
    render(<ExternalProposalReviewPanel review={review()} pending />);
    const panel = screen.getByRole("region", { name: "External proposal review" });
    const text = panel.textContent ?? "";
    for (const expected of [
      "Synthetic Person A", "person", "22222222-2222-4222-8222-222222222222",
      "Synthetic Org B", "organization", "33333333-3333-4333-8333-333333333333",
      "works-at", "External AI client", "synthetic-client",
    ]) expect(text).toContain(expected);
    expect(screen.getByTestId("external-proposal-authority").textContent).toContain(
      "external AI client",
    );
    // No raw call dump and no standing-rule control.
    expect(text).not.toContain("approval_action_id");
    expect(text).not.toContain("{");
    expect(within(panel).queryByRole("button")).toBeNull();
    expect(within(panel).queryByText(/standing rule|always allow/i)).toBeNull();
  });

  // Scenario: Evidence links are inert.
  it("renders the evidence URL and the note as plain text that cannot navigate or fetch", () => {
    const { container } = render(<ExternalProposalReviewPanel review={review()} pending />);
    expect(screen.getByTestId("external-proposal-evidence-url").textContent).toBe(URL_TEXT);
    expect(screen.getByTestId("external-proposal-note").textContent).toBe(NOTE);
    expect(container.querySelector("a, [href], img, iframe, object, embed, link, [src]")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });

  // Scenario: Unresolvable entity blocks approval but not rejection (the panel half).
  it("states why approval is unavailable for a merged entity", () => {
    const reason =
      "The subject entity 22222222-2222-4222-8222-222222222222 was merged into another entity (merged).";
    render(
      <ExternalProposalReviewPanel
        review={review({
          unresolvable_entity: true,
          displayed_call: null,
          approve_unavailable_reason: reason,
          subject: { ...review().subject!, status: "merged" },
        })}
        pending
      />,
    );
    const stated = screen.getByTestId("external-proposal-unavailable");
    expect(stated.id).toBe("external-proposal-approve-unavailable");
    expect(stated.textContent).toContain(reason);
    expect(screen.getByText("merged into another entity")).toBeTruthy();
  });

  it("states no unavailable reason once the proposal is decided", () => {
    render(
      <ExternalProposalReviewPanel
        review={review({ displayed_call: null, approve_unavailable_reason: "No longer pending." })}
        pending={false}
      />,
    );
    expect(screen.queryByTestId("external-proposal-unavailable")).toBeNull();
  });

  // Scenario: Review is keyboard and screen-reader accessible.
  it("is a named region with labelled fields before the controls and no axe violations", async () => {
    const { container } = render(
      <>
        <ExternalProposalReviewPanel
          review={review({ displayed_call: null, approve_unavailable_reason: "Entity deleted." })}
          pending
        />
        <button type="button" aria-describedby="external-proposal-approve-unavailable" disabled>
          Approve
        </button>
        <button type="button">Deny</button>
      </>,
    );
    const region = screen.getByRole("region", { name: "External proposal review" });
    const deny = screen.getByRole("button", { name: "Deny" });
    expect(region.compareDocumentPosition(deny) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Nothing in the review is a focus trap or a stray tab stop; Tab goes to the controls.
    await userEvent.setup().tab();
    expect(document.activeElement).toBe(deny);
    const approve = screen.getByRole("button", { name: "Approve" });
    expect(approve.getAttribute("aria-describedby")).toBe("external-proposal-approve-unavailable");
    const terms = Array.from(container.querySelectorAll("dt")).map((dt) => dt.textContent);
    expect(terms).toEqual([
      "External AI client", "Subject", "Predicate", "Object", "Note", "Evidence URL (not opened)",
    ]);
    expect(await axe(container)).toHaveNoViolations();
  });
});
