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
import { MemoryRouter } from "react-router";

import { ChatContent } from "./ChatPanel";
import { PageContextProvider } from "@/lib/page-context.tsx";
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
  useMessageSearch: vi.fn(() => ({
    data: { data: [], meta: { next_cursor: null, has_more: false } },
    isLoading: false,
    isError: false,
  })),
}));

vi.mock("@/api/client.ts", () => ({
  fetchPricingMap: vi.fn().mockResolvedValue({ data: {} }),
}));

const createConversationMock = vi.fn();
const sendMessageMock = vi.fn();
const cancelConversationMessageTurnMock = vi.fn();
vi.mock("@/api/index.ts", () => ({
  createConversation: (...args: unknown[]) => createConversationMock(...args),
  sendMessage: (...args: unknown[]) => sendMessageMock(...args),
  cancelConversationMessageTurn: (...args: unknown[]) =>
    cancelConversationMessageTurnMock(...args),
}));

// consumeSseStream is mocked to synchronously replay a scripted event queue,
// bypassing real stream parsing entirely — same approach as
// FloatingChatWidget.test.tsx.
let scriptedEvents: Array<{ event: string; data: unknown }> = [];
let activeSseEventHandler: ((event: { event: string; data: unknown }) => void) | null = null;
vi.mock("./sse-utils.ts", () => ({
  consumeSseStream: async (
    _response: Response,
    onEvent: (event: { event: string; data: unknown }) => void,
  ) => {
    activeSseEventHandler = onEvent;
    for (const evt of scriptedEvents) onEvent(evt);
  },
}));

import {
  useConversations,
  useConversationMessages,
  useConversationSearch,
} from "@/hooks/use-conversations.ts";

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

function renderChatContent(initialPath = "/") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const content = () => (
    <MemoryRouter initialEntries={[initialPath]}>
      <QueryClientProvider client={queryClient}>
        <PageContextProvider>
          <ChatContent butlerName="switchboard" />
        </PageContextProvider>
      </QueryClientProvider>
    </MemoryRouter>
  );
  const view = render(content());
  return {
    ...view,
    queryClient,
    rerenderChatContent: () => view.rerender(content()),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  scriptedEvents = [];
  activeSseEventHandler = null;
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
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <PageContextProvider>
            <ChatContent butlerName="finance" />
          </PageContextProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    );

    // Auto-resumed to finance's thread (sidebar entry + header title).
    expect(screen.getAllByText("finance thread")).toHaveLength(2);

    rerender(
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <PageContextProvider>
            <ChatContent butlerName="calendar" />
          </PageContextProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    );

    // Must re-resume to calendar's own thread (sidebar + header), not get
    // stuck on "New conversation" because the guard from the finance mount
    // never reset.
    expect(screen.getAllByText("calendar thread")).toHaveLength(2);
    expect(screen.queryByText("New conversation")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Conversation-key refetch isolation (bu-zu265)
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
    failNextThread() {
      nextMessagesResult = {
        data: undefined,
        isLoading: false,
        isError: true,
        refetch: vi.fn(),
      } as unknown as ReturnType<typeof useConversationMessages>;
    },
  };
}

describe("ChatContent — conversation switch refetch floor (bu-zu265)", () => {
  it("hides the previous thread while loading, then synchronizes the next thread", async () => {
    const { resolveNextThread } = mockHooksForConversationRefetchGap();
    const view = renderChatContent();

    expect(screen.getByText("Retained while the next thread refetches")).toBeDefined();

    fireEvent.click(screen.getByText("Next thread"));

    // The conversation selection changes immediately and TanStack Query's
    // pending result has no data. Never render the previous conversation's
    // messages under the newly selected conversation's header.
    expect(screen.getAllByText("Next thread")).toHaveLength(2);
    expect(screen.queryByText("Retained while the next thread refetches")).toBeNull();
    expect(screen.queryByText("No messages yet. Start the conversation below.")).toBeNull();

    resolveNextThread();
    view.rerenderChatContent();

    await waitFor(() => expect(screen.getByText("Rendered when the next thread arrives")).toBeDefined());
    expect(screen.queryByText("Retained while the next thread refetches")).toBeNull();
  });

  it("does not expose the previous thread when the selected thread fails to load", async () => {
    const { failNextThread } = mockHooksForConversationRefetchGap();
    const view = renderChatContent();

    expect(screen.getByText("Retained while the next thread refetches")).toBeDefined();
    fireEvent.click(screen.getByText("Next thread"));

    failNextThread();
    view.rerenderChatContent();

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Could not load conversation history.",
    );
    expect(screen.queryByText("Retained while the next thread refetches")).toBeNull();
    expect(screen.getAllByText("Next thread")).toHaveLength(2);
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
// Conversation read recovery
// ---------------------------------------------------------------------------

function mockHistoryReadFailureAfterInitialLoad() {
  const refetchMessages = vi.fn();
  const historyMessage: Message = {
    id: "history-message-1",
    conversation_id: "conv-1",
    role: "assistant",
    content: "Already loaded history stays visible",
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
  const noConversationResult = {
    data: { data: [], meta: {} },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useConversationMessages>;
  let activeConversationResult = {
    data: { data: [historyMessage], meta: {} },
    isLoading: false,
    isError: false,
    refetch: refetchMessages,
  } as unknown as ReturnType<typeof useConversationMessages>;

  vi.mocked(useConversations).mockReturnValue({
    data: { data: EXISTING_CONVERSATIONS, meta: {} },
    isLoading: false,
  } as unknown as ReturnType<typeof useConversations>);
  vi.mocked(useConversationMessages).mockImplementation(
    (_butlerName: string, conversationId: string | null) =>
      (conversationId === "conv-1"
        ? activeConversationResult
        : noConversationResult) as ReturnType<typeof useConversationMessages>,
  );
  vi.mocked(useConversationSearch).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
  } as unknown as ReturnType<typeof useConversationSearch>);

  return {
    failHistory() {
      activeConversationResult = {
        data: undefined,
        isLoading: false,
        isError: true,
        refetch: refetchMessages,
      } as unknown as ReturnType<typeof useConversationMessages>;
    },
    refetchMessages,
  };
}

describe("ChatContent — conversation read recovery", () => {
  it("keeps loaded history and the draft visible when history refresh fails, with retry", async () => {
    const { failHistory, refetchMessages } = mockHistoryReadFailureAfterInitialLoad();
    const view = renderChatContent();

    await waitFor(() => {
      expect(screen.getByText("Already loaded history stays visible")).toBeDefined();
    });

    failHistory();
    view.rerenderChatContent();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Could not load conversation history.");
    const input = screen.getByPlaceholderText("Type a message...") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "Keep this draft" } });
    expect(input.value).toBe("Keep this draft");
    expect(screen.getByText("Already loaded history stays visible")).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetchMessages).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// dispatch_accepted SSE routing receipt
// ---------------------------------------------------------------------------

describe("ChatContent — dispatch_accepted routing receipt", () => {
  it("announces and links the actual routed butler while waiting for a reply", async () => {
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [
      { event: "conversation_created", data: { conversation_id: "conv-route-1", title: null } },
      { event: "dispatch_accepted", data: { routed_butler: "finance" } },
    ];

    renderChatContent();

    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "categorize this" } });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    expect(screen.getByTestId("chat-activity-status").textContent).toBe(
      "Routed to finance; waiting for a reply.",
    );
    const routedButler = screen.getByRole("link", { name: "finance" });
    expect(routedButler.getAttribute("href")).toBe("/butlers/finance");
  });

  it("does not label a targetless acceptance as a domain route", async () => {
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [
      { event: "conversation_created", data: { conversation_id: "conv-route-2", title: null } },
      { event: "dispatch_accepted", data: { routed_butler: null } },
    ];

    renderChatContent();

    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "capture this" } });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    expect(screen.getByTestId("chat-activity-status").textContent).toBe(
      "Received by Switchboard; waiting for a reply.",
    );
    expect(screen.queryByRole("link", { name: "switchboard" })).toBeNull();
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
    // page_context is now attached (bu-0ynlk.4 fixes ChatPanel's previous
    // "sends no context at all" bypass) — default capture (route only, no
    // query params on "/").
    expect(sendMessageMock.mock.calls[0][2]).toEqual({
      message: "hello switchboard",
      message_id: firstPayload.message_id,
      page_context: { route: "/" },
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

  it("does not offer retry when TURN_OUTCOME_UNKNOWN prevents a safe replay", async () => {
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [
      {
        event: "error",
        data: {
          code: "TURN_OUTCOME_UNKNOWN",
          message: "This request may still have completed.",
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

    const banner = screen.getByTestId("chat-widget-ambiguous-banner");
    expect(banner.textContent).toContain("may still have completed");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.queryByTestId("chat-widget-timeout-session-link")).toBeNull();
  });

  it("offers check again, not retry, while INGEST_IN_PROGRESS owns the same turn", async () => {
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [
      {
        event: "error",
        data: {
          code: "INGEST_IN_PROGRESS",
          message: "This message is already being submitted.",
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

    const banner = screen.getByTestId("chat-widget-pending-banner");
    expect(banner.textContent).toContain("already being submitted");
    expect(screen.getByRole("button", { name: "Check again" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
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

// ---------------------------------------------------------------------------
// Stop button — server-side cancellation (bu-ep4ks.2)
// ---------------------------------------------------------------------------

describe("ChatContent — Stop button", () => {
  /** Drive ChatContent into an active mid-stream state (Stop button visible,
   * a real conversation id known) by scripting a token event with no
   * trailing `done` — the awaited send call resolves while still "open". */
  async function sendAndEnterStreamingState({
    conversationCreated = true,
    token = true,
  }: { conversationCreated?: boolean; token?: boolean } = {}) {
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [
      ...(conversationCreated
        ? [{ event: "conversation_created", data: { conversation_id: "conv-stop-1", title: null } }]
        : []),
      ...(token ? [{ event: "token", data: { content: "partial response" } }] : []),
    ];

    const view = renderChatContent();
    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "hello" } });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    expect(screen.getByTestId("chat-stop-button")).toBeDefined();
    return view;
  }

  it("does not send Stop until the create response proves the durable turn exists", async () => {
    let resolveCreate!: (response: Response) => void;
    createConversationMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveCreate = resolve;
        }),
    );

    renderChatContent();
    fireEvent.change(screen.getByPlaceholderText("Type a message..."), {
      target: { value: "hello" },
    });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
      await Promise.resolve();
    });

    const stopButton = screen.getByTestId("chat-stop-button") as HTMLButtonElement;
    expect(stopButton.disabled).toBe(true);
    fireEvent.click(stopButton);
    expect(cancelConversationMessageTurnMock).not.toHaveBeenCalled();

    await act(async () => {
      resolveCreate({ ok: true } as Response);
      await Promise.resolve();
    });
    await waitFor(() => expect((screen.getByTestId("chat-stop-button") as HTMLButtonElement).disabled).toBe(false));

    cancelConversationMessageTurnMock.mockResolvedValue({
      cancelled: true,
      already_finished: false,
      message: null,
    });
    fireEvent.click(screen.getByTestId("chat-stop-button"));
    await waitFor(() => expect(cancelConversationMessageTurnMock).toHaveBeenCalledOnce());
  });

  it("kills the session and renders the confirmed cancellation, never claiming success beforehand", async () => {
    await sendAndEnterStreamingState();
    cancelConversationMessageTurnMock.mockResolvedValue({
      cancelled: true,
      already_finished: false,
      session_id: "sess-1",
      message: null,
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("chat-stop-button"));
    });

    const messageId = (createConversationMock.mock.calls[0][1] as { message_id: string }).message_id;
    expect(cancelConversationMessageTurnMock).toHaveBeenCalledWith("switchboard", messageId);
    await waitFor(() => {
      expect(screen.getByText("Cancelled by owner")).toBeDefined();
    });
    expect(screen.getByRole("status").textContent).toBe("This turn was stopped.");
  });

  it("keeps the optimistic message visible through a confirmed Stop before conversation_created", async () => {
    const { queryClient } = await sendAndEnterStreamingState({
      conversationCreated: false,
      token: false,
    });
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");
    cancelConversationMessageTurnMock.mockResolvedValue({
      cancelled: true,
      already_finished: false,
      conversation_id: "conv-stop-1",
      session_id: null,
      message: null,
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("chat-stop-button"));
    });

    const messageId = (createConversationMock.mock.calls[0][1] as { message_id: string }).message_id;
    expect(cancelConversationMessageTurnMock).toHaveBeenCalledWith("switchboard", messageId);
    expect(screen.getByText("Cancelled by owner")).toBeDefined();
    expect(screen.getByText("hello")).toBeDefined();
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["conversation-messages", "switchboard", "conv-stop-1"],
    });
    expect(useConversationMessages).toHaveBeenLastCalledWith("switchboard", "conv-stop-1");
  });

  it("suppresses a named receipt during Stop settlement and confirmed cancellation", async () => {
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [
      { event: "conversation_created", data: { conversation_id: "conv-stop-route", title: null } },
      { event: "dispatch_accepted", data: { routed_butler: "finance" } },
    ];
    renderChatContent();
    fireEvent.change(screen.getByPlaceholderText("Type a message..."), {
      target: { value: "route then stop" },
    });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    expect(screen.getByTestId("chat-activity-status").textContent).toBe(
      "Routed to finance; waiting for a reply.",
    );
    expect(screen.getByRole("link", { name: "finance" })).toBeDefined();

    let resolveCancel!: (result: { cancelled: boolean; already_finished: boolean }) => void;
    cancelConversationMessageTurnMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveCancel = resolve;
        }),
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId("chat-stop-button"));
      await Promise.resolve();
    });

    expect(screen.getByRole("status").textContent).toBe("Stopping this turn.");
    expect(screen.queryByTestId("chat-activity-status")).toBeNull();
    expect(screen.queryByRole("link", { name: "finance" })).toBeNull();

    await act(async () => {
      activeSseEventHandler?.({
        event: "error",
        data: { code: "SESSION_CANCELLED", message: "This turn was stopped before routing." },
      });
    });
    expect(screen.getByText("Cancelled by owner")).toBeDefined();
    expect(screen.getByRole("status").textContent).toBe("This turn was stopped.");
    expect(screen.queryByTestId("chat-activity-status")).toBeNull();
    expect(screen.queryByRole("link", { name: "finance" })).toBeNull();

    await act(async () => {
      resolveCancel({ cancelled: false, already_finished: true });
      await Promise.resolve();
    });
  });

  it("accepts a terminal server cancellation after Stop was still settling", async () => {
    await sendAndEnterStreamingState();
    cancelConversationMessageTurnMock.mockResolvedValue({
      cancelled: false,
      already_finished: false,
      session_id: null,
      message: "Waiting for the in-flight ingress to settle.",
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("chat-stop-button"));
    });
    expect(screen.getByText("Waiting for the in-flight ingress to settle.")).toBeDefined();

    await act(async () => {
      activeSseEventHandler?.({
        event: "error",
        data: { code: "SESSION_CANCELLED", message: "This turn was stopped before routing." },
      });
    });

    expect(screen.getByText("Cancelled by owner")).toBeDefined();
    expect(screen.queryByText("Waiting for the in-flight ingress to settle.")).toBeNull();
    expect(screen.getByRole("status").textContent).toBe("This turn was stopped.");
  });

  it("keeps an SSE-confirmed Stop visible when its POST later says already finished", async () => {
    await sendAndEnterStreamingState();
    let resolveCancel!: (result: { cancelled: boolean; already_finished: boolean }) => void;
    cancelConversationMessageTurnMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveCancel = resolve;
        }),
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("chat-stop-button"));
      await Promise.resolve();
    });
    await act(async () => {
      activeSseEventHandler?.({
        event: "error",
        data: { code: "SESSION_CANCELLED", message: "This turn was stopped before routing." },
      });
    });
    expect(screen.getByText("Cancelled by owner")).toBeDefined();

    await act(async () => {
      resolveCancel({ cancelled: false, already_finished: true });
      await Promise.resolve();
    });

    expect(screen.getByText("Cancelled by owner")).toBeDefined();
    expect(screen.getByRole("status").textContent).toBe("This turn was stopped.");
  });

  it("ignores a late Stop result after the owner starts a different turn", async () => {
    await sendAndEnterStreamingState();
    let resolveCancel!: (result: { cancelled: boolean; already_finished: boolean }) => void;
    cancelConversationMessageTurnMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveCancel = resolve;
        }),
    );

    fireEvent.click(screen.getByTestId("chat-stop-button"));
    expect((screen.getByTestId("chat-stop-button") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("status").textContent).toBe("Stopping this turn.");
    fireEvent.click(screen.getByText("New"));

    scriptedEvents = [
      { event: "conversation_created", data: { conversation_id: "conv-stop-2", title: null } },
      { event: "token", data: { content: "second turn" } },
    ];
    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "second" } });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });
    expect(screen.getByText("second turn")).toBeDefined();

    await act(async () => {
      resolveCancel({ cancelled: true, already_finished: false });
    });

    expect(screen.getByText("second turn")).toBeDefined();
    expect(screen.queryByText("Cancelled by owner")).toBeNull();
    expect(screen.getByTestId("chat-stop-button")).toBeDefined();
  });

  it("surfaces a failed cancel honestly instead of rendering calm", async () => {
    await sendAndEnterStreamingState();
    cancelConversationMessageTurnMock.mockResolvedValue({
      cancelled: false,
      already_finished: false,
      session_id: "sess-1",
      message: "switchboard unreachable",
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("chat-stop-button"));
    });

    await waitFor(() => {
      expect(screen.getByText("switchboard unreachable")).toBeDefined();
    });
    expect((screen.getByTestId("chat-stop-button") as HTMLButtonElement).disabled).toBe(false);
  });

  it("quietly stops watching without a false 'stopped' claim when the turn already finished", async () => {
    const { queryClient } = await sendAndEnterStreamingState({
      conversationCreated: false,
      token: false,
    });
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");
    cancelConversationMessageTurnMock.mockResolvedValue({
      cancelled: false,
      already_finished: true,
      conversation_id: "conv-finished-1",
      session_id: null,
      message: null,
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("chat-stop-button"));
    });

    expect(screen.queryByText("Cancelled by owner")).toBeNull();
    expect(screen.queryByTestId("chat-stop-button")).toBeNull();
    expect(screen.getByText("hello")).toBeDefined();
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["conversations", "switchboard"],
    });
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["conversation-messages", "switchboard", "conv-finished-1"],
    });
    expect(useConversationMessages).toHaveBeenLastCalledWith("switchboard", "conv-finished-1");
  });
});

// ---------------------------------------------------------------------------
// Page-context capture / ContextChip (bu-0ynlk.4)
// ---------------------------------------------------------------------------

describe("ChatContent — page-context capture", () => {
  it("attaches route + query params captured at send time (fixes the prior 'no context at all' bypass)", async () => {
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [{ event: "done", data: {} }];

    renderChatContent("/entities/concentration?predicate=child-of");

    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "Alice is child-of Bob" } });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    expect(createConversationMock.mock.calls[0][1]).toEqual(
      expect.objectContaining({
        message: "Alice is child-of Bob",
        page_context: {
          route: "/entities/concentration",
          query_params: { predicate: "child-of" },
        },
      }),
    );
  });

  it("omits the page_context key entirely when the ContextChip is detached", async () => {
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [{ event: "done", data: {} }];

    renderChatContent("/entities/concentration?predicate=child-of");
    fireEvent.click(screen.getByTestId("context-chip-remove"));

    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "Alice is child-of Bob" } });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    const payload = createConversationMock.mock.calls[0][1] as Record<string, unknown>;
    expect(payload).not.toHaveProperty("page_context");
  });

  it("never attaches page_context on a policy 'none' route (/secrets)", async () => {
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [{ event: "done", data: {} }];

    renderChatContent("/secrets");

    expect(screen.getByTestId("context-chip").getAttribute("data-policy")).toBe("none");
    expect(screen.queryByTestId("context-chip-remove")).toBeNull();

    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "what's my API key" } });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    expect(createConversationMock.mock.calls[0][1]).not.toHaveProperty("page_context");
  });
});
