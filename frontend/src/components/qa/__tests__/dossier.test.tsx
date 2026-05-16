// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  PaginatedResponse,
  QaCaseDossier,
  QaInvestigationNotes,
  QaJournalEvent,
} from "@/api/types";
import { CaseDossier, PatrolJournal } from "@/components/qa";
import { useQaCase, useQaCaseJournal } from "@/hooks/use-qa";

const qaHookMocks = vi.hoisted(() => ({
  removeDismissalMutate: vi.fn(),
  useQaCase: vi.fn(),
  useQaCaseJournal: vi.fn(),
}));

vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => <time dateTime={value}>{value}</time>,
}));

// Pin the "detected at" formatter so the dossier snapshot is stable across
// timezones. The real helper renders local-time strings; tests for the helper
// itself live in __tests__/utils.test.ts.
vi.mock("@/components/qa/utils", async () => {
  const actual = await vi.importActual<typeof import("../utils")>("../utils");
  return {
    ...actual,
    formatQaDetectedTime: (ts: string) => `formatted(${ts})`,
  };
});

vi.mock("@/hooks/use-qa", () => ({
  useQaCase: qaHookMocks.useQaCase,
  useQaCaseJournal: qaHookMocks.useQaCaseJournal,
  useRemoveDismissal: () => ({
    mutate: qaHookMocks.removeDismissalMutate,
    isPending: false,
  }),
  useDismissQaIssue: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useRetryHealingAttempt: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
}));

afterEach(() => {
  cleanup();
});

const notes: QaInvestigationNotes = {
  schema_version: 1,
  headline: "Runtime ignored catalog timeout",
  hypothesis: "Spawner forwarded the outer timeout but not the adapter timeout.",
  blurb_segments: [
    "The investigation found ",
    { claim: "c-timeout", text: "the adapter launched without the catalog timeout" },
    ", while ",
    { claim: "c-run", text: "the run still completed enough to leave logs" },
    ".",
  ],
  claims: {
    "c-timeout": {
      evidence_ids: ["e-timeout"],
      note: "Adapter invocation lacked the timeout argument.",
    },
    "c-run": {
      evidence_ids: ["e-run"],
      note: "Session logs show the runtime started.",
    },
  },
  evidence_lines: [
    {
      id: "e-timeout",
      ts: "08:14:01",
      lvl: "ERROR",
      butler: "qa",
      msg: "runtime.invoke(prompt) called without timeout",
    },
    {
      id: "e-run",
      ts: "08:14:18",
      lvl: "INFO",
      butler: "switchboard",
      msg: "session record persisted",
    },
  ],
  counter_evidence: [
    {
      hypothesis: "Database unavailable",
      verdict: "rejected",
      reason: "health checks stayed green",
    },
  ],
  why_this_fix: "The patch forwards the catalog timeout into the adapter call.",
  diff_snapshot: [
    { kind: "meta", text: "src/butlers/core/spawner.py" },
    { kind: "-", text: "runtime.invoke(prompt)" },
    { kind: "+", text: "runtime.invoke(prompt, timeout=session_timeout_s)" },
  ],
};

const journal: QaJournalEvent[] = [
  {
    id: "j-flagged",
    ts: "2026-05-15T00:10:00Z",
    step: "flagged",
    text: "Finding flagged by patrol",
    detail: "fingerprint bu-timeout",
    data: {},
  },
  {
    id: "j-concluded",
    ts: "2026-05-15T00:16:00Z",
    step: "concluded",
    text: "Investigation selected timeout propagation as root cause",
    detail: null,
    data: {},
  },
];

const fullCase: QaCaseDossier = {
  case: {
    id: "case-1",
    short_id: "#123",
    sev: "high",
    butler: "qa",
    headline: "Runtime ignored catalog timeout",
    detected: "2026-05-15T00:10:00Z",
    age_seconds: 420,
    state: "pr",
    pr_state: "open",
    pr_url: "https://github.com/Tzeusy/butlers/pull/1677",
  },
  state_track_stage: "pr",
  fingerprint: "deadbeef" + "0".repeat(56),
  dismissal: null,
  investigation_notes: notes,
  pr: {
    number: 1677,
    state: "open",
    title: "Forward catalog timeouts",
    branch: "agent/bu-9lo5u",
    ci_status: "pending",
    additions: 18,
    deletions: 4,
    opened_at: "2026-05-15T00:20:00Z",
    merged_at: null,
    url: "https://github.com/Tzeusy/butlers/pull/1677",
  },
  journal,
};

function caseResponse(dossier: QaCaseDossier) {
  return {
    data: { data: dossier, meta: {} },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useQaCase>;
}

function journalResponse(events: QaJournalEvent[]) {
  return {
    data: {
      data: events,
      meta: { total: events.length, offset: 0, limit: 50, has_more: false },
    } satisfies PaginatedResponse<QaJournalEvent>,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useQaCaseJournal>;
}

describe("QA case dossier composition", () => {
  beforeEach(() => {
    qaHookMocks.removeDismissalMutate.mockReset();
    qaHookMocks.useQaCase.mockReturnValue(caseResponse(fullCase));
    qaHookMocks.useQaCaseJournal.mockReturnValue(journalResponse(journal));
  });

  it("test_dossier_renders_full_case", () => {
    const { container } = render(<CaseDossier caseId="case-1" />);

    expect(vi.mocked(useQaCase)).toHaveBeenCalledWith("case-1");
    expect(vi.mocked(useQaCaseJournal)).toHaveBeenCalledWith("case-1", { limit: 50 });
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe(
      "Runtime ignored catalog timeout",
    );
    expect(screen.getByText("Diagnosis")).toBeTruthy();
    expect(screen.getByText("Evidence · log fragments")).toBeTruthy();
    expect(screen.getByText("Proposed fix")).toBeTruthy();
    expect(screen.getByText("Patrol journal · every QA decision on this case")).toBeTruthy();
    expect(screen.getByText("2 entries · patrol every 10m")).toBeTruthy();
    expect(container.querySelector("[data-testid='qa-case-dossier']")).toMatchSnapshot();
  });

  it("test_dossier_renders_in_flight_case", () => {
    qaHookMocks.useQaCase.mockReturnValue(
      caseResponse({
        ...fullCase,
        state_track_stage: "diagnose",
        investigation_notes: null,
        pr: null,
        journal: [],
      }),
    );
    qaHookMocks.useQaCaseJournal.mockReturnValue(journalResponse([]));

    render(<CaseDossier caseId="case-1" />);

    expect(screen.getByText("Diagnosing…")).toBeTruthy();
    expect(screen.queryByText("Evidence · log fragments")).toBeNull();
    expect(screen.queryByText("Considered & ruled out")).toBeNull();
    expect(screen.getByText("No PR. Escalated to user.")).toBeTruthy();
    expect(screen.queryByText("Patrol journal · every QA decision on this case")).toBeNull();
  });

  it.each([
    ["pr"],
    ["landed"],
    ["escalated"],
  ] as const)(
    "renders no-notes-captured copy when stage is %s and notes are missing",
    (stage) => {
      qaHookMocks.useQaCase.mockReturnValue(
        caseResponse({
          ...fullCase,
          state_track_stage: stage,
          investigation_notes: null,
          journal: [],
        }),
      );
      qaHookMocks.useQaCaseJournal.mockReturnValue(journalResponse([]));

      render(<CaseDossier caseId="case-1" />);

      expect(
        screen.getByText("No investigation notes were captured for this case."),
      ).toBeTruthy();
      expect(screen.queryByText("Diagnosing…")).toBeNull();
      expect(
        screen.queryByText("Investigation notes have not been emitted yet."),
      ).toBeNull();
    },
  );

  it("test_dossier_lifts_hover_state", () => {
    render(<CaseDossier caseId="case-1" />);

    fireEvent.mouseEnter(screen.getByTestId("qa-claim-c-timeout"));
    expect(screen.getByTestId("qa-evidence-row-e-timeout").className).toContain(
      "bg-severity-medium/10",
    );

    fireEvent.mouseLeave(screen.getByTestId("qa-claim-c-timeout"));
    fireEvent.mouseEnter(screen.getByTestId("qa-evidence-row-e-run"));
    expect(screen.getByTestId("qa-claim-c-run").className).toContain(
      "bg-severity-medium/15",
    );
  });

  it("test_journal_renders_per_step_colors", () => {
    const events = [
      { id: "flagged", ts: "2026-05-15T01:00:00Z", step: "flagged", text: "flagged", detail: null, data: {} },
      { id: "sampled", ts: "2026-05-15T01:02:00Z", step: "sampled", text: "sampled", detail: null, data: {} },
      { id: "wait", ts: "2026-05-15T01:03:00Z", step: "wait", text: "wait", detail: null, data: {} },
      { id: "merged", ts: "2026-05-15T01:04:00Z", step: "merged", text: "merged", detail: null, data: {} },
      { id: "escalated", ts: "2026-05-15T01:05:00Z", step: "escalated", text: "escalated", detail: null, data: {} },
    ] as unknown as QaJournalEvent[];

    render(<PatrolJournal events={events} patrolIntervalMinutes={12} />);

    expect(screen.getByTestId("qa-journal-step-flagged").className).toContain("text-amber-500");
    expect(screen.getByTestId("qa-journal-step-sampled").className).toContain("text-foreground");
    expect(screen.getByTestId("qa-journal-step-wait").className).toContain("text-muted-foreground");
    expect(screen.getByTestId("qa-journal-step-merged").className).toContain("text-emerald-500");
    expect(screen.getByTestId("qa-journal-step-escalated").className).toContain("text-amber-500");
    expect(screen.getByText("5 entries · patrol every 12m")).toBeTruthy();
  });
});
