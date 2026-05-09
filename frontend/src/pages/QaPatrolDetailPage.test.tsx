import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import QaPatrolDetailPage from "@/pages/QaPatrolDetailPage";
import { useQaPatrol } from "@/hooks/use-qa";
import type { QaPatrolDetail, QaFindingRecord } from "@/api/types";

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useParams: vi.fn(() => ({ patrolId: "patrol-abc12345" })),
  };
});

vi.mock("@/hooks/use-qa", () => ({
  useQaPatrol: vi.fn(),
}));

type UseQaPatrolResult = ReturnType<typeof useQaPatrol>;

const BASE_FINDING: QaFindingRecord = {
  id: "finding-001",
  patrol_id: "patrol-abc12345",
  fingerprint: "fp-aabbcc",
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
  healing_attempt_id: null,
  source_session_trigger_source: null,
  structured_evidence: null,
  created_at: "2025-03-01T10:00:00Z",
};

const BASE_PATROL: QaPatrolDetail = {
  id: "patrol-abc12345",
  started_at: "2025-03-01T10:00:00Z",
  completed_at: "2025-03-01T10:05:00Z",
  status: "clean",
  findings_count: 0,
  novel_count: 0,
  dispatched_count: 0,
  log_lookback_minutes: 60,
  sources_polled: ["log_scanner"],
  error_detail: null,
  findings: [],
};

function setPatrolState(patrol: QaPatrolDetail | null, opts: Partial<UseQaPatrolResult> = {}) {
  vi.mocked(useQaPatrol).mockReturnValue({
    data: patrol ? { data: patrol } : undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...opts,
  } as UseQaPatrolResult);
}

function renderPage(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <QaPatrolDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Single-H1 contract — QaPatrolDetailPage
// ---------------------------------------------------------------------------

describe("QaPatrolDetailPage — single-H1 contract", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders exactly one H1 when patrol is loaded", () => {
    setPatrolState(BASE_PATROL);
    const html = renderPage();
    expect(html.match(/<h1[^>]*>/g) ?? []).toHaveLength(1);
  });

  it("H1 contains 'Patrol Detail'", () => {
    setPatrolState(BASE_PATROL);
    const html = renderPage();
    const h1 = html.match(/<h1[^>]*>(.*?)<\/h1>/s);
    expect(h1).not.toBeNull();
    expect(h1![1]).toContain("Patrol Detail");
  });

  it("renders zero H1s in loading state (skeleton, no heading)", () => {
    vi.mocked(useQaPatrol).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as UseQaPatrolResult);
    const html = renderPage();
    expect(html.match(/<h1[^>]*>/g) ?? []).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Content — QaPatrolDetailPage
// ---------------------------------------------------------------------------

describe("QaPatrolDetailPage — content", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders the patrol ID short form in breadcrumbs", () => {
    setPatrolState(BASE_PATROL);
    const html = renderPage();
    // Breadcrumb shows first 8 chars of patrol ID
    expect(html).toContain("patrol-a");
  });

  it("renders breadcrumb link to /qa", () => {
    setPatrolState(BASE_PATROL);
    const html = renderPage();
    expect(html).toContain("/qa");
    expect(html).toContain("QA");
  });

  it("renders clean status badge for clean patrol", () => {
    setPatrolState({ ...BASE_PATROL, status: "clean" });
    const html = renderPage();
    expect(html).toContain("clean");
  });

  it("renders 'dispatched' badge for findings_dispatched status", () => {
    setPatrolState({ ...BASE_PATROL, status: "findings_dispatched" });
    const html = renderPage();
    expect(html).toContain("dispatched");
  });

  it("renders metadata section with patrol full ID", () => {
    setPatrolState(BASE_PATROL);
    const html = renderPage();
    expect(html).toContain("patrol-abc12345");
    expect(html).toContain("Metadata");
  });

  it("renders lookback duration", () => {
    setPatrolState(BASE_PATROL);
    const html = renderPage();
    expect(html).toContain("60");
    expect(html).toContain("minutes");
  });

  it("renders findings count", () => {
    setPatrolState({ ...BASE_PATROL, findings_count: 5, novel_count: 2 });
    const html = renderPage();
    expect(html).toContain("5");
    expect(html).toContain("2");
  });

  it("renders 'No findings in this patrol' when findings array is empty", () => {
    setPatrolState({ ...BASE_PATROL, findings: [] });
    const html = renderPage();
    expect(html).toContain("No findings");
  });

  it("renders findings table when findings are present", () => {
    setPatrolState({
      ...BASE_PATROL,
      findings_count: 1,
      findings: [BASE_FINDING],
    });
    const html = renderPage();
    expect(html).toContain("Severity");
    expect(html).toContain("Exception");
    expect(html).toContain("ValueError");
    expect(html).toContain("Unexpected null encountered in pipeline");
  });

  it("renders 'novel' dedup badge for findings without dedup_reason", () => {
    setPatrolState({
      ...BASE_PATROL,
      findings_count: 1,
      findings: [{ ...BASE_FINDING, dedup_reason: null }],
    });
    const html = renderPage();
    expect(html).toContain("novel");
  });

  it("renders error_detail in metadata when present", () => {
    setPatrolState({ ...BASE_PATROL, error_detail: "Timeout during log scan" });
    const html = renderPage();
    expect(html).toContain("Timeout during log scan");
  });

  it("renders dispatched investigations card when any finding has healing_attempt_id", () => {
    setPatrolState({
      ...BASE_PATROL,
      findings_count: 1,
      dispatched_count: 1,
      findings: [{ ...BASE_FINDING, healing_attempt_id: "attempt-xyz" }],
    });
    const html = renderPage();
    expect(html).toContain("Dispatched Investigations");
    expect(html).toContain("/qa/investigations/attempt-xyz");
  });

  it("does not render dispatched investigations card when no findings are dispatched", () => {
    setPatrolState({ ...BASE_PATROL, findings: [] });
    const html = renderPage();
    expect(html).not.toContain("Dispatched Investigations");
  });
});

// ---------------------------------------------------------------------------
// Error / not-found states
// ---------------------------------------------------------------------------

describe("QaPatrolDetailPage — async states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders error state when patrol not found", () => {
    vi.mocked(useQaPatrol).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Not found"),
    } as UseQaPatrolResult);
    const html = renderPage();
    expect(html).toContain("Patrol not found or failed to load");
    expect(html).toContain("Back to QA");
  });

  it("renders error state when patrol data is absent", () => {
    vi.mocked(useQaPatrol).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
    } as UseQaPatrolResult);
    const html = renderPage();
    expect(html).toContain("Patrol not found or failed to load");
  });
});

// ---------------------------------------------------------------------------
// Slot composition baseline — for Gate-A change tracking
// ---------------------------------------------------------------------------

describe("QaPatrolDetailPage — slot composition baseline", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  // Breadcrumbs: rendered directly by page (not via Page archetype=detail)
  it("breadcrumbs are rendered directly by the page (not via Page archetype)", () => {
    setPatrolState(BASE_PATROL);
    const html = renderPage();
    expect(html).toContain('aria-label="Breadcrumb"');
    // Not using DetailPage shell — no max-w-5xl constraint
    expect(html).not.toContain("max-w-5xl");
  });

  // No Tier-2 hero (no PulseStrip, no DetailPage shell)
  it("does not render a Tier-2 hero or PulseStrip today (pre-redesign baseline)", () => {
    setPatrolState(BASE_PATROL);
    const html = renderPage();
    expect(html).not.toContain("max-w-5xl");
    expect(html).not.toContain("Dunbar tier");
  });

  // Actions slot: no top-level action buttons in the loaded state
  it("does not render Back to QA button in loaded (non-error) state", () => {
    setPatrolState(BASE_PATROL);
    const html = renderPage();
    // Back to QA button only appears in the error state
    expect(html).not.toContain("Back to QA");
  });

  // Primary slot: Metadata card and All Findings card present
  it("primary content includes Metadata and All Findings sections", () => {
    setPatrolState(BASE_PATROL);
    const html = renderPage();
    expect(html).toContain("Metadata");
    expect(html).toContain("All Findings");
  });
});
