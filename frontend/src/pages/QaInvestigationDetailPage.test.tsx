import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import QaInvestigationDetailPage from "@/pages/QaInvestigationDetailPage";
import { useHealingAttempt, useQaFindingByAttempt } from "@/hooks/use-qa";
import type { HealingAttempt, QaFindingRecord } from "@/api/types";

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useParams: vi.fn(() => ({ attemptId: "attempt-abc12345" })),
  };
});

vi.mock("@/hooks/use-qa", () => ({
  useHealingAttempt: vi.fn(),
  useQaFindingByAttempt: vi.fn(),
}));

type UseHealingAttemptResult = ReturnType<typeof useHealingAttempt>;
type UseQaFindingByAttemptResult = ReturnType<typeof useQaFindingByAttempt>;

const BASE_ATTEMPT: HealingAttempt = {
  id: "attempt-abc12345",
  fingerprint: "fp-aabbccddee112233",
  butler_name: "qa",
  status: "investigating",
  severity: 1,
  exception_type: "ValueError",
  call_site: "src/butler.py:42",
  sanitized_msg: "Unexpected null value in field",
  branch_name: null,
  worktree_path: null,
  pr_url: null,
  pr_number: null,
  session_ids: [],
  healing_session_id: null,
  created_at: "2025-03-01T10:00:00Z",
  updated_at: "2025-03-01T10:05:00Z",
  closed_at: null,
  error_detail: null,
};

const BASE_FINDING: QaFindingRecord = {
  id: "finding-001",
  patrol_id: "patrol-xyz",
  fingerprint: "fp-aabbccddee112233",
  source_type: "log_scanner",
  source_butler: "qa",
  severity: 1,
  exception_type: "ValueError",
  event_summary: "Unexpected null encountered in pipeline",
  call_site: "src/butler.py:42",
  occurrence_count: 3,
  first_seen: "2025-03-01T10:00:00Z",
  last_seen: "2025-03-01T10:30:00Z",
  dedup_reason: null,
  healing_attempt_id: "attempt-abc12345",
  source_session_trigger_source: null,
  structured_evidence: null,
  created_at: "2025-03-01T10:00:00Z",
};

function setAttemptState(
  attempt: HealingAttempt | null,
  opts: Partial<UseHealingAttemptResult> = {},
) {
  vi.mocked(useHealingAttempt).mockReturnValue({
    data: attempt ?? undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...opts,
  } as UseHealingAttemptResult);
}

function setFindingState(
  finding: QaFindingRecord | null,
  opts: Partial<UseQaFindingByAttemptResult> = {},
) {
  vi.mocked(useQaFindingByAttempt).mockReturnValue({
    data: finding ? { data: finding } : undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...opts,
  } as UseQaFindingByAttemptResult);
}

function renderPage(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <QaInvestigationDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Single-H1 contract — QaInvestigationDetailPage
// ---------------------------------------------------------------------------

describe("QaInvestigationDetailPage — single-H1 contract", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders exactly one H1 when attempt is loaded", () => {
    setAttemptState(BASE_ATTEMPT);
    setFindingState(null);
    const html = renderPage();
    expect(html.match(/<h1[^>]*>/g) ?? []).toHaveLength(1);
  });

  it("H1 contains 'Investigation Detail'", () => {
    setAttemptState(BASE_ATTEMPT);
    setFindingState(null);
    const html = renderPage();
    const h1 = html.match(/<h1[^>]*>(.*?)<\/h1>/s);
    expect(h1).not.toBeNull();
    expect(h1![1]).toContain("Investigation Detail");
  });

  it("renders zero H1s in loading state (skeleton, no heading)", () => {
    vi.mocked(useHealingAttempt).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as UseHealingAttemptResult);
    vi.mocked(useQaFindingByAttempt).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as UseQaFindingByAttemptResult);
    const html = renderPage();
    expect(html.match(/<h1[^>]*>/g) ?? []).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Content — QaInvestigationDetailPage
// ---------------------------------------------------------------------------

describe("QaInvestigationDetailPage — content", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders attempt ID short form in breadcrumbs", () => {
    setAttemptState(BASE_ATTEMPT);
    setFindingState(null);
    const html = renderPage();
    // Third breadcrumb shows first 8 chars of attempt ID
    expect(html).toContain("attempt-");
  });

  it("renders breadcrumb links to /qa and /qa/investigations", () => {
    setAttemptState(BASE_ATTEMPT);
    setFindingState(null);
    const html = renderPage();
    expect(html).toContain("/qa");
    expect(html).toContain("/qa/investigations");
  });

  it("renders 'investigating' status badge", () => {
    setAttemptState({ ...BASE_ATTEMPT, status: "investigating" });
    setFindingState(null);
    const html = renderPage();
    expect(html).toContain("investigating");
  });

  it("renders 'PR merged' status badge for pr_merged status", () => {
    setAttemptState({ ...BASE_ATTEMPT, status: "pr_merged" });
    setFindingState(null);
    const html = renderPage();
    expect(html).toContain("PR merged");
  });

  it("renders 'failed' status badge", () => {
    setAttemptState({ ...BASE_ATTEMPT, status: "failed" });
    setFindingState(null);
    const html = renderPage();
    expect(html).toContain("failed");
  });

  it("renders severity badge (high=1 → high label)", () => {
    setAttemptState({ ...BASE_ATTEMPT, severity: 1 });
    setFindingState(null);
    const html = renderPage();
    expect(html).toContain("high");
  });

  it("renders the full attempt ID in metadata card", () => {
    setAttemptState(BASE_ATTEMPT);
    setFindingState(null);
    const html = renderPage();
    expect(html).toContain("attempt-abc12345");
    expect(html).toContain("Metadata");
  });

  it("renders butler name badge", () => {
    setAttemptState(BASE_ATTEMPT);
    setFindingState(null);
    const html = renderPage();
    expect(html).toContain("qa");
  });

  it("renders fingerprint (truncated to 16 chars)", () => {
    setAttemptState(BASE_ATTEMPT);
    setFindingState(null);
    const html = renderPage();
    expect(html).toContain("fp-aabbccddee112");
  });

  it("renders exception type in error context card", () => {
    setAttemptState(BASE_ATTEMPT);
    setFindingState(null);
    const html = renderPage();
    expect(html).toContain("ValueError");
    expect(html).toContain("Error Context");
  });

  it("renders call site", () => {
    setAttemptState(BASE_ATTEMPT);
    setFindingState(null);
    const html = renderPage();
    expect(html).toContain("src/butler.py:42");
  });

  it("renders sanitized message when present", () => {
    setAttemptState(BASE_ATTEMPT);
    setFindingState(null);
    const html = renderPage();
    expect(html).toContain("Unexpected null value in field");
  });

  it("renders timeline card", () => {
    setAttemptState(BASE_ATTEMPT);
    setFindingState(null);
    const html = renderPage();
    expect(html).toContain("Timeline");
    expect(html).toContain("Dispatched");
    expect(html).toContain("Investigating");
  });
});

// ---------------------------------------------------------------------------
// Dispatch reason card — QaFindingRecord integration
// ---------------------------------------------------------------------------

describe("QaInvestigationDetailPage — dispatch reason card", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders 'No QA finding linked' message when finding is absent", () => {
    setAttemptState(BASE_ATTEMPT);
    setFindingState(null);
    const html = renderPage();
    expect(html).toContain("Dispatch Reason");
    expect(html).toContain("No QA finding is linked to this attempt");
  });

  it("renders finding summary when finding is present", () => {
    setAttemptState(BASE_ATTEMPT);
    setFindingState(BASE_FINDING);
    const html = renderPage();
    expect(html).toContain("Unexpected null encountered in pipeline");
    expect(html).toContain("Occurrences");
    expect(html).toContain("3");
  });

  it("renders patrol link in dispatch reason card", () => {
    setAttemptState(BASE_ATTEMPT);
    setFindingState(BASE_FINDING);
    const html = renderPage();
    expect(html).toContain("/qa/patrols/patrol-xyz");
  });
});

// ---------------------------------------------------------------------------
// PR card
// ---------------------------------------------------------------------------

describe("QaInvestigationDetailPage — PR card", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders PR card when pr_url is present", () => {
    setAttemptState({
      ...BASE_ATTEMPT,
      status: "pr_open",
      pr_url: "https://github.com/org/repo/pull/42",
      pr_number: 42,
    });
    setFindingState(null);
    const html = renderPage();
    expect(html).toContain("Pull Request");
    expect(html).toContain("#42");
    expect(html).toContain("Open on GitHub");
    expect(html).toContain("https://github.com/org/repo/pull/42");
  });

  it("does not render PR card when pr_url and pr_number are absent", () => {
    setAttemptState({ ...BASE_ATTEMPT, pr_url: null, pr_number: null });
    setFindingState(null);
    const html = renderPage();
    expect(html).not.toContain("Pull Request");
  });
});

// ---------------------------------------------------------------------------
// Triggering sessions card
// ---------------------------------------------------------------------------

describe("QaInvestigationDetailPage — triggering sessions card", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders triggering sessions when session_ids are present", () => {
    setAttemptState({
      ...BASE_ATTEMPT,
      session_ids: ["sess-111", "sess-222"],
    });
    setFindingState(null);
    const html = renderPage();
    expect(html).toContain("Triggering Sessions");
    expect(html).toContain("sess-111");
    expect(html).toContain("sess-222");
    expect(html).toContain("/sessions/sess-111?butler=qa");
  });

  it("does not render triggering sessions when session_ids is empty", () => {
    setAttemptState({ ...BASE_ATTEMPT, session_ids: [] });
    setFindingState(null);
    const html = renderPage();
    expect(html).not.toContain("Triggering Sessions");
  });
});

// ---------------------------------------------------------------------------
// Error / not-found states
// ---------------------------------------------------------------------------

describe("QaInvestigationDetailPage — async states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders error state when attempt fetch fails", () => {
    vi.mocked(useHealingAttempt).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Not found"),
    } as UseHealingAttemptResult);
    vi.mocked(useQaFindingByAttempt).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
    } as UseQaFindingByAttemptResult);
    const html = renderPage();
    expect(html).toContain("Investigation not found or failed to load");
    expect(html).toContain("Back to QA");
  });

  it("renders error state when attempt data is absent", () => {
    vi.mocked(useHealingAttempt).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
    } as UseHealingAttemptResult);
    vi.mocked(useQaFindingByAttempt).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
    } as UseQaFindingByAttemptResult);
    const html = renderPage();
    expect(html).toContain("Investigation not found or failed to load");
  });
});

// ---------------------------------------------------------------------------
// Slot composition baseline — for Gate-A change tracking
// ---------------------------------------------------------------------------

describe("QaInvestigationDetailPage — slot composition baseline", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  // Breadcrumbs: rendered directly by page (not via Page archetype=detail)
  it("breadcrumbs are rendered directly by the page (not via Page archetype)", () => {
    setAttemptState(BASE_ATTEMPT);
    setFindingState(null);
    const html = renderPage();
    expect(html).toContain('aria-label="Breadcrumb"');
    // Not using DetailPage shell — no max-w-5xl constraint
    expect(html).not.toContain("max-w-5xl");
  });

  // No Tier-2 hero (no PulseStrip, no DetailPage shell)
  it("does not render a Tier-2 hero or PulseStrip today (pre-redesign baseline)", () => {
    setAttemptState(BASE_ATTEMPT);
    setFindingState(null);
    const html = renderPage();
    expect(html).not.toContain("max-w-5xl");
    expect(html).not.toContain("Dunbar tier");
  });

  // Primary slot: Metadata, Timeline, Error Context, Dispatch Reason present
  it("primary content includes Metadata, Timeline, Error Context, and Dispatch Reason", () => {
    setAttemptState(BASE_ATTEMPT);
    setFindingState(null);
    const html = renderPage();
    expect(html).toContain("Metadata");
    expect(html).toContain("Timeline");
    expect(html).toContain("Error Context");
    expect(html).toContain("Dispatch Reason");
  });

  // No pulse slot today
  it("does not render a pulse strip (pre-redesign baseline)", () => {
    setAttemptState(BASE_ATTEMPT);
    setFindingState(null);
    const html = renderPage();
    // PulseStrip would contain Dunbar-tier metric rows
    expect(html).not.toContain("Last interaction");
  });
});
