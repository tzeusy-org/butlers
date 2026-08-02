// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ConversationList } from "./ConversationList.tsx";

vi.mock("@/hooks/use-conversations.ts", () => ({
  useConversations: vi.fn(),
  useConversationSearch: vi.fn(),
}));

import { useConversations, useConversationSearch } from "@/hooks/use-conversations.ts";

function renderConversationList() {
  return render(
    <ConversationList
      butlerName="switchboard"
      activeConversationId={null}
      onSelectConversation={vi.fn()}
      onNewConversation={vi.fn()}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  vi.mocked(useConversationSearch).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useConversationSearch>);
});

afterEach(() => cleanup());

describe("ConversationList — read recovery", () => {
  it("surfaces a list read error with an explicit retry instead of an empty-state lie", () => {
    const refetchConversations = vi.fn();
    vi.mocked(useConversations).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: refetchConversations,
    } as unknown as ReturnType<typeof useConversations>);

    renderConversationList();

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Could not load conversations.");
    expect(screen.queryByText("No conversations yet.")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetchConversations).toHaveBeenCalledTimes(1);
  });

  it("keeps cached list rows visible alongside a recoverable list error", () => {
    const refetchConversations = vi.fn();
    vi.mocked(useConversations).mockReturnValue({
      data: {
        data: [
          {
            id: "conversation-cached",
            title: "Cached conversation",
            updated_at: "2026-08-02T00:00:00Z",
            routed_butler: null,
          },
        ],
        meta: {},
      },
      isLoading: false,
      isError: true,
      refetch: refetchConversations,
    } as unknown as ReturnType<typeof useConversations>);

    renderConversationList();

    expect(screen.getByText("Cached conversation")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("Could not load conversations.");

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetchConversations).toHaveBeenCalledTimes(1);
  });

  it("keeps cached search rows visible alongside a recoverable search error", () => {
    vi.useFakeTimers();
    const refetchSearch = vi.fn();
    vi.mocked(useConversations).mockReturnValue({
      data: { data: [], meta: {} },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useConversations>);
    vi.mocked(useConversationSearch).mockImplementation((_, query) => {
      if (query === "cached") {
        return {
          data: {
            data: [
              {
                id: "conversation-search-cached",
                title: "Cached search result",
                updated_at: "2026-08-02T00:00:00Z",
                routed_butler: null,
              },
            ],
            meta: {},
          },
          isLoading: false,
          isError: true,
          refetch: refetchSearch,
        } as unknown as ReturnType<typeof useConversationSearch>;
      }

      return {
        data: { data: [], meta: {} },
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      } as unknown as ReturnType<typeof useConversationSearch>;
    });

    try {
      renderConversationList();
      fireEvent.change(screen.getByPlaceholderText("Search..."), {
        target: { value: "cached" },
      });
      act(() => vi.advanceTimersByTime(300));

      expect(screen.getByText("Cached search result")).toBeTruthy();
      expect(screen.getByRole("alert").textContent).toContain(
        "Could not load conversation search results.",
      );

      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(refetchSearch).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
