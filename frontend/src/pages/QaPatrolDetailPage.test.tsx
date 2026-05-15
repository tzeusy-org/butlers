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
// Navigation — breadcrumbs
// ---------------------------------------------------------------------------

describe("QaPatrolDetailPage — navigation", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders a breadcrumb link to /qa", () => {
    setPatrolState(BASE_PATROL);
    const html = renderPage();
    expect(html).toContain('href="/qa"');
    expect(html).toContain("QA");
  });

  it("renders patrol short ID in breadcrumbs", () => {
    setPatrolState(BASE_PATROL);
    const html = renderPage();
    expect(html).toContain("patrol-a");
  });
});

// ---------------------------------------------------------------------------
// Page header
// ---------------------------------------------------------------------------

describe("QaPatrolDetailPage — header", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders 'QA Patrol' mono eyebrow", () => {
    setPatrolState(BASE_PATROL);
    const html = renderPage();
    expect(html).toContain("QA Patrol");
  });

  it("renders H1 with 'Patrol' in the title", () => {
    setPatrolState(BASE_PATROL);
    const html = renderPage();
    expect(html.match(/<h1[^>]*>/g) ?? []).toHaveLength(1);
    const h1 = html.match(/<h1[^>]*>(.*?)<\/h1>/s);
    expect(h1![1]).toContain("Patrol");
  });

  it("renders the log_lookback_minutes in the caption", () => {
    setPatrolState(BASE_PATROL);
    const html = renderPage();
    expect(html).toContain("60");
    expect(html).toContain("lookback");
  });

  it("renders 'clean' status in caption", () => {
    setPatrolState({ ...BASE_PATROL, status: "clean" });
    const html = renderPage();
    expect(html).toContain("clean");
  });

  it("renders 'dispatched' status in caption for findings_dispatched", () => {
    setPatrolState({ ...BASE_PATROL, status: "findings_dispatched" });
    const html = renderPage();
    expect(html).toContain("dispatched");
  });
});

// ---------------------------------------------------------------------------
// Findings section (rule-separated list, no table)
// ---------------------------------------------------------------------------

describe("QaPatrolDetailPage — findings list", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders 'Findings' eyebrow section", () => {
    setPatrolState(BASE_PATROL);
    const html = renderPage();
    expect(html).toContain("Findings");
  });

  it("renders 'No findings' empty state when findings array is empty", () => {
    setPatrolState({ ...BASE_PATROL, findings: [] });
    const html = renderPage();
    expect(html).toContain("No findings");
  });

  it("renders finding event_summary in list row", () => {
    setPatrolState({
      ...BASE_PATROL,
      findings_count: 1,
      novel_count: 1,
      findings: [BASE_FINDING],
    });
    const html = renderPage();
    expect(html).toContain("Unexpected null encountered in pipeline");
  });

  it("renders 'novel' dedup mark for findings without dedup_reason", () => {
    setPatrolState({
      ...BASE_PATROL,
      findings_count: 1,
      findings: [{ ...BASE_FINDING, dedup_reason: null }],
    });
    const html = renderPage();
    expect(html).toContain("novel");
  });

  it("renders source butler name in finding row", () => {
    setPatrolState({
      ...BASE_PATROL,
      findings_count: 1,
      findings: [BASE_FINDING],
    });
    const html = renderPage();
    expect(html).toContain("qa");
  });

  it("does NOT render a <table> element (no table layout)", () => {
    setPatrolState({
      ...BASE_PATROL,
      findings_count: 1,
      findings: [BASE_FINDING],
    });
    const html = renderPage();
    expect(html).not.toContain("<table");
  });
});

// ---------------------------------------------------------------------------
// Dispatch summary section
// ---------------------------------------------------------------------------

describe("QaPatrolDetailPage — dispatch summary", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders 'Dispatched' section when findings have healing_attempt_id", () => {
    setPatrolState({
      ...BASE_PATROL,
      findings_count: 1,
      dispatched_count: 1,
      findings: [{ ...BASE_FINDING, healing_attempt_id: "attempt-xyz" }],
    });
    const html = renderPage();
    expect(html).toContain("Dispatched");
    expect(html).toContain("/qa/investigations/attempt-xyz");
  });

  it("does not render 'Dispatched' section when no findings are dispatched", () => {
    setPatrolState({ ...BASE_PATROL, findings: [] });
    const html = renderPage();
    expect(html).not.toContain("Dispatched (");
  });
});

// ---------------------------------------------------------------------------
// Error detail
// ---------------------------------------------------------------------------

describe("QaPatrolDetailPage — error detail", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders error_detail when present", () => {
    setPatrolState({ ...BASE_PATROL, error_detail: "Timeout during log scan" });
    const html = renderPage();
    expect(html).toContain("Timeout during log scan");
  });
});

// ---------------------------------------------------------------------------
// Not-found / async states
// ---------------------------------------------------------------------------

describe("QaPatrolDetailPage — not-found", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders serif-italic 'Patrol not found.' on error", () => {
    vi.mocked(useQaPatrol).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Not found"),
    } as UseQaPatrolResult);
    const html = renderPage();
    expect(html).toContain("Patrol not found");
    expect(html).toContain("italic");
    expect(html).toContain('href="/qa"');
  });

  it("renders serif-italic 'Patrol not found.' when data is absent", () => {
    vi.mocked(useQaPatrol).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
    } as UseQaPatrolResult);
    const html = renderPage();
    expect(html).toContain("Patrol not found");
  });

  it("renders loading skeleton in loading state (no H1)", () => {
    vi.mocked(useQaPatrol).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as UseQaPatrolResult);
    const html = renderPage();
    expect(html.match(/<h1[^>]*>/g) ?? []).toHaveLength(0);
    expect(html).toContain("animate-pulse");
  });
});

// ---------------------------------------------------------------------------
// No recharts
// ---------------------------------------------------------------------------

describe("QaPatrolDetailPage — no recharts", () => {
  it("page file contains no recharts import", async () => {
    const src = await import("@/pages/QaPatrolDetailPage?raw");
    expect((src as unknown as { default: string }).default).not.toContain("recharts");
  });
});
