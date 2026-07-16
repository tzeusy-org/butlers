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
  it("shows a retryable offline banner on SWITCHBOARD_UNAVAILABLE instead of an inert error bubble", async () => {
    sendMessageMock.mockResolvedValue({ ok: true } as Response);
    createConversationMock.mockResolvedValue({ ok: true } as Response);
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

    const firstPayload = createConversationMock.mock.calls[0][1] as {
      message_id: string;
    };
    // Retry uses the persisted conversation and exact same client message ID.
    sendMessageMock.mockClear();
    scriptedEvents = [{ event: "done", data: {} }];
    await act(async () => {
      fireEvent.click(screen.getByText("Retry"));
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
