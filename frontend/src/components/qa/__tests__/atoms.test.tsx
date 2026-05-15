// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  QaActiveBreakdown,
  QaActiveDismissal,
  QaCaseSummary,
  QaKpiBlock,
} from "@/api/types";
import { CaseDossierHeader, CaseList, QaKpiStrip, StateTrack } from "@/components/qa";

const qaHookMocks = vi.hoisted(() => ({
  removeDismissalMutate: vi.fn(),
}));

vi.mock("@/hooks/use-qa", () => ({
  useRemoveDismissal: () => ({
    mutate: qaHookMocks.removeDismissalMutate,
    isPending: false,
  }),
}));

afterEach(() => {
  cleanup();
});

const kpisWithNullMttr: QaKpiBlock = {
  prs_landed_24h: 3,
  mttr_24h_seconds: null,
  self_resolved_7d_pct: 71,
  active_cases_now: 5,
};

const activeBreakdown: QaActiveBreakdown = {
  awaiting_ci: 2,
  escalated_open_cases: 1,
};

const activeDismissal: QaActiveDismissal = {
  fingerprint: "f".repeat(64),
  expires_at: "2026-05-15T10:30:00Z",
  reason: null,
};

const cases: QaCaseSummary[] = [
  {
    id: "case-1",
    short_id: "mfg",
    sev: "high",
    butler: "qa",
    headline: "Runtime args dropped before adapter launch",
    detected: "2026-05-14T09:12:00Z",
    age_seconds: 5400,
    state: "pr",
    pr_state: "open",
    pr_url: "https://github.com/Tzeusy/butlers/pull/1",
  },
  {
    id: "case-2",
    short_id: "q2",
    sev: "medium",
    butler: "health",
    headline: "Measurement sync stalled",
    detected: "2026-05-14T10:00:00Z",
    age_seconds: 120,
    state: "diagnose",
    pr_state: null,
    pr_url: null,
  },
];

describe("QA dossier atoms", () => {
  beforeEach(() => {
    qaHookMocks.removeDismissalMutate.mockReset();
  });

  it("renders null MTTR as an em dash with the terminal-case sublabel", () => {
    render(<QaKpiStrip kpis={kpisWithNullMttr} active={activeBreakdown} />);

    expect(screen.getByText("mttr · 24h")).toBeTruthy();
    expect(screen.getByTestId("qa-kpi-mttr-value").textContent).toBe("—");
    expect(screen.getByText("no terminal cases in 24h")).toBeTruthy();
  });

  it("renders active breakdown with awaiting CI and escalated open cases", () => {
    render(<QaKpiStrip kpis={kpisWithNullMttr} active={activeBreakdown} />);

    const activeValue = screen.getByTestId("qa-kpi-active-cases-value");
    expect(activeValue.nextElementSibling?.textContent).toBe("2 awaiting CI · 1 escalated");
  });

  it("marks the active CaseList row and still emits onSelect for row clicks", () => {
    const onSelect = vi.fn();
    render(<CaseList cases={cases} selectedId="case-1" onSelect={onSelect} />);

    const activeRow = screen.getByTestId("qa-case-row-case-1");
    expect(activeRow.className).toContain("border-l-2");
    expect(activeRow.className).toContain("border-foreground");
    expect(activeRow.className).toContain("bg-white/[0.04]");

    fireEvent.click(screen.getByTestId("qa-case-row-case-2"));
    expect(onSelect).toHaveBeenCalledWith("case-2");
  });

  it("renders the escalated StateTrack variant with amber pr and landed stages", () => {
    render(<StateTrack stage="escalated" />);

    expect(screen.getByTestId("qa-state-track-escalated-label").textContent).toBe("· escalated");
    expect(screen.getByTestId("qa-state-track-pr").className).toContain("text-amber-500");
    expect(screen.getByTestId("qa-state-track-landed").className).toContain("text-amber-500");
  });

  it("renders the active dismissal caption when present", () => {
    render(<CaseDossierHeader case={cases[0]} stage="pr" dismissal={activeDismissal} />);

    expect(screen.getByText(/dismissed until/i)).toBeTruthy();
    expect(screen.getByLabelText("Remove dismissal")).toBeTruthy();
  });

  it("invokes the remove dismissal mutation from the header pill", () => {
    render(<CaseDossierHeader case={cases[0]} stage="pr" dismissal={activeDismissal} />);

    fireEvent.click(screen.getByLabelText("Remove dismissal"));

    expect(qaHookMocks.removeDismissalMutate).toHaveBeenCalledWith(activeDismissal.fingerprint);
  });
});
