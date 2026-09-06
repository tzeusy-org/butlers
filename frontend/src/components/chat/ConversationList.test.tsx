// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ConversationList } from "./ConversationList.tsx";

vi.mock("@/hooks/use-conversations.ts", () => ({
  useConversations: vi.fn(),
  useConversationSearch: vi.fn(),
  useMessageSearch: vi.fn(),
}));

import {
  useConversations,
  useConversationSearch,
  useMessageSearch,
} from "@/hooks/use-conversations.ts";

function renderConversationList(onSelectConversation = vi.fn()) {
  return render(
    <ConversationList
      butlerName="switchboard"
      activeConversationId={null}
      onSelectConversation={onSelectConversation}
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
  vi.mocked(useMessageSearch).mockReturnValue({
    data: { data: [], meta: { next_cursor: null, has_more: false } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useMessageSearch>);
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

describe("ConversationList — message search (bu-0ynlk.9)", () => {
  it("renders snippet rows with a butler mark for an active query", () => {
    vi.useFakeTimers();
    vi.mocked(useMessageSearch).mockReturnValue({
      data: {
        data: [
          {
            message_id: "msg-1",
            conversation_id: "conv-1",
            role: "user",
            created_at: "2026-08-02T00:00:00Z",
            butler_name: "home",
            session_id: null,
            snippet: "call the landlord today",
            highlight_ranges: [[9, 17]],
            deep_link: "/butlers/home",
          },
        ],
        meta: { next_cursor: null, has_more: false },
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMessageSearch>);

    try {
      renderConversationList();
      fireEvent.change(screen.getByPlaceholderText("Search..."), {
        target: { value: "landlord" },
      });
      act(() => vi.advanceTimersByTime(300));

      const row = screen.getByTestId("message-search-result");
      expect(row.textContent).toContain("call the landlord today");
      // Highlighted match is wrapped in a <mark>.
      const mark = row.querySelector("mark");
      expect(mark?.textContent).toBe("landlord");
      // ButlerMark renders the butler's initial letter.
      expect(row.textContent).toContain("H");
    } finally {
      vi.useRealTimers();
    }
  });

  it("jump-to-message calls onSelectConversation with the message id for a same-butler hit", () => {
    vi.useFakeTimers();
    vi.mocked(useMessageSearch).mockReturnValue({
      data: {
        data: [
          {
            message_id: "msg-1",
            conversation_id: "conv-1",
            role: "user",
            created_at: "2026-08-02T00:00:00Z",
            butler_name: "switchboard",
            session_id: null,
            snippet: "call the landlord today",
            highlight_ranges: [[9, 17]],
            deep_link: "/butlers/switchboard",
          },
        ],
        meta: { next_cursor: null, has_more: false },
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMessageSearch>);

    const onSelectConversation = vi.fn();
    try {
      renderConversationList(onSelectConversation);
      fireEvent.change(screen.getByPlaceholderText("Search..."), {
        target: { value: "landlord" },
      });
      act(() => vi.advanceTimersByTime(300));

      fireEvent.click(screen.getByTestId("message-search-result"));

      expect(onSelectConversation).toHaveBeenCalledWith("conv-1", "msg-1");
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders a cross-butler hit as an external deep_link instead of an in-panel jump", () => {
    vi.useFakeTimers();
    vi.mocked(useMessageSearch).mockReturnValue({
      data: {
        data: [
          {
            message_id: "msg-2",
            conversation_id: "conv-2",
            role: "assistant",
            created_at: "2026-08-02T00:00:00Z",
            butler_name: "finance",
            session_id: "sess-9",
            snippet: "budget review",
            highlight_ranges: [],
            deep_link: "/sessions/sess-9",
          },
        ],
        meta: { next_cursor: null, has_more: false },
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMessageSearch>);

    try {
      renderConversationList();
      fireEvent.change(screen.getByPlaceholderText("Search..."), {
        target: { value: "budget" },
      });
      act(() => vi.advanceTimersByTime(300));

      const row = screen.getByTestId("message-search-result");
      expect(row.tagName).toBe("A");
      expect(row.getAttribute("href")).toBe("/sessions/sess-9");
      expect(row.getAttribute("target")).toBe("_blank");
    } finally {
      vi.useRealTimers();
    }
  });

  it("surfaces a message search read error with retry instead of a silent empty section", () => {
    vi.useFakeTimers();
    const refetchMessageSearch = vi.fn();
    vi.mocked(useMessageSearch).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: refetchMessageSearch,
    } as unknown as ReturnType<typeof useMessageSearch>);

    try {
      renderConversationList();
      fireEvent.change(screen.getByPlaceholderText("Search..."), {
        target: { value: "landlord" },
      });
      act(() => vi.advanceTimersByTime(300));

      expect(screen.queryByTestId("message-search-result")).toBeNull();
      const alerts = screen.getAllByRole("alert");
      const messageSearchAlert = alerts.find((el) =>
        el.textContent?.includes("Could not load message search results."),
      );
      expect(messageSearchAlert).toBeTruthy();

      fireEvent.click(
        messageSearchAlert!.querySelector("button") as HTMLButtonElement,
      );
      expect(refetchMessageSearch).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
