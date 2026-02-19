// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";

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

const LONG_EPISODE_CONTENT =
  "This is an intentionally long episode body that should only appear in full after expanding the row for detailed reading.";

function setMemoryQueryState(episodes: Episode[]) {
  vi.mocked(useFacts).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 20, has_more: false } },
    isLoading: false,
  } as unknown as UseFactsResult);

  vi.mocked(useRules).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 20, has_more: false } },
    isLoading: false,
  } as unknown as UseRulesResult);

  vi.mocked(useEpisodes).mockReturnValue({
    data: {
      data: episodes,
      meta: { total: episodes.length, offset: 0, limit: 20, has_more: false },
    },
    isLoading: false,
  } as unknown as UseEpisodesResult);
}

function findButtonByText(container: HTMLElement, text: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll("button")).find(
    (button): button is HTMLButtonElement => button.textContent?.includes(text) ?? false,
  );
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

  it("expands and collapses episode content", () => {
    setMemoryQueryState([
      {
        id: "episode-1",
        butler: "general",
        session_id: null,
        content: LONG_EPISODE_CONTENT,
        importance: 0.8,
        reference_count: 1,
        consolidated: false,
        created_at: "2026-02-19T01:00:00Z",
        last_referenced_at: null,
        expires_at: null,
        metadata: {},
      },
    ]);

    act(() => {
      root.render(<MemoryBrowser />);
    });

    expect(container.textContent).not.toContain(LONG_EPISODE_CONTENT);

    const expandButton = findButtonByText(container, "Expand");
    expect(expandButton).toBeDefined();

    act(() => {
      expandButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(container.textContent).toContain(LONG_EPISODE_CONTENT);

    const collapseButton = findButtonByText(container, "Collapse");
    expect(collapseButton).toBeDefined();

    act(() => {
      collapseButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(container.textContent).not.toContain(LONG_EPISODE_CONTENT);
  });
});
