// @vitest-environment jsdom
//
// SearchResults truthfulness (bu-kqnum.14.5): an in-flight search shows a loading
// line (never the empty verdict), a dropped pool is named (with or without rows),
// and results page by offset with the exact total from the inspect meta.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import SearchResults from "@/components/memory/SearchResults";
import { useMemoryInspect, useRetryEpisodeConsolidation } from "@/hooks/use-memory";
import type { MemoryInspectResult } from "@/api/types";

vi.mock("@/hooks/use-memory", () => ({
  useMemoryInspect: vi.fn(),
  useRetryEpisodeConsolidation: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type UseInspectResult = ReturnType<typeof useMemoryInspect>;

const EMPTY_VERDICT = "Nothing in the books.";

function episodeResult(id: string): MemoryInspectResult {
  return {
    id,
    kind: "episode",
    content: `episode ${id}`,
    butler: "general",
    created_at: "2026-02-19T01:00:00Z",
    metadata: {},
    episode: {
      id,
      butler: "general",
      session_id: null,
      content: `episode ${id}`,
      importance: 5,
      reference_count: 0,
      consolidated: false,
      consolidation_status: "pending",
      created_at: "2026-02-19T01:00:00Z",
      last_referenced_at: null,
      expires_at: null,
      metadata: {},
    },
  } as unknown as MemoryInspectResult;
}

function prime(
  rows: MemoryInspectResult[],
  meta: Record<string, unknown> = {},
  extra: Record<string, unknown> = {},
) {
  vi.mocked(useMemoryInspect).mockReturnValue({
    data: {
      data: rows,
      meta: { total: rows.length, offset: 0, limit: 50, has_more: false, ...meta },
    },
    ...extra,
  } as unknown as UseInspectResult);
}

describe("SearchResults degraded and paging states", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useRetryEpisodeConsolidation).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof useRetryEpisodeConsolidation>);
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function render(props: Partial<React.ComponentProps<typeof SearchResults>> = {}) {
    act(() => {
      root.render(
        <MemoryRouter>
          <SearchResults q="fatigue" kind="all" {...props} />
        </MemoryRouter>,
      );
    });
  }

  it("shows a loading line, never the empty verdict, before any response (even paused)", () => {
    // Pending but not fetching: a query paused offline has isLoading false.
    vi.mocked(useMemoryInspect).mockReturnValue({
      data: undefined,
      isPending: true,
      isLoading: false,
    } as unknown as UseInspectResult);

    render();

    expect(container.querySelector('[data-testid="memory-search-loading"]')).not.toBeNull();
    expect(container.textContent).not.toContain(EMPTY_VERDICT);
  });

  it("keeps the empty verdict for a healthy, successful empty response", () => {
    prime([]);

    render();

    expect(container.textContent).toContain(EMPTY_VERDICT);
    expect(container.querySelector('[data-testid="memory-search-degraded"]')).toBeNull();
  });

  it("names the failed pool and withholds the empty verdict when zero rows came back", () => {
    prime([], { pools_failed: ["finance"] });

    render();

    const note = container.querySelector('[data-testid="memory-search-degraded"]');
    expect(note?.textContent).toContain("finance");
    expect(container.textContent).not.toContain(EMPTY_VERDICT);
  });

  it("shows the exact range and total, qualified as partial when a pool is missing", () => {
    prime([episodeResult("e1")], { total: 312, offset: 50, has_more: true });
    render({ offset: 50, onOffset: vi.fn() });
    expect(container.textContent).toContain("showing 51–51 of 312");
    expect(container.textContent).not.toContain("(partial)");

    prime([episodeResult("e1")], { total: 7, pools_failed: ["finance"] });
    render({ onOffset: vi.fn() });
    expect(container.textContent).toContain("showing 1–1 of 7 (partial)");
    expect(container.textContent).toContain("episode e1");
    expect(
      container.querySelector('[data-testid="memory-search-degraded"]')?.textContent,
    ).toContain("finance");
  });

  it("offers the way back, not the empty verdict, when paged past the last result", () => {
    prime([], { total: 60, offset: 100, has_more: false });

    render({ offset: 100, onOffset: vi.fn() });

    expect(container.textContent).not.toContain(EMPTY_VERDICT);
    expect(container.textContent).toContain("Nothing on this page.");
    expect(container.textContent).toContain("showing 0 of 60");
  });
});
