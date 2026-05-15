import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import QaInvestigationDetailPage from "@/pages/QaInvestigationDetailPage";
import { useQaCase } from "@/hooks/use-qa";
import type { QaCaseDossier } from "@/api/types";

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useParams: vi.fn(() => ({ attemptId: "attempt-abc12345" })),
  };
});

vi.mock("@/hooks/use-qa", () => ({
  useQaCase: vi.fn(),
  useQaCaseJournal: vi.fn(() => ({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
  })),
  useRemoveDismissal: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
  })),
}));

type UseQaCaseResult = ReturnType<typeof useQaCase>;

const BASE_CASE_SUMMARY = {
  id: "attempt-abc12345",
  short_id: "#218",
  sev: "high" as const,
  butler: "qa",
  headline: "Spotify ingestion failing",
  detected: "2025-03-01T10:00:00Z",
  age_seconds: 3600,
  state: "diagnose" as const,
  pr_state: null,
  pr_url: null,
};

const BASE_DOSSIER: QaCaseDossier = {
  case: BASE_CASE_SUMMARY,
  state_track_stage: "diagnose",
  dismissal: null,
  investigation_notes: null,
  pr: null,
  journal: [],
};

function setCaseState(
  dossier: QaCaseDossier | null,
  opts: Partial<UseQaCaseResult> = {},
) {
  vi.mocked(useQaCase).mockReturnValue({
    data: dossier ? { data: dossier } : undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...opts,
  } as UseQaCaseResult);
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
// Back-link / breadcrumb
// ---------------------------------------------------------------------------

describe("QaInvestigationDetailPage — navigation", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders a breadcrumb back-link to /qa", () => {
    setCaseState(BASE_DOSSIER);
    const html = renderPage();
    expect(html).toContain('href="/qa"');
    expect(html).toContain("QA");
  });

  it("shows the eyebrow with short_id once the case is loaded", () => {
    setCaseState(BASE_DOSSIER);
    const html = renderPage();
    // short_id already includes the leading '#' — the eyebrow must not double it
    expect(html).toContain("QA Investigation · #218");
    expect(html).not.toContain("##218");
  });
});

// ---------------------------------------------------------------------------
// CaseDossier mount
// ---------------------------------------------------------------------------

describe("QaInvestigationDetailPage — CaseDossier", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("mounts CaseDossier when case is loaded", () => {
    setCaseState(BASE_DOSSIER);
    const html = renderPage();
    // CaseDossier renders a data-testid="qa-case-dossier" article
    expect(html).toContain("qa-case-dossier");
  });

  it("shows the case headline from investigation notes headline fallback", () => {
    setCaseState(BASE_DOSSIER);
    const html = renderPage();
    expect(html).toContain("Spotify ingestion failing");
  });

  it("renders the StateTrack for the case stage", () => {
    setCaseState(BASE_DOSSIER);
    const html = renderPage();
    // StateTrack renders stage labels; "diagnose" stage is visible
    expect(html).toContain("diagnose");
  });
});

// ---------------------------------------------------------------------------
// Not-found / error states
// ---------------------------------------------------------------------------

describe("QaInvestigationDetailPage — not-found", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders serif-italic 'Investigation not found.' on error", () => {
    setCaseState(null, { isError: true, error: new Error("Not found") });
    const html = renderPage();
    expect(html).toContain("Investigation not found");
    expect(html).toContain("italic");
    expect(html).toContain('href="/qa"');
  });

  it("renders serif-italic 'Investigation not found.' when data is absent", () => {
    setCaseState(null, { isError: false, error: null });
    const html = renderPage();
    expect(html).toContain("Investigation not found");
  });
});

// ---------------------------------------------------------------------------
// No recharts
// ---------------------------------------------------------------------------

describe("QaInvestigationDetailPage — no recharts", () => {
  it("page file contains no recharts import", async () => {
    const src = await import("@/pages/QaInvestigationDetailPage?raw");
    expect((src as unknown as { default: string }).default).not.toContain("recharts");
  });
});
