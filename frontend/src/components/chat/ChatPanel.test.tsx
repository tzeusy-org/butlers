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
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ChatContent } from "./ChatPanel";
import type { Message } from "@/api/types.ts";

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
  const content = () => (
    <QueryClientProvider client={queryClient}>
      <ChatContent butlerName="switchboard" />
    </QueryClientProvider>
  );
  const view = render(content());
  return {
    ...view,
    rerenderChatContent: () => view.rerender(content()),
  };
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

// ---------------------------------------------------------------------------
// Auto-resume / "New conversation" regression (bu-5gp95)
// ---------------------------------------------------------------------------

const EXISTING_CONVERSATIONS = [
  {
    id: "conv-1",
    butler_name: "switchboard",
    title: "Existing thread",
    status: "active",
    created_at: "2026-07-03T12:00:00.000Z",
    updated_at: "2026-07-04T12:00:00.000Z",
    message_count: 1,
    total_input_tokens: 5,
    total_output_tokens: 5,
    total_duration_ms: 200,
    routed_butler: null,
  },
];

function mockHooksWithConversations() {
  vi.mocked(useConversations).mockReturnValue({
    data: { data: EXISTING_CONVERSATIONS, meta: {} },
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

describe("ChatContent — resume / New-conversation lifecycle (bu-5gp95)", () => {
  it("auto-resumes the most recent conversation on initial mount", () => {
    mockHooksWithConversations();
    renderChatContent();

    // Appears twice: once in the ConversationList sidebar entry, once as the
    // ConversationHeader title — the header only shows the real title when
    // activeConversationId has actually been auto-resumed to conv-1.
    expect(screen.getAllByText("Existing thread")).toHaveLength(2);
    expect(screen.queryByText("New conversation")).toBeNull();
  });

  it("clicking New conversation stays on the fresh-thread state instead of snapping back", () => {
    mockHooksWithConversations();
    renderChatContent();

    // Auto-resumed to the existing thread first (sidebar entry + header title).
    expect(screen.getAllByText("Existing thread")).toHaveLength(2);

    fireEvent.click(screen.getByText("New"));

    // Without the one-shot guard, the auto-select effect re-fires (conversations
    // is still non-empty and activeConversationId is now null) and immediately
    // reselects conv-1 — regression under test. With the guard, the sidebar
    // still lists "Existing thread" (it's still a real conversation) but the
    // header must now read "New conversation", not snap back.
    expect(screen.getAllByText("Existing thread")).toHaveLength(1);
    expect(screen.getByText("New conversation")).toBeDefined();
  });

  it("re-resumes the new butler's conversation after a butlerName switch with no unmount in between", () => {
    // ChatPanel gates ChatContent on `{open && <ChatContent />}`, so it only
    // unmounts on Sheet close — NOT on a butler switch that leaves the Sheet
    // open (e.g. jumping butlers via the EntityFinder Cmd+K palette while
    // chatting; the header slot hosting ChatPanel survives Page's
    // loading/loaded transitions, see ui/page.tsx status-board archetype).
    // Simulate that here via `rerender` with a new `butlerName` prop on the
    // SAME ChatContent instance (no intervening unmount) and assert the
    // one-shot resume guard re-arms for the newly-viewed butler instead of
    // staying latched from the first butler.
    vi.mocked(useConversations).mockImplementation(
      (butlerName: string) =>
        ({
          data: {
            data: [
              {
                id: `conv-${butlerName}`,
                butler_name: butlerName,
                title: `${butlerName} thread`,
                status: "active",
                created_at: "2026-07-03T12:00:00.000Z",
                updated_at: "2026-07-04T12:00:00.000Z",
                message_count: 1,
                total_input_tokens: 5,
                total_output_tokens: 5,
                total_duration_ms: 200,
                routed_butler: null,
              },
            ],
            meta: {},
          },
          isLoading: false,
        }) as unknown as ReturnType<typeof useConversations>,
    );
    vi.mocked(useConversationMessages).mockReturnValue({
      data: { data: [], meta: {} },
      isLoading: false,
    } as unknown as ReturnType<typeof useConversationMessages>);
    vi.mocked(useConversationSearch).mockReturnValue({
      data: { data: [], meta: {} },
      isLoading: false,
    } as unknown as ReturnType<typeof useConversationSearch>);

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <ChatContent butlerName="finance" />
      </QueryClientProvider>,
    );

    // Auto-resumed to finance's thread (sidebar entry + header title).
    expect(screen.getAllByText("finance thread")).toHaveLength(2);

    rerender(
      <QueryClientProvider client={queryClient}>
        <ChatContent butlerName="calendar" />
      </QueryClientProvider>,
    );

    // Must re-resume to calendar's own thread (sidebar + header), not get
    // stuck on "New conversation" because the guard from the finance mount
    // never reset.
    expect(screen.getAllByText("calendar thread")).toHaveLength(2);
    expect(screen.queryByText("New conversation")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Retained thread during conversation-key refetch (bu-zu265)
// ---------------------------------------------------------------------------

const SWITCHING_CONVERSATIONS = [
  {
    id: "conv-current",
    butler_name: "switchboard",
    title: "Current thread",
    status: "active",
    created_at: "2026-07-03T12:00:00.000Z",
    updated_at: "2026-07-04T12:00:00.000Z",
    message_count: 1,
    total_input_tokens: 5,
    total_output_tokens: 5,
    total_duration_ms: 200,
    routed_butler: null,
  },
  {
    id: "conv-next",
    butler_name: "switchboard",
    title: "Next thread",
    status: "active",
    created_at: "2026-07-02T12:00:00.000Z",
    updated_at: "2026-07-03T12:00:00.000Z",
    message_count: 1,
    total_input_tokens: 5,
    total_output_tokens: 5,
    total_duration_ms: 200,
    routed_butler: null,
  },
];

const CURRENT_THREAD_MESSAGE: Message = {
  id: "current-thread-message",
  conversation_id: "conv-current",
  role: "user",
  content: "Retained while the next thread refetches",
  tool_calls: null,
  error: null,
  model: null,
  input_tokens: null,
  output_tokens: null,
  duration_ms: null,
  session_id: null,
  request_id: null,
  created_at: "2026-07-04T12:00:00.000Z",
};

const NEXT_THREAD_MESSAGE: Message = {
  ...CURRENT_THREAD_MESSAGE,
  id: "next-thread-message",
  conversation_id: "conv-next",
  content: "Rendered when the next thread arrives",
};

function mockHooksForConversationRefetchGap() {
  const conversationsResult = {
    data: { data: SWITCHING_CONVERSATIONS, meta: {} },
    isLoading: false,
  } as unknown as ReturnType<typeof useConversations>;
  const emptyMessagesResult = {
    data: { data: [], meta: {} },
    isLoading: false,
  } as unknown as ReturnType<typeof useConversationMessages>;
  const currentMessagesResult = {
    data: { data: [CURRENT_THREAD_MESSAGE], meta: {} },
    isLoading: false,
  } as unknown as ReturnType<typeof useConversationMessages>;
  let nextMessagesResult = {
    data: undefined,
    isLoading: true,
  } as unknown as ReturnType<typeof useConversationMessages>;

  vi.mocked(useConversations).mockReturnValue(conversationsResult);
  vi.mocked(useConversationMessages).mockImplementation(
    (_butlerName: string, conversationId: string | null) => {
      if (conversationId === "conv-current") return currentMessagesResult;
      if (conversationId === "conv-next") return nextMessagesResult;
      return emptyMessagesResult;
    },
  );
  vi.mocked(useConversationSearch).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
  } as unknown as ReturnType<typeof useConversationSearch>);

  return {
    resolveNextThread() {
      nextMessagesResult = {
        data: { data: [NEXT_THREAD_MESSAGE], meta: {} },
        isLoading: false,
      } as unknown as ReturnType<typeof useConversationMessages>;
    },
  };
}

describe("ChatContent — conversation switch refetch floor (bu-zu265)", () => {
  it("keeps the current thread visible while loading, then synchronizes the next thread", async () => {
    const { resolveNextThread } = mockHooksForConversationRefetchGap();
    const view = renderChatContent();

    expect(screen.getByText("Retained while the next thread refetches")).toBeDefined();

    fireEvent.click(screen.getByText("Next thread"));

    // The conversation selection changes immediately and TanStack Query's
    // pending result has no data. Retain the rendered thread instead of
    // replacing it with a loading skeleton during that gap.
    expect(screen.getAllByText("Next thread")).toHaveLength(2);
    expect(screen.getByText("Retained while the next thread refetches")).toBeDefined();
    expect(screen.queryByText("No messages yet. Start the conversation below.")).toBeNull();

    resolveNextThread();
    view.rerenderChatContent();

    await waitFor(() => expect(screen.getByText("Rendered when the next thread arrives")).toBeDefined());
    expect(screen.queryByText("Retained while the next thread refetches")).toBeNull();
  });
});

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

// ---------------------------------------------------------------------------
// Send-error classification parity with FloatingChatWidget (bu-o0ab2)
// ---------------------------------------------------------------------------

describe("ChatContent — send-error classification", () => {
  it("uses doctrine-compliant copy for a generic transport failure", async () => {
    createConversationMock.mockRejectedValue(new Error("network unavailable"));

    renderChatContent();

    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "hello" } });
    fireEvent.click(screen.getByTitle("Send message"));

    await waitFor(() => {
      expect(screen.getByTestId("chat-widget-error-banner").textContent).toContain(
        "Failed to send message.",
      );
    });

    const banner = screen.getByTestId("chat-widget-error-banner");
    expect(banner.textContent).not.toContain("Please try again.");
    expect(screen.getByRole("button", { name: "Retry" })).toBeDefined();
  });

  it("keeps a failed optimistic message visible and retryable through an empty server sync", async () => {
    sendMessageMock.mockResolvedValue({ ok: true } as Response);
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    // The create event changes the active conversation id. Return a distinct,
    // stable empty result for that conversation so the real sync effect runs
    // after the SSE error (rather than accidentally reusing the initial
    // no-conversation result object from this mock).
    const noConversationMessages = { data: { data: [], meta: {} }, isLoading: false };
    const emptyConversationMessages = { data: { data: [], meta: {} }, isLoading: false };
    vi.mocked(useConversationMessages).mockImplementation(
      (_butlerName: string, conversationId: string | null) =>
        (conversationId === "conv-retry-1"
          ? emptyConversationMessages
          : noConversationMessages) as unknown as ReturnType<typeof useConversationMessages>,
    );
    scriptedEvents = [
      { event: "conversation_created", data: { conversation_id: "conv-retry-1", title: null } },
      {
        event: "error",
        data: { code: "SWITCHBOARD_UNAVAILABLE", message: "Switchboard offline — retry" },
      },
      { event: "done", data: {} },
    ];

    renderChatContent();

    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "hello switchboard" } });

    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    expect(screen.getByTestId("chat-widget-error-banner")).toBeDefined();
    expect(screen.getByTestId("chat-widget-error-banner").textContent).toContain(
      "Switchboard offline",
    );
    // No inert assistant-bubble error message rendered alongside the banner.
    expect(screen.queryByText("Unknown error")).toBeNull();

    // The error transition clears `streaming`, which permits the message-query
    // sync effect to run. A stale/empty server response must not erase the
    // uncommitted optimistic user bubble before the owner can act on it.
    expect(screen.getAllByText("hello switchboard")).toHaveLength(1);
    const retryButton = screen.getByRole("button", { name: "Retry" });
    expect((retryButton as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByTestId("chat-widget-error-banner").getAttribute("role")).toBe("alert");

    const firstPayload = createConversationMock.mock.calls[0][1] as {
      message_id: string;
    };
    // Retry uses the persisted conversation and exact same client message ID.
    sendMessageMock.mockClear();
    scriptedEvents = [{ event: "done", data: {} }];
    await act(async () => {
      fireEvent.click(retryButton);
    });
    expect(sendMessageMock).toHaveBeenCalledTimes(1);
    expect(sendMessageMock.mock.calls[0][1]).toBe("conv-retry-1");
    expect(sendMessageMock.mock.calls[0][2]).toEqual({
      message: "hello switchboard",
      message_id: firstPayload.message_id,
    });
    // A retry is the same logical message, so it must retain the first
    // optimistic bubble rather than append a duplicate alongside it.
    expect(screen.getAllByText("hello switchboard")).toHaveLength(1);
  });

  it("shows an inspect-session banner with a session link on SESSION_TIMEOUT", async () => {
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [
      {
        event: "error",
        data: {
          code: "SESSION_TIMEOUT",
          message: "No reply yet — inspect the session for details.",
          session_id: "session-abc-123",
        },
      },
      { event: "done", data: {} },
    ];

    renderChatContent();

    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "report a bug" } });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    const banner = screen.getByTestId("chat-widget-timeout-banner");
    expect(banner.textContent).toContain("No reply yet");
    const link = screen.getByTestId(
      "chat-widget-timeout-session-link",
    ) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/sessions/session-abc-123");
  });

  it("dismissing the offline banner clears it without resending", async () => {
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [
      { event: "error", data: { code: "SWITCHBOARD_UNAVAILABLE", message: "offline" } },
      { event: "done", data: {} },
    ];

    renderChatContent();

    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "hello" } });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    expect(screen.getByTestId("chat-widget-error-banner")).toBeDefined();
    createConversationMock.mockClear();
    fireEvent.click(screen.getByLabelText("Dismiss"));
    expect(screen.queryByTestId("chat-widget-error-banner")).toBeNull();
    expect(createConversationMock).not.toHaveBeenCalled();
  });
});
