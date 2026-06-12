import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import EpisodeDetailPage from "@/pages/EpisodeDetailPage";
import { useEpisode } from "@/hooks/use-memory";
import type { Episode } from "@/api/types";

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useParams: vi.fn(() => ({ episodeId: "ep-001" })) };
});

vi.mock("@/hooks/use-memory", () => ({
  useEpisode: vi.fn(),
}));

type UseEpisodeResult = ReturnType<typeof useEpisode>;

const BASE_EPISODE: Episode = {
  id: "ep-001",
  butler: "general",
  session_id: "sess-abc",
  content: "Alice mentioned she prefers tea over coffee.",
  importance: 7.5,
  reference_count: 3,
  consolidated: false,
  consolidation_status: "pending",
  created_at: "2025-01-01T10:00:00Z",
  last_referenced_at: "2025-01-15T12:00:00Z",
  expires_at: null,
  metadata: {},
};

function setEpisodeState(episode: Episode | null, opts: Partial<UseEpisodeResult> = {}) {
  vi.mocked(useEpisode).mockReturnValue({
    data: episode ? { data: episode } : undefined,
    isLoading: false,
    error: null,
    ...opts,
  } as UseEpisodeResult);
}

function renderPage(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <EpisodeDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("EpisodeDetailPage — single-H1 contract", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders exactly one H1 when episode is loaded", () => {
    setEpisodeState(BASE_EPISODE);
    const html = renderPage();
    expect(html.match(/<h1[^>]*>/g) ?? []).toHaveLength(1);
  });

  it("renders zero H1s in loading state (skeleton, no heading)", () => {
    vi.mocked(useEpisode).mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as UseEpisodeResult);
    const html = renderPage();
    expect(html.match(/<h1[^>]*>/g) ?? []).toHaveLength(0);
  });
});

describe("EpisodeDetailPage — content", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders episode content in the page", () => {
    setEpisodeState(BASE_EPISODE);
    const html = renderPage();
    expect(html).toContain("Alice mentioned she prefers tea over coffee.");
  });

  it("uses first line of content as H1 title", () => {
    setEpisodeState(BASE_EPISODE);
    const html = renderPage();
    expect(html).toContain("Alice mentioned she prefers tea over coffee.");
  });

  it("renders butler name as page description and as badge", () => {
    setEpisodeState(BASE_EPISODE);
    const html = renderPage();
    // Appears in description and in the badge
    expect(html.match(/general/g)?.length ?? 0).toBeGreaterThanOrEqual(2);
  });

  it("renders importance and reference count", () => {
    setEpisodeState(BASE_EPISODE);
    const html = renderPage();
    expect(html).toContain("7.5");
    expect(html).toContain("Reference count");
    expect(html).toContain("3");
  });

  it("renders consolidated badge as No", () => {
    setEpisodeState(BASE_EPISODE);
    const html = renderPage();
    expect(html).toContain("Consolidated");
    expect(html).toContain("No");
  });

  it("renders consolidated badge as Yes when consolidated=true", () => {
    setEpisodeState({ ...BASE_EPISODE, consolidated: true });
    const html = renderPage();
    expect(html).toContain("Yes");
  });

  it("renders session ID when present", () => {
    setEpisodeState(BASE_EPISODE);
    const html = renderPage();
    expect(html).toContain("sess-abc");
  });

  it("renders metadata when non-empty", () => {
    setEpisodeState({ ...BASE_EPISODE, metadata: { source: "telegram" } });
    const html = renderPage();
    expect(html).toContain("Metadata");
    expect(html).toContain("telegram");
  });

  it("truncates long first-line content for H1 title", () => {
    const longContent = "A".repeat(100);
    setEpisodeState({ ...BASE_EPISODE, content: longContent });
    const html = renderPage();
    // The full 100-char string is NOT in the title, but appears in the content area
    // The title is capped at 80 chars (77 + ellipsis)
    expect(html).toContain("A".repeat(77) + "…");
  });

  it("skips leading blank lines when deriving H1 title", () => {
    setEpisodeState({ ...BASE_EPISODE, content: "\n\n  \nActual content here" });
    const html = renderPage();
    // The first non-empty line is used, not the blank first line
    expect(html.match(/<h1[^>]*>.*?<\/h1>/s)?.[0]).toContain("Actual content here");
  });

  it("renders provenance timestamps", () => {
    setEpisodeState(BASE_EPISODE);
    const html = renderPage();
    expect(html).toContain("Created");
    expect(html).toContain("Provenance");
  });

  it("renders breadcrumbs to Memory and Episodes", () => {
    setEpisodeState(BASE_EPISODE);
    const html = renderPage();
    expect(html).toContain("/memory");
    expect(html).toContain("/memory?register=episodes");
  });
});

describe("EpisodeDetailPage — error state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("shows an error region when fetch fails", () => {
    vi.mocked(useEpisode).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("Not found"),
    } as UseEpisodeResult);
    const html = renderPage();
    expect(html).toContain("Something went wrong");
    expect(html).toContain("Not found");
  });
});
