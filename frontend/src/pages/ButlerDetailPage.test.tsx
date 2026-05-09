import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerDetailPage from "@/pages/ButlerDetailPage";
import { useButler } from "@/hooks/use-butlers";
import type { ButlerSummary } from "@/api/types";

// Mock react-router's useParams so we can control the butler name
vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useParams: vi.fn(() => ({ name: "general" })),
    useSearchParams: vi.fn(() => [new URLSearchParams(), vi.fn()]),
  };
});

vi.mock("@/hooks/use-butlers", () => ({
  useButler: vi.fn(),
  useButlers: vi.fn(() => ({ data: { data: [] }, isLoading: false })),
  useButlerConfig: vi.fn(() => ({ data: null, isLoading: false })),
  useButlerSkills: vi.fn(() => ({ data: null, isLoading: false })),
  useRuntimeConfig: vi.fn(() => ({ data: null, isLoading: false })),
  usePatchRuntimeConfig: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("@/hooks/use-sessions", () => ({
  useButlerSessions: vi.fn(() => ({ data: null, isLoading: false })),
}));

vi.mock("@/hooks/use-contacts", () => ({
  useUpcomingDates: vi.fn(() => ({ data: [], isLoading: false })),
}));

vi.mock("@/hooks/use-system", () => ({
  useButlerHeartbeats: vi.fn(() => ({ data: null, isLoading: false, error: null })),
}));

vi.mock("@/hooks/use-costs", () => ({
  useCostSummary: vi.fn(() => ({ data: null, isLoading: false })),
}));

vi.mock("@/hooks/use-notifications", () => ({
  useButlerNotifications: vi.fn(() => ({ data: null, isLoading: false })),
}));

vi.mock("@/hooks/use-general", () => ({
  useRegistry: vi.fn(() => ({ data: null, isLoading: false })),
  useSetEligibility: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("@/components/chat/ChatPanel", () => ({
  ChatPanel: ({ butlerName }: { butlerName: string }) => (
    <div data-testid="chat-panel">{butlerName}</div>
  ),
}));

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

type UseButlerResult = ReturnType<typeof useButler>;

const BASE_BUTLER: ButlerSummary = {
  name: "general",
  status: "ok",
  port: 8001,
  type: "butler",
  sessions_24h: 0,
};

function setButlerState(butler: ButlerSummary | null, opts: Partial<UseButlerResult> = {}) {
  vi.mocked(useButler).mockReturnValue({
    data: butler ? { data: butler } : undefined,
    isLoading: false,
    error: null,
    ...opts,
  } as UseButlerResult);
}

function renderPage(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ButlerDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Single-H1 contract — ButlerDetailPage
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — single-H1 contract", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setButlerState(BASE_BUTLER);
  });

  it("renders exactly one <h1> element", () => {
    const html = renderPage();
    const h1Matches = html.match(/<h1[\s>]/g) ?? [];
    expect(h1Matches).toHaveLength(1);
  });

  it("h1 contains the butler name", () => {
    const html = renderPage();
    const h1Match = html.match(/<h1[^>]*>(.*?)<\/h1>/s);
    expect(h1Match).not.toBeNull();
    expect(h1Match![1]).toContain("general");
  });

  it("tabs block remains inside the primary content — no second h1", () => {
    const html = renderPage();
    // Tabs render Overview, Sessions, etc. — none should generate an h1
    expect(html).toContain("Overview");
    expect(html).toContain("Sessions");
    const h1Matches = html.match(/<h1[\s>]/g) ?? [];
    expect(h1Matches).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// ChatPanel — actions slot placement
// ---------------------------------------------------------------------------
//
// spec: openspec/specs/dashboard-butler-management/spec.md §96-99
// bead: bu-sfeuw.2
//
// ChatPanel MUST be rendered exactly once via the <Page> actions slot.
// The static-markup assertions below pin single-mount placement in the heading
// row. Any duplicate or shadow render would produce more than one
// data-testid="chat-panel" occurrence.
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — ChatPanel actions slot", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setButlerState(BASE_BUTLER);
  });

  it("renders exactly one ChatPanel instance", () => {
    const html = renderPage();
    const occurrences = (html.match(/data-testid="chat-panel"/g) ?? []).length;
    expect(occurrences).toBe(1);
  });

  it("ChatPanel receives the butler name as butlerName prop", () => {
    const html = renderPage();
    // The mock renders the butlerName as text content inside the stub element
    expect(html).toContain(
      '<div data-testid="chat-panel">general</div>',
    );
  });

  it("ChatPanel appears in the heading region alongside the h1", () => {
    const html = renderPage();
    // The heading row is a flex container with the h1 and the shrink-0 actions div.
    // Verify both the h1 and the chat-panel stub are present in the same document.
    const h1Index = html.indexOf("<h1");
    const chatPanelIndex = html.indexOf('data-testid="chat-panel"');
    expect(h1Index).toBeGreaterThanOrEqual(0);
    expect(chatPanelIndex).toBeGreaterThanOrEqual(0);
    // actions slot renders after the heading opening tag in document order
    expect(chatPanelIndex).toBeGreaterThan(h1Index);
  });
});

// ---------------------------------------------------------------------------
// Basic rendering
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — rendering", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setButlerState(BASE_BUTLER);
  });

  it("renders the butler name as page title", () => {
    const html = renderPage();
    expect(html).toContain("general");
  });

  it("renders tabs block inside the page", () => {
    const html = renderPage();
    // Core tab labels are always present
    expect(html).toContain("Sessions");
    expect(html).toContain("Config");
    expect(html).toContain("Skills");
  });

  it("renders breadcrumbs with Overview and Butlers links", () => {
    const html = renderPage();
    expect(html).toContain("/butlers");
    expect(html).toContain("Butlers");
  });
});
