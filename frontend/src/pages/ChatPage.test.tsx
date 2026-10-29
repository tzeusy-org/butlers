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
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";

import { ApiError } from "@/api/client";
import type { ConversationSummary, Message } from "@/api/types";

vi.mock("@/hooks/use-conversations.ts", () => ({
  useConversations: vi.fn(),
  useConversationById: vi.fn(),
  useConversationMessages: vi.fn(),
}));
vi.mock("@/hooks/use-pricing-map.ts", () => ({ usePricingMap: vi.fn() }));
vi.mock("@/hooks/use-conversation-turn.ts", () => ({
  useConversationTurn: vi.fn(),
}));

import ChatPage from "./ChatPage";
import {
  useConversations,
  useConversationById,
  useConversationMessages,
} from "@/hooks/use-conversations.ts";
import { usePricingMap } from "@/hooks/use-pricing-map.ts";
import { useConversationTurn } from "@/hooks/use-conversation-turn.ts";
import { WIDGET_BUTLER } from "@/components/chat/chat-constants.ts";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

function makeConversation(
  overrides: Partial<ConversationSummary> = {},
): ConversationSummary {
  return {
routed_butler: null,
latest_assistant_reply_at: null,
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
model_name: null,
sources: [],
citations: [],
routed_butler: null,
page_context: null,
captured_at: null,
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
    vi.mocked(useConversations).mockReturnValue({
      data: { data: [] },
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

    expect(screen.getByTestId("chat-page")).toBeTruthy();
  });

  it("resolves /chat/:conversationId, fetching the cross-butler detail for that id", () => {
    vi.mocked(useConversations).mockReturnValue({
      data: { data: [] },
    } as AnyMock);
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
    expect(vi.mocked(useConversationById)).toHaveBeenCalledWith(
      "conversation-1",
    );
  });

  it("renders a not-found state for an unknown conversation id, not a blank thread", () => {
    vi.mocked(useConversations).mockReturnValue({
      data: { data: [] },
    } as AnyMock);
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
    vi.mocked(useConversations).mockReturnValue({
      data: { data: [] },
    } as AnyMock);
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
    vi.mocked(useConversations).mockReturnValue({
      data: { data: [] },
    } as AnyMock);
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

describe("ChatPage — owner gate before send (bu-p5umi2.1)", () => {
  const OWNER = "relationship";

  function mockSurroundings(
    sendText = vi.fn(),
    overrides: Record<string, unknown> = {},
  ) {
    vi.mocked(useConversations).mockReturnValue({
      data: { data: [] },
    } as AnyMock);
    vi.mocked(useConversationMessages).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as AnyMock);
    vi.mocked(usePricingMap).mockReturnValue({ data: null } as AnyMock);
    const handleStop = vi.fn();
    vi.mocked(useConversationTurn).mockReturnValue({
      streaming: null,
      visibleMessages: [],
      sendError: null,
      setSendError: vi.fn(),
      isStreaming: false,
      hasActiveRuntime: false,
      contextPreview: { policy: "snapshot", label: "Chat", context: null },
      includeContext: true,
      toggleIncludeContext: vi.fn(),
      sendText,
      handleStop,
      abandonCurrentStream: vi.fn(),
      resetTurnState: vi.fn(),
      ...overrides,
    } as AnyMock);
    return { sendText, handleStop };
  }

  function routes() {
    return (
      <MemoryRouter initialEntries={["/chat/conversation-1"]}>
        <Routes>
          <Route path="/chat/:conversationId" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>
    );
  }

  it("names an ownership lookup failure, keeps typed text and retries only the lookup", () => {
    const { sendText } = mockSurroundings();
    const refetch = vi.fn();
    const resolved = {
      data: makeConversation({ butler_name: OWNER }),
      isLoading: false,
      isError: false,
      error: null,
      refetch,
    };
    const failed = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError("UNKNOWN_ERROR", "boom", 500),
      refetch,
    };
    vi.mocked(useConversationById).mockReturnValue(resolved as AnyMock);

    const view = renderPage("/chat/conversation-1");
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "draft text" },
    });

    // The lookup result is lost (cache reset): ownership is no longer known.
    vi.mocked(useConversationById).mockReturnValue(failed as AnyMock);
    view.rerender(routes());

    expect(screen.getByTestId("chat-page-owner-unresolved")).toBeTruthy();
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
      "draft text",
    );
    expect(
      (screen.getByLabelText("Send message") as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(vi.mocked(useConversationMessages).mock.calls.at(-1)?.[0]).toBe("");

    fireEvent.click(screen.getByText("Retry"));
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(sendText).not.toHaveBeenCalled();
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
      "draft text",
    );

    vi.mocked(useConversationById).mockReturnValue(resolved as AnyMock);
    view.rerender(routes());

    expect(screen.queryByTestId("chat-page-owner-unresolved")).toBeNull();
    expect(vi.mocked(useConversationMessages)).toHaveBeenLastCalledWith(
      OWNER,
      "conversation-1",
    );
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
      "draft text",
    );
    fireEvent.click(screen.getByLabelText("Send message"));
    expect(sendText).toHaveBeenCalledWith("draft text");
  });

  it("keeps the established owner and Stop when a background lookup refetch fails", () => {
    const { handleStop } = mockSurroundings(vi.fn(), {
      hasActiveRuntime: true,
    });
    vi.mocked(useConversationById).mockReturnValue({
      data: makeConversation({ butler_name: OWNER }),
      isLoading: false,
      isError: true,
      error: new ApiError("UNKNOWN_ERROR", "boom", 500),
      refetch: vi.fn(),
    } as AnyMock);

    renderPage("/chat/conversation-1");

    expect(screen.getByTestId("chat-page-owner-stale")).toBeTruthy();
    expect(screen.queryByTestId("chat-page-owner-unresolved")).toBeNull();
    expect(vi.mocked(useConversationMessages)).toHaveBeenLastCalledWith(
      OWNER,
      "conversation-1",
    );
    fireEvent.click(screen.getByTestId("chat-stop-button"));
    expect(handleStop).toHaveBeenCalledTimes(1);
  });

  it("does not let an earlier route's lookup result bind or unlock a newer route", () => {
    const { sendText } = mockSurroundings();
    vi.mocked(useConversationById).mockReturnValue({
      data: makeConversation({ id: "conversation-1", butler_name: OWNER }),
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);

    renderPage("/chat/conversation-2");

    expect((screen.getByRole("textbox") as HTMLTextAreaElement).disabled).toBe(
      true,
    );
    for (const [name] of vi.mocked(useConversationMessages).mock.calls) {
      expect(name).toBe("");
    }
    for (const [options] of vi.mocked(useConversationTurn).mock.calls) {
      expect(options.butlerName).toBe(WIDGET_BUTLER);
    }
    expect(sendText).not.toHaveBeenCalled();
  });

  it("keeps bare /chat send-capable through Switchboard", () => {
    const { sendText } = mockSurroundings();
    vi.mocked(useConversationById).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);

    renderPage("/chat");
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "hello" },
    });
    fireEvent.click(screen.getByLabelText("Send message"));

    expect(sendText).toHaveBeenCalledWith("hello");
    expect(
      vi.mocked(useConversationTurn).mock.calls.at(-1)?.[0].butlerName,
    ).toBe(WIDGET_BUTLER);
    expect(screen.queryByTestId("chat-page-owner-unresolved")).toBeNull();
  });

  it("does not replay a failed send from the banner while the owner is unresolved", () => {
    const { sendText } = mockSurroundings(vi.fn(), {
      sendError: {
        kind: "generic",
        message: "Send failed",
        failedText: "earlier text",
        messageId: "message-9",
      },
    });
    vi.mocked(useConversationById).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as AnyMock);

    renderPage("/chat/conversation-1");
    fireEvent.click(
      within(screen.getByTestId("chat-widget-error-banner")).getByText("Retry"),
    );

    expect(sendText).not.toHaveBeenCalled();
  });
});
