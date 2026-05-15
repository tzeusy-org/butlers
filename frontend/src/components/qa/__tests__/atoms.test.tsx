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
  dismissIssueMutate: vi.fn(),
  retryAttemptMutate: vi.fn(),
}));

vi.mock("@/hooks/use-qa", () => ({
  useRemoveDismissal: () => ({
    mutate: qaHookMocks.removeDismissalMutate,
    isPending: false,
  }),
  useDismissQaIssue: () => ({
    mutate: qaHookMocks.dismissIssueMutate,
    isPending: false,
  }),
  useRetryHealingAttempt: () => ({
    mutate: qaHookMocks.retryAttemptMutate,
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
  prs_landed_prior_24h: 0,
  mttr_prior_24h_seconds: null,
  self_resolved_prior_7d_pct: null,
};

const kpisWithDeltas: QaKpiBlock = {
  prs_landed_24h: 5,
  mttr_24h_seconds: 720,
  self_resolved_7d_pct: 80,
  active_cases_now: 2,
  prs_landed_prior_24h: 3,
  mttr_prior_24h_seconds: 840,
  self_resolved_prior_7d_pct: 76,
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
    short_id: "#401",
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
    short_id: "#202",
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
    qaHookMocks.dismissIssueMutate.mockReset();
    qaHookMocks.retryAttemptMutate.mockReset();
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
    render(
      <CaseDossierHeader
        case={cases[0]}
        stage="pr"
        fingerprint={activeDismissal.fingerprint}
        dismissal={activeDismissal}
      />,
    );

    expect(screen.getByText(/dismissed until/i)).toBeTruthy();
    expect(screen.getByLabelText("Remove dismissal")).toBeTruthy();
  });

  it("invokes the remove dismissal mutation from the header pill", () => {
    render(
      <CaseDossierHeader
        case={cases[0]}
        stage="pr"
        fingerprint={activeDismissal.fingerprint}
        dismissal={activeDismissal}
      />,
    );

    fireEvent.click(screen.getByLabelText("Remove dismissal"));

    expect(qaHookMocks.removeDismissalMutate).toHaveBeenCalledWith(activeDismissal.fingerprint);
  });

  it("renders delta sub-labels when prior-period values are available", () => {
    render(<QaKpiStrip kpis={kpisWithDeltas} active={activeBreakdown} />);

    // prs landed: +2 vs prior 24h (5 - 3 = +2)
    expect(screen.getByText("+2 vs prior 24h")).toBeTruthy();
    // mttr: 720s = 12m, 840s = 14m, delta = -120s = -2m → "−2m vs prior 24h"
    expect(screen.getByText("−2m vs prior 24h")).toBeTruthy();
    // self-resolved: 80 - 76 = +4pp vs prior week
    expect(screen.getByText("+4pp vs prior week")).toBeTruthy();
  });

  it("renders negative count delta with minus sign", () => {
    const kpisNegativeDelta: QaKpiBlock = {
      ...kpisWithDeltas,
      prs_landed_24h: 1,
      prs_landed_prior_24h: 4,
    };
    render(<QaKpiStrip kpis={kpisNegativeDelta} active={activeBreakdown} />);

    expect(screen.getByText("−3 vs prior 24h")).toBeTruthy();
  });

  it("renders zero count delta as +0", () => {
    const kpisZeroDelta: QaKpiBlock = {
      ...kpisWithDeltas,
      prs_landed_24h: 3,
      prs_landed_prior_24h: 3,
    };
    render(<QaKpiStrip kpis={kpisZeroDelta} active={activeBreakdown} />);

    expect(screen.getByText("+0 vs prior 24h")).toBeTruthy();
  });

  it("falls back to static sub-labels when prior-period values are null", () => {
    // When prior-period MTTR and self-resolved are null (no prior sample), static fallbacks are used.
    // prs_landed_prior_24h is 0, which is a real value, so "+0 vs prior 24h" is shown.
    render(<QaKpiStrip kpis={kpisWithNullMttr} active={activeBreakdown} />);

    // prs_landed_24h=3, prs_landed_prior_24h=0 → delta shown ("+3 vs prior 24h"), not fallback
    expect(screen.getByText("+3 vs prior 24h")).toBeTruthy();

    // MTTR is null → "no terminal cases in 24h" sub-label (not a delta)
    expect(screen.getByText("no terminal cases in 24h")).toBeTruthy();
    // self_resolved_prior_7d_pct is null → fallback to "7d window"
    expect(screen.getByText("7d window")).toBeTruthy();
  });

  it("shows no MTTR delta when current MTTR is null even with prior", () => {
    const kpisNullCurrentMttr: QaKpiBlock = {
      ...kpisWithDeltas,
      mttr_24h_seconds: null,
      mttr_prior_24h_seconds: 600,
    };
    render(<QaKpiStrip kpis={kpisNullCurrentMttr} active={activeBreakdown} />);

    // Current MTTR null → cannot show delta; shows null-mttr sub-label
    expect(screen.getByText("no terminal cases in 24h")).toBeTruthy();
  });

  it("renders Dismiss pill when fingerprint present and no active dismissal in non-terminal state", () => {
    render(
      <CaseDossierHeader
        case={cases[1]}
        stage="diagnose"
        fingerprint="abc123fingerprint"
        dismissal={null}
      />,
    );

    expect(screen.getByLabelText("Dismiss case")).toBeTruthy();
    expect(screen.queryByLabelText("Retry investigation")).toBeNull();
  });

  it("invokes the dismiss mutation with fingerprint when Dismiss pill is clicked", () => {
    const fp = "abc123fingerprint";
    render(
      <CaseDossierHeader
        case={cases[1]}
        stage="diagnose"
        fingerprint={fp}
        dismissal={null}
      />,
    );

    fireEvent.click(screen.getByLabelText("Dismiss case"));

    expect(qaHookMocks.dismissIssueMutate).toHaveBeenCalledWith(
      { fingerprint: fp },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("renders Retry pill for terminal stage (landed) and hides Dismiss pill", () => {
    render(
      <CaseDossierHeader
        case={{ ...cases[0], state: "landed" }}
        stage="landed"
        fingerprint="fp-landed"
        dismissal={null}
      />,
    );

    expect(screen.getByLabelText("Retry investigation")).toBeTruthy();
    expect(screen.queryByLabelText("Dismiss case")).toBeNull();
  });

  it("invokes retry mutation with case id when Retry pill is clicked", () => {
    render(
      <CaseDossierHeader
        case={{ ...cases[0], state: "escalated" }}
        stage="escalated"
        fingerprint={null}
        dismissal={null}
      />,
    );

    fireEvent.click(screen.getByLabelText("Retry investigation"));

    expect(qaHookMocks.retryAttemptMutate).toHaveBeenCalledWith(
      cases[0].id,
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });
});
