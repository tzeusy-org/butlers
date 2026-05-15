// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { QaInvestigationNotes } from "@/api/types";
import { ClaimAnchoredBlurb, EvidenceLog, getClaimOrderFromSegments } from "@/components/qa";

afterEach(() => cleanup());

const segments: QaInvestigationNotes["blurb_segments"] = [
  "The patrol shows ",
  { claim: "c1", text: "the scheduler guard fired before hydration" },
  ", while ",
  { claim: "c2", text: "the database stayed reachable" },
  ".",
];

const claims: QaInvestigationNotes["claims"] = {
  c2: {
    evidence_ids: ["e2", "e3"],
    note: "DB health stayed green during the failure.",
  },
  c1: {
    evidence_ids: ["e1", "e3"],
    note: "Guard logs line up with the failed attempt.",
  },
};

const evidence: QaInvestigationNotes["evidence_lines"] = [
  {
    id: "e1",
    ts: "07:58:00",
    lvl: "ERROR",
    butler: "qa",
    msg: "scheduler guard rejected launch",
  },
  {
    id: "e2",
    ts: "07:58:03",
    lvl: "INFO",
    butler: "switchboard",
    msg: "database health check passed",
  },
  {
    id: "e3",
    ts: "07:58:08",
    lvl: "WARN",
    butler: "qa",
    msg: "hydration lag detected",
  },
];

function DiagnosisHarness() {
  const [hoveredClaim, setHoveredClaim] = useState<string[] | null>(null);
  const claimOrder = getClaimOrderFromSegments(segments);

  return (
    <div>
      <ClaimAnchoredBlurb
        segments={segments}
        claims={claims}
        claimOrder={claimOrder}
        hoveredClaim={hoveredClaim}
        onClaimHover={setHoveredClaim}
      />
      <EvidenceLog
        evidence={evidence}
        claims={claims}
        claimOrder={claimOrder}
        hoveredClaim={hoveredClaim}
        onRowHover={setHoveredClaim}
      />
    </div>
  );
}

describe("QA diagnosis components", () => {
  it("test_blurb_hover_highlights_matching_evidence", () => {
    render(<DiagnosisHarness />);

    fireEvent.mouseEnter(screen.getByTestId("qa-claim-c1"));

    expect(screen.getByTestId("qa-evidence-row-e1").className).toContain(
      "bg-[oklch(0.81_0.185_84_/_0.10)]",
    );
    expect(screen.getByTestId("qa-evidence-row-e3").className).toContain(
      "bg-[oklch(0.81_0.185_84_/_0.10)]",
    );
    expect(screen.getByTestId("qa-evidence-row-e2").className).not.toContain(
      "bg-[oklch(0.81_0.185_84_/_0.10)]",
    );
  });

  it("test_evidence_hover_highlights_matching_claim", () => {
    render(<DiagnosisHarness />);

    fireEvent.mouseEnter(screen.getByTestId("qa-evidence-row-e2"));

    expect(screen.getByTestId("qa-claim-c2").className).toContain(
      "bg-[oklch(0.81_0.185_84_/_0.15)]",
    );
    expect(screen.getByTestId("qa-claim-c2-marker").className).toContain("text-amber-500");
    expect(screen.getByTestId("qa-claim-c1").className).not.toContain(
      "bg-[oklch(0.81_0.185_84_/_0.15)]",
    );
  });

  it("test_evidence_hover_highlights_all_linked_claims", () => {
    // e3 is linked to both c1 and c2; hovering it must highlight both claim segments
    render(<DiagnosisHarness />);

    fireEvent.mouseEnter(screen.getByTestId("qa-evidence-row-e3"));

    expect(screen.getByTestId("qa-claim-c1").className).toContain(
      "bg-[oklch(0.81_0.185_84_/_0.15)]",
    );
    expect(screen.getByTestId("qa-claim-c2").className).toContain(
      "bg-[oklch(0.81_0.185_84_/_0.15)]",
    );
  });

  it("test_no_claims_no_hover_propagation", () => {
    const onRowHover = vi.fn();
    render(
      <EvidenceLog
        evidence={[
          {
            id: "orphan",
            ts: "08:00:00",
            lvl: "INFO",
            butler: "qa",
            msg: "unlinked evidence line",
          },
        ]}
        claims={claims}
        hoveredClaim={null}
        onRowHover={onRowHover}
      />,
    );

    fireEvent.mouseEnter(screen.getByTestId("qa-evidence-row-orphan"));

    expect(onRowHover).not.toHaveBeenCalled();
  });

  it("test_claim_numbers_are_stable_across_blurb_and_evidence", () => {
    render(<DiagnosisHarness />);

    expect(screen.getByTestId("qa-claim-c1-marker").textContent).toBe("[1]");
    expect(screen.getByTestId("qa-claim-c2-marker").textContent).toBe("[2]");
    expect(screen.getByTestId("qa-evidence-row-e1-claims").textContent).toBe("[1]");
    expect(screen.getByTestId("qa-evidence-row-e2-claims").textContent).toBe("[2]");
    expect(screen.getByTestId("qa-evidence-row-e3-claims").textContent).toBe("[1,2]");
  });

  it("test_evidence_level_colors_stay_semantic_when_highlighted", () => {
    render(<DiagnosisHarness />);

    fireEvent.mouseEnter(screen.getByTestId("qa-claim-c1"));

    expect(screen.getByText("ERROR").className).toContain("text-destructive");
    expect(screen.getByText("WARN").className).toContain("text-amber-500");
    expect(screen.getByText("INFO").className).toContain("text-muted-foreground");
  });
});
