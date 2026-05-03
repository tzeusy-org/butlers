import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import FactDetailPage from "@/pages/FactDetailPage";
import { useFact } from "@/hooks/use-memory";
import type { Fact } from "@/api/types";

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useParams: vi.fn(() => ({ factId: "fact-001" })) };
});

vi.mock("@/hooks/use-memory", () => ({
  useFact: vi.fn(),
}));

type UseFactResult = ReturnType<typeof useFact>;

const BASE_FACT: Fact = {
  id: "fact-001",
  subject: "Alice",
  predicate: "prefers",
  content: "dark chocolate over milk chocolate",
  importance: 5,
  confidence: 0.85,
  decay_rate: 0.01,
  permanence: "stable",
  source_butler: "general",
  source_episode_id: "ep-42",
  session_id: null,
  supersedes_id: null,
  entity_id: "entity-001",
  entity_name: "Alice Example",
  object_entity_id: null,
  object_entity_name: null,
  validity: "active",
  scope: "global",
  reference_count: 3,
  created_at: "2025-01-01T12:00:00Z",
  last_referenced_at: "2025-03-01T09:00:00Z",
  last_confirmed_at: null,
  tags: ["food", "preference"],
  metadata: {},
};

function setFactState(fact: Fact | null, opts: Partial<UseFactResult> = {}) {
  vi.mocked(useFact).mockReturnValue({
    data: fact ? { data: fact } : undefined,
    isLoading: false,
    error: null,
    ...opts,
  } as UseFactResult);
}

function renderPage(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <FactDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("FactDetailPage — layout", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders a single H1 (no double-H1 regression)", () => {
    setFactState(BASE_FACT);
    const html = renderPage();
    const h1Matches = html.match(/<h1[^>]*>/g) ?? [];
    expect(h1Matches.length).toBe(1);
  });

  it("renders the fact content as the page title", () => {
    setFactState(BASE_FACT);
    const html = renderPage();
    expect(html).toContain("dark chocolate over milk chocolate");
  });

  it("renders the entity name and predicate as subtitle", () => {
    setFactState(BASE_FACT);
    const html = renderPage();
    expect(html).toContain("Alice Example");
    expect(html).toContain("prefers");
  });

  it("renders the type pill with 'fact'", () => {
    setFactState(BASE_FACT);
    const html = renderPage();
    expect(html).toContain("fact");
  });

  it("renders breadcrumbs back to memory and facts", () => {
    setFactState(BASE_FACT);
    const html = renderPage();
    expect(html).toContain("/memory");
    expect(html).toContain("Facts");
  });
});

describe("FactDetailPage — body content", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders provenance when source_butler and source_episode_id are set", () => {
    setFactState(BASE_FACT);
    const html = renderPage();
    expect(html).toContain("general");
    expect(html).toContain("ep-42");
  });

  it("renders tags", () => {
    setFactState(BASE_FACT);
    const html = renderPage();
    expect(html).toContain("food");
    expect(html).toContain("preference");
  });

  it("renders permanence badge in supporting panel", () => {
    setFactState(BASE_FACT);
    const html = renderPage();
    // permanenceBadge renders 'stable' as a badge
    expect(html).toContain("stable");
    expect(html).toContain("Permanence");
  });

  it("renders confidence progress bar", () => {
    setFactState(BASE_FACT);
    const html = renderPage();
    expect(html).toContain("Confidence");
    expect(html).toContain("85%");
  });

  it("renders validity badge", () => {
    setFactState(BASE_FACT);
    const html = renderPage();
    expect(html).toContain("active");
  });

  it("renders 'No provenance data' when no provenance fields set", () => {
    setFactState({
      ...BASE_FACT,
      source_butler: null,
      source_episode_id: null,
      supersedes_id: null,
    });
    const html = renderPage();
    expect(html).toContain("No provenance data");
  });
});

describe("FactDetailPage — async states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders loading state", () => {
    setFactState(null, { isLoading: true } as Partial<UseFactResult>);
    const html = renderPage();
    // Page renders with loading state — fact content absent
    expect(html).not.toContain("dark chocolate");
  });

  it("renders nothing when fact data is absent and not loading", () => {
    setFactState(null);
    const html = renderPage();
    expect(html).not.toContain("Content");
  });
});
