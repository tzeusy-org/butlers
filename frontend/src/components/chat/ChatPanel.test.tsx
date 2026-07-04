// @vitest-environment jsdom
/**
 * ChatPanel — regression test for bu-8k9n7.
 *
 * Backend's `conversation_created` SSE event carries `{conversation_id,
 * title}` (see src/butlers/api/routers/conversations.py
 * _stream_conversation_response), but handleSend previously read `data.id`
 * (always undefined), so the butler-detail ChatPanel's create-new-conversation
 * flow never captured the new conversation id. FloatingChatWidget already
 * read `data.conversation_id` correctly; this test asserts ChatContent now
 * matches.
 *
 * Tests target `ChatContent` directly (not the `ChatPanel` Sheet wrapper) —
 * same convention as EpisodeDrawerContent in EpisodeDrawer.test.tsx — since
 * only butlerName is needed to exercise the send flow.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ChatContent } from "./ChatPanel";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-conversations.ts", () => ({
  conversationKeys: {
    all: (butlerName: string) => ["conversations", butlerName],
    messages: (butlerName: string, id: string) => ["conversation-messages", butlerName, id],
  },
  useConversations: vi.fn(),
  useConversationMessages: vi.fn(),
  useConversationSearch: vi.fn(),
}));

vi.mock("@/api/client.ts", () => ({
  fetchPricingMap: vi.fn().mockResolvedValue({ data: {} }),
}));

const createConversationMock = vi.fn();
const sendMessageMock = vi.fn();
vi.mock("@/api/index.ts", () => ({
  createConversation: (...args: unknown[]) => createConversationMock(...args),
  sendMessage: (...args: unknown[]) => sendMessageMock(...args),
}));

// consumeSseStream is mocked to synchronously replay a scripted event queue,
// bypassing real stream parsing entirely — same approach as
// FloatingChatWidget.test.tsx.
let scriptedEvents: Array<{ event: string; data: unknown }> = [];
vi.mock("./sse-utils.ts", () => ({
  consumeSseStream: async (
    _response: Response,
    onEvent: (event: { event: string; data: unknown }) => void,
  ) => {
    for (const evt of scriptedEvents) onEvent(evt);
  },
}));

import { useConversations, useConversationMessages, useConversationSearch } from "@/hooks/use-conversations.ts";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function mockHooksEmpty() {
  vi.mocked(useConversations).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
  } as unknown as ReturnType<typeof useConversations>);

  vi.mocked(useConversationMessages).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
  } as unknown as ReturnType<typeof useConversationMessages>);

  vi.mocked(useConversationSearch).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
  } as unknown as ReturnType<typeof useConversationSearch>);
}

function renderChatContent() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ChatContent butlerName="switchboard" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  scriptedEvents = [];
  mockHooksEmpty();
  window.localStorage.clear();
});

afterEach(() => cleanup());

// ---------------------------------------------------------------------------
// conversation_created SSE handling
// ---------------------------------------------------------------------------

describe("ChatContent — conversation_created SSE handling", () => {
  it("captures the new conversation id from `conversation_id` (not `id`) on the create flow", async () => {
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [
      { event: "conversation_created", data: { conversation_id: "conv-new-1", title: null } },
      { event: "done", data: {} },
    ];

    renderChatContent();

    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "hello butler" } });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    // The optimistic user message should have been re-tagged with the real
    // conversation id surfaced by the conversation_created event, proving the
    // handler read `conversation_id` rather than the nonexistent `id` field.
    expect(screen.getByText("hello butler")).toBeDefined();
    expect(createConversationMock).toHaveBeenCalledTimes(1);

    // Sending a follow-up message must now go through sendMessage scoped to
    // the captured conversation id — this only happens if
    // activeConversationId was actually set from the event.
    sendMessageMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [{ event: "done", data: {} }];
    fireEvent.change(input, { target: { value: "follow up" } });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    expect(sendMessageMock).toHaveBeenCalledTimes(1);
    expect(sendMessageMock.mock.calls[0][1]).toBe("conv-new-1");
  });
});
