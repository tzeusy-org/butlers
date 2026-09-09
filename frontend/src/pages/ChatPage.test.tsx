// @vitest-environment jsdom
/**
 * ChatPage — full-page chat posture (bu-0ynlk.11).
 *
 * Acceptance criterion #3: /chat and /chat/:conversationId resolve to
 * ChatPage; unknown id renders not-found; #m-{id} focuses the message
 * element (scrollIntoView spy); copy-link URL format is covered by
 * message-id.test.ts and MessageThread.test.tsx.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";

import { ApiError } from "@/api/client";
import type { ConversationSummary, Message } from "@/api/types";

vi.mock("@/hooks/use-conversations.ts", () => ({
  useConversations: vi.fn(),
  useConversationById: vi.fn(),
  useConversationMessages: vi.fn(),
}));
vi.mock("@/hooks/use-pricing-map.ts", () => ({ usePricingMap: vi.fn() }));
vi.mock("@/hooks/use-conversation-turn.ts", () => ({ useConversationTurn: vi.fn() }));

import ChatPage from "./ChatPage";
import {
  useConversations,
  useConversationById,
  useConversationMessages,
} from "@/hooks/use-conversations.ts";
import { usePricingMap } from "@/hooks/use-pricing-map.ts";
import { useConversationTurn } from "@/hooks/use-conversation-turn.ts";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

function makeConversation(overrides: Partial<ConversationSummary> = {}): ConversationSummary {
  return {
    id: "conversation-1",
    butler_name: "switchboard",
    title: "Landlord follow-up",
    status: "active",
    created_at: "2026-09-05T00:00:00Z",
    updated_at: "2026-09-05T00:00:00Z",
    message_count: 1,
    ...overrides,
  };
}

function makeMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: "message-1",
    conversation_id: "conversation-1",
    role: "assistant",
    content: "Recorded.",
    tool_calls: null,
    error: null,
    model: null,
    input_tokens: null,
    output_tokens: null,
    duration_ms: null,
    session_id: null,
    request_id: null,
    created_at: "2026-09-05T00:00:00Z",
    ...overrides,
  };
}

function mockTurn(visibleMessages: Message[] = []) {
  vi.mocked(useConversationTurn).mockReturnValue({
    streaming: null,
    visibleMessages,
    sendError: null,
    setSendError: vi.fn(),
    isStreaming: false,
    hasActiveRuntime: false,
    contextPreview: { policy: "snapshot", label: "Chat", context: null },
    includeContext: true,
    toggleIncludeContext: vi.fn(),
    sendText: vi.fn(),
    handleStop: vi.fn(),
    abandonCurrentStream: vi.fn(),
    resetTurnState: vi.fn(),
  } as AnyMock);
}

function renderPage(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/chat/:conversationId" element={<ChatPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ChatPage — route resolution (bu-0ynlk.11)", () => {
  it("resolves /chat to the composer with no conversation loaded", () => {
    vi.mocked(useConversations).mockReturnValue({ data: { data: [] } } as AnyMock);
    vi.mocked(useConversationById).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);
    vi.mocked(useConversationMessages).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as AnyMock);
    vi.mocked(usePricingMap).mockReturnValue({ data: null } as AnyMock);
    mockTurn([]);

    renderPage("/chat");

    expect(screen.getByTestId("chat-page")).toBeTruthy();
  });

  it("resolves /chat/:conversationId, fetching the cross-butler detail for that id", () => {
    vi.mocked(useConversations).mockReturnValue({ data: { data: [] } } as AnyMock);
    vi.mocked(useConversationById).mockReturnValue({
      data: makeConversation(),
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);
    vi.mocked(useConversationMessages).mockReturnValue({
      data: { data: [makeMessage()] },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as AnyMock);
    vi.mocked(usePricingMap).mockReturnValue({ data: null } as AnyMock);
    mockTurn([makeMessage()]);

    renderPage("/chat/conversation-1");

    expect(screen.getByTestId("chat-page")).toBeTruthy();
    expect(vi.mocked(useConversationById)).toHaveBeenCalledWith("conversation-1");
  });

  it("renders a not-found state for an unknown conversation id, not a blank thread", () => {
    vi.mocked(useConversations).mockReturnValue({ data: { data: [] } } as AnyMock);
    vi.mocked(useConversationById).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError("UNKNOWN_ERROR", "Conversation not found.", 404),
    } as AnyMock);
    vi.mocked(useConversationMessages).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as AnyMock);
    vi.mocked(usePricingMap).mockReturnValue({ data: null } as AnyMock);
    mockTurn([]);

    renderPage("/chat/does-not-exist");

    expect(screen.getByTestId("chat-page-not-found")).toBeTruthy();
    expect(screen.getByText("Conversation not found")).toBeTruthy();
    expect(screen.queryByTestId("chat-page")).toBeNull();
  });

  it("names the degraded recent-conversations source instead of rendering an empty sidebar", () => {
    vi.mocked(useConversations).mockReturnValue({
      data: undefined,
      isError: true,
    } as AnyMock);
    vi.mocked(useConversationById).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);
    vi.mocked(useConversationMessages).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as AnyMock);
    vi.mocked(usePricingMap).mockReturnValue({ data: null } as AnyMock);
    mockTurn([]);

    renderPage("/chat");

    expect(screen.getByTestId("chat-page-recent-degraded")).toBeTruthy();
  });
});

describe("ChatPage — #m-{id} anchor scroll (bu-0ynlk.11)", () => {
  it("scrolls to and focuses the message named by the URL fragment once messages have loaded", () => {
    vi.mocked(useConversations).mockReturnValue({ data: { data: [] } } as AnyMock);
    vi.mocked(useConversationById).mockReturnValue({
      data: makeConversation(),
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);
    const message = makeMessage({ id: "message-42" });
    vi.mocked(useConversationMessages).mockReturnValue({
      data: { data: [message] },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as AnyMock);
    vi.mocked(usePricingMap).mockReturnValue({ data: null } as AnyMock);
    mockTurn([message]);

    const scrollIntoViewSpy = vi.fn();
    // jsdom does not implement scrollIntoView/focus by default.
    HTMLElement.prototype.scrollIntoView = scrollIntoViewSpy;

    renderPage("/chat/conversation-1#m-message-42");

    const bubble = document.getElementById("m-message-42") as HTMLElement;
    expect(bubble).toBeTruthy();
    expect(scrollIntoViewSpy).toHaveBeenCalled();
    expect(document.activeElement).toBe(bubble);
  });

  it("loads the thread without scrolling when the fragment names a message that isn't there", () => {
    vi.mocked(useConversations).mockReturnValue({ data: { data: [] } } as AnyMock);
    vi.mocked(useConversationById).mockReturnValue({
      data: makeConversation(),
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);
    const message = makeMessage({ id: "message-1" });
    vi.mocked(useConversationMessages).mockReturnValue({
      data: { data: [message] },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as AnyMock);
    vi.mocked(usePricingMap).mockReturnValue({ data: null } as AnyMock);
    mockTurn([message]);

    const scrollIntoViewSpy = vi.fn();
    HTMLElement.prototype.scrollIntoView = scrollIntoViewSpy;

    renderPage("/chat/conversation-1#m-missing-message");

    expect(screen.getByTestId("chat-page")).toBeTruthy();
    // MessageThread's own auto-scroll-to-bottom (`{ behavior: "smooth" }`,
    // unrelated to the anchor feature) is expected to fire; the anchor-scroll
    // signature (`block: "center"`, see scrollToMessageAnchor) must not.
    expect(scrollIntoViewSpy).not.toHaveBeenCalledWith(
      expect.objectContaining({ block: "center" }),
    );
  });
});
