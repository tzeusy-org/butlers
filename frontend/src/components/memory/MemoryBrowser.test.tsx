// @vitest-environment jsdom
//
// MemoryBrowser wires the three register components under tabs. This suite only
// asserts that the episodes tab renders the daybook (EpisodesRegister) — the
// daybook's own behavior is covered exhaustively in EpisodesRegister.test.tsx.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import MemoryBrowser from "@/components/memory/MemoryBrowser";
import { useEpisodes, useFacts, useRules } from "@/hooks/use-memory";
import type { Episode } from "@/api/types";

vi.mock("@/hooks/use-memory", () => ({
  useEpisodes: vi.fn(),
  useFacts: vi.fn(),
  useRules: vi.fn(),
}));

vi.mock("@/components/ui/tabs", () => ({
  Tabs: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  TabsList: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  TabsTrigger: ({ children }: { children: ReactNode }) => (
    <button type="button">{children}</button>
  ),
  TabsContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type UseEpisodesResult = ReturnType<typeof useEpisodes>;
type UseFactsResult = ReturnType<typeof useFacts>;
type UseRulesResult = ReturnType<typeof useRules>;

const EPISODE_CONTENT = "Owner mentioned fatigue again during the afternoon check-in.";

function makeEpisode(overrides: Partial<Episode> = {}): Episode {
  return {
    id: "episode-1",
    butler: "general",
    session_id: null,
    content: EPISODE_CONTENT,
    importance: 5,
    reference_count: 1,
    consolidated: false,
    consolidation_status: "pending",
    created_at: "2026-02-19T01:00:00Z",
    last_referenced_at: null,
    expires_at: null,
    metadata: {},
    ...overrides,
  };
}

function setMemoryQueryState(episodes: Episode[]) {
  vi.mocked(useFacts).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 20, has_more: false } },
  } as unknown as UseFactsResult);

  vi.mocked(useRules).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 20, has_more: false } },
  } as unknown as UseRulesResult);

  vi.mocked(useEpisodes).mockReturnValue({
    data: {
      data: episodes,
      meta: { total: episodes.length, offset: 0, limit: 50, has_more: false },
    },
  } as unknown as UseEpisodesResult);
}

describe("MemoryBrowser episodes", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  it("renders the daybook register for the episodes tab", () => {
    setMemoryQueryState([makeEpisode()]);

    act(() => {
      root.render(
        <MemoryRouter>
          <MemoryBrowser />
        </MemoryRouter>,
      );
    });

    // The daybook always renders content (CSS-clamped, not JS-hidden) and the
    // status filter pills, plus the ButlerMark — none of the legacy Expand
    // table chrome.
    expect(container.textContent).toContain(EPISODE_CONTENT);
    expect(container.textContent).toContain("dead letter");
    expect(container.querySelector('[role="button"][aria-expanded]')).not.toBeNull();
  });
});
