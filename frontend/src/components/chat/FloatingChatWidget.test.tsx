// @vitest-environment jsdom
/**
 * FloatingChatWidget — RTL tests (bu-p6ey8.3).
 *
 * Covers:
 *  - Trigger button renders; opening/closing the panel
 *  - Resuming the most recent open conversation on open
 *  - History view: conversation list rendering (incl. routed_butler badge)
 *    and switching back to the thread on select
 *  - "Talk to Butlers" cmdk command registration opens the widget
 *  - New-conversation reset
 *  - Send-error classification: SWITCHBOARD_UNAVAILABLE -> retry banner,
 *    SESSION_TIMEOUT -> inspect-session banner
 *
 * Data-fetching hooks (`@/hooks/use-conversations`) and the SSE/create/send
 * boundary (`./sse-utils`, `@/api/index`) are mocked so tests are
 * deterministic and don't depend on a real backend or streaming transport.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

import { FloatingChatWidget } from "./FloatingChatWidget";
import { CommandRegistryProvider, useCommandMenuActions } from "@/lib/command-registry";
import { PageContextProvider } from "@/lib/page-context.tsx";
import { __resetChatUnreadWatermarkForTests } from "@/hooks/use-chat-unread.ts";
import type { ConversationSummary, Message } from "@/api/types.ts";

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
const cancelConversationTurnMock = vi.fn();
vi.mock("@/api/index.ts", () => ({
  createConversation: (...args: unknown[]) => createConversationMock(...args),
  sendMessage: (...args: unknown[]) => sendMessageMock(...args),
  cancelConversationTurn: (...args: unknown[]) => cancelConversationTurnMock(...args),
}));

// consumeSseStream is mocked to synchronously replay a scripted event queue,
// bypassing real stream parsing entirely.
let scriptedEvents: Array<{ event: string; data: unknown }> = [];
vi.mock("./sse-utils.ts", () => ({
  consumeSseStream: async (
    _response: Response,
    onEvent: (event: { event: string; data: unknown }) => void,
  ) => {
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

const CONVERSATIONS: ConversationSummary[] = [
  {
    id: "conv-2",
    butler_name: "switchboard",
    title: "Most recent thread",
    status: "active",
    created_at: "2026-07-04T12:00:00.000Z",
    updated_at: "2026-07-05T09:00:00.000Z",
    message_count: 2,
    routed_butler: "relationship",
  },
  {
    id: "conv-1",
    butler_name: "switchboard",
    title: "Older thread",
    status: "active",
    created_at: "2026-07-03T12:00:00.000Z",
    updated_at: "2026-07-03T12:05:00.000Z",
    message_count: 1,
    routed_butler: null,
  },
];

const MESSAGES_BY_CONV: Record<string, Message[]> = {
  "conv-2": [
    {
      id: "msg-1",
      conversation_id: "conv-2",
      role: "user",
      content: "Alice is child-of Bob",
      tool_calls: null,
      error: null,
      model: null,
      input_tokens: null,
      output_tokens: null,
      duration_ms: null,
      session_id: null,
      request_id: null,
      created_at: "2026-07-05T09:00:00.000Z",
    },
  ],
  "conv-1": [],
};

const NEXT_THREAD_MESSAGE: Message = {
  ...MESSAGES_BY_CONV["conv-2"][0],
  id: "msg-older-thread",
  conversation_id: "conv-1",
  content: "Rendered when the older thread arrives",
};

function mockHooksWithConversations() {
  vi.mocked(useConversations).mockReturnValue({
    data: { data: CONVERSATIONS, meta: {} },
    isLoading: false,
  } as unknown as ReturnType<typeof useConversations>);

  // Real react-query returns a stable `data` reference across re-renders
  // when the underlying query hasn't refetched. A mockImplementation that
  // allocates a fresh object/array on every call breaks that invariant and
  // sends WidgetPanel's `messagesData` sync effect into an infinite
  // render loop (new identity -> effect fires -> setLocalMessages -> new
  // identity -> ...). Precompute one stable result per conversationId key
  // (including the "no conversation selected" case) so identity is stable.
  const stableResultByKey = new Map<string, unknown>();
  function resultFor(conversationId: string | null) {
    const key = conversationId ?? "__none__";
    if (!stableResultByKey.has(key)) {
      stableResultByKey.set(key, {
        data: { data: conversationId ? (MESSAGES_BY_CONV[conversationId] ?? []) : [], meta: {} },
        isLoading: false,
      });
    }
    return stableResultByKey.get(key);
  }

  vi.mocked(useConversationMessages).mockImplementation(
    (_butlerName: string, conversationId: string | null) =>
      resultFor(conversationId) as ReturnType<typeof useConversationMessages>,
  );

  vi.mocked(useConversationSearch).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
  } as unknown as ReturnType<typeof useConversationSearch>);
}

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

function mockHooksForConversationRefetchGap() {
  const conversationsResult = {
    data: { data: CONVERSATIONS, meta: {} },
    isLoading: false,
  } as unknown as ReturnType<typeof useConversations>;
  const emptyMessagesResult = {
    data: { data: [], meta: {} },
    isLoading: false,
  } as unknown as ReturnType<typeof useConversationMessages>;
  const currentMessagesResult = {
    data: { data: MESSAGES_BY_CONV["conv-2"], meta: {} },
    isLoading: false,
  } as unknown as ReturnType<typeof useConversationMessages>;
  let olderMessagesResult = {
    data: undefined,
    isLoading: true,
  } as unknown as ReturnType<typeof useConversationMessages>;

  vi.mocked(useConversations).mockReturnValue(conversationsResult);
  vi.mocked(useConversationMessages).mockImplementation(
    (_butlerName: string, conversationId: string | null) => {
      if (conversationId === "conv-2") return currentMessagesResult;
      if (conversationId === "conv-1") return olderMessagesResult;
      return emptyMessagesResult;
    },
  );
  vi.mocked(useConversationSearch).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
  } as unknown as ReturnType<typeof useConversationSearch>);

  return {
    resolveOlderThread() {
      olderMessagesResult = {
        data: { data: [NEXT_THREAD_MESSAGE], meta: {} },
        isLoading: false,
      } as unknown as ReturnType<typeof useConversationMessages>;
    },
  };
}

function buildWidgetTree(queryClient: QueryClient, initialPath: string) {
  return (
    <MemoryRouter initialEntries={[initialPath]}>
      <QueryClientProvider client={queryClient}>
        <CommandRegistryProvider>
          <PageContextProvider>
            <FloatingChatWidget />
          </PageContextProvider>
        </CommandRegistryProvider>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

function renderWidget(initialPath = "/") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(buildWidgetTree(queryClient, initialPath));
  return {
    ...utils,
    /** Force a re-render (e.g. after changing a mocked hook's return value). */
    rerenderWidget: (path: string = initialPath) => utils.rerender(buildWidgetTree(queryClient, path)),
  };
}

beforeEach(() => {
  // clearAllMocks (not resetAllMocks): resetAllMocks would strip the
  // `fetchPricingMap` module factory's `.mockResolvedValue(...)` set at
  // import time, leaving it returning undefined on every render.
  vi.clearAllMocks();
  scriptedEvents = [];
  mockHooksWithConversations();
  // useChatUnreadBadge's watermark is a real (unmocked) module-scope store —
  // reset it + localStorage so badge state never leaks across tests.
  window.localStorage.clear();
  __resetChatUnreadWatermarkForTests();
});

afterEach(() => cleanup());

// ---------------------------------------------------------------------------
// Trigger + open/close
// ---------------------------------------------------------------------------

describe("FloatingChatWidget — trigger and open/close", () => {
  it("renders the floating trigger button on mount", () => {
    renderWidget();
    expect(screen.getByTestId("floating-chat-trigger")).toBeDefined();
    expect(screen.queryByTestId("floating-chat-panel")).toBeNull();
  });

  it("opens the panel without a ref-forwarding warning", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    try {
      renderWidget();
      fireEvent.click(screen.getByTestId("floating-chat-trigger"));
      expect(screen.getByTestId("floating-chat-panel")).toBeDefined();
      expect(screen.queryByTestId("floating-chat-trigger")).toBeNull();
      expect(
        consoleError.mock.calls.some(
          ([message]) =>
            typeof message === "string" && message.includes("Function components cannot be given refs"),
        ),
      ).toBe(false);
    } finally {
      consoleError.mockRestore();
    }
  });

  it("closes the panel and restores the trigger on close click", () => {
    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    fireEvent.click(screen.getByTestId("chat-widget-close-button"));
    expect(screen.queryByTestId("floating-chat-panel")).toBeNull();
    expect(screen.getByTestId("floating-chat-trigger")).toBeDefined();
  });

  it("focuses its non-modal panel and restores the trigger after Escape closes it", () => {
    renderWidget();
    const trigger = screen.getByTestId("floating-chat-trigger");
    trigger.focus();

    fireEvent.click(trigger);

    const panel = screen.getByTestId("floating-chat-panel");
    expect(panel.getAttribute("aria-modal")).toBeNull();
    expect(document.activeElement).toBe(
      within(panel).getByRole("heading", { name: "Talk to Butlers" }),
    );

    const input = within(panel).getByPlaceholderText("Type a message...");
    input.focus();
    expect(fireEvent.keyDown(input, { key: "Tab" })).toBe(true);
    expect(document.activeElement).toBe(input);

    fireEvent.keyDown(input, { key: "Escape" });

    expect(screen.queryByTestId("floating-chat-panel")).toBeNull();
    expect(document.activeElement).toBe(screen.getByTestId("floating-chat-trigger"));
  });
});

// ---------------------------------------------------------------------------
// Resume most recent conversation
// ---------------------------------------------------------------------------

describe("FloatingChatWidget — resume lifecycle", () => {
  it("resumes the most recently updated open conversation on open", () => {
    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    // conv-2 ("Most recent thread") is first in the server-ordered list.
    expect(screen.getByText("Most recent thread")).toBeDefined();
    expect(screen.getByText("Alice is child-of Bob")).toBeDefined();
  });

  it("shows the empty thread state when there are no conversations yet", () => {
    mockHooksEmpty();
    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    expect(screen.getByText("New conversation")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// History view
// ---------------------------------------------------------------------------

describe("FloatingChatWidget — history view", () => {
  it("toggles to the history view and lists conversations with routed-butler metadata", () => {
    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    fireEvent.click(screen.getByTestId("chat-widget-history-button"));

    expect(screen.getByText("Older thread")).toBeDefined();
    expect(screen.getAllByText("Most recent thread").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByTestId("conversation-routed-butler").textContent).toBe("relationship");
  });

  it("selecting a past thread switches back to the thread view", () => {
    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    fireEvent.click(screen.getByTestId("chat-widget-history-button"));

    fireEvent.click(screen.getByText("Older thread"));

    expect(screen.queryByTestId("chat-widget-back-button")).toBeNull();
    expect(screen.getByTestId("chat-widget-history-button")).toBeDefined();
  });

  it("keeps the current thread visible while loading, then synchronizes the selected thread", async () => {
    const { resolveOlderThread } = mockHooksForConversationRefetchGap();
    const view = renderWidget();

    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    expect(screen.getByText("Alice is child-of Bob")).toBeDefined();

    fireEvent.click(screen.getByTestId("chat-widget-history-button"));
    fireEvent.click(screen.getByText("Older thread"));

    expect(screen.getByText("Alice is child-of Bob")).toBeDefined();
    expect(screen.queryByText("No messages yet. Start the conversation below.")).toBeNull();

    resolveOlderThread();
    view.rerenderWidget();

    await waitFor(() => {
      expect(screen.getByText("Rendered when the older thread arrives")).toBeDefined();
    });
    expect(screen.queryByText("Alice is child-of Bob")).toBeNull();
  });

  it("New button from history resets to a fresh conversation in thread view", () => {
    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    fireEvent.click(screen.getByTestId("chat-widget-history-button"));

    const historyPanel = screen.getByTestId("floating-chat-panel");
    fireEvent.click(within(historyPanel).getByText("New"));

    expect(screen.getByText("New conversation")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// cmdk command registration
// ---------------------------------------------------------------------------

describe("FloatingChatWidget — cmdk command registration", () => {
  it("registers a 'Talk to Butlers' command that opens the widget", () => {
    function CommandProbe() {
      const commands = useCommandMenuActions();
      const talkCommand = commands.find((c) => c.id === "talk-to-butlers");
      return (
        <button
          type="button"
          data-testid="probe-run-command"
          onClick={() => talkCommand?.perform()}
        >
          run
        </button>
      );
    }

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MemoryRouter initialEntries={["/"]}>
        <QueryClientProvider client={queryClient}>
          <CommandRegistryProvider>
            <PageContextProvider>
              <FloatingChatWidget />
              <CommandProbe />
            </PageContextProvider>
          </CommandRegistryProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    );

    expect(screen.queryByTestId("floating-chat-panel")).toBeNull();
    fireEvent.click(screen.getByTestId("probe-run-command"));
    expect(screen.getByTestId("floating-chat-panel")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Page-context capture (bu-p6ey8.4)
// ---------------------------------------------------------------------------

describe("FloatingChatWidget — page-context capture", () => {
  it("attaches route + query params captured at send time", async () => {
    mockHooksEmpty();
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [{ event: "done", data: {} }];

    renderWidget("/entities/concentration?predicate=child-of");
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));

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
        message_id: expect.any(String),
      }),
    );
  });
});

// ---------------------------------------------------------------------------
// dispatch_accepted SSE routing receipt
// ---------------------------------------------------------------------------

describe("FloatingChatWidget — dispatch_accepted routing receipt", () => {
  it("announces and links the actual routed butler while waiting for a reply", async () => {
    mockHooksEmpty();
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [
      { event: "conversation_created", data: { conversation_id: "conv-route-1", title: null } },
      { event: "dispatch_accepted", data: { routed_butler: "relationship" } },
    ];

    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));

    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "remember this" } });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    const activity = screen.getByTestId("chat-activity-status");
    expect(activity.textContent).toBe("Routed to relationship; waiting for a reply.");
    expect(activity.getAttribute("role")).toBe("status");
    expect(activity.getAttribute("aria-live")).toBe("polite");
    expect(activity.getAttribute("aria-atomic")).toBe("true");

    const routedButler = screen.getByRole("link", { name: "relationship" });
    expect(routedButler.getAttribute("href")).toBe("/butlers/relationship");
  });

  it("does not invent a domain route for a targetless acceptance", async () => {
    // The resumed thread has a persisted `relationship` route. An explicit
    // targetless receipt for this turn must override it rather than showing
    // stale accountability.
    sendMessageMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [{ event: "dispatch_accepted", data: { routed_butler: null } }];

    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));

    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "log this" } });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    expect(screen.getByTestId("chat-activity-status").textContent).toBe(
      "Received by Switchboard; waiting for a reply.",
    );
    expect(screen.queryByRole("link", { name: "relationship" })).toBeNull();
    expect(screen.queryByRole("link", { name: "switchboard" })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Conversation read recovery
// ---------------------------------------------------------------------------

describe("FloatingChatWidget — conversation read recovery", () => {
  it("keeps the draft available and offers retry when resumed history cannot load", async () => {
    const refetchMessages = vi.fn();
    const noConversationResult = {
      data: { data: [], meta: {} },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useConversationMessages>;
    const failedHistoryResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: refetchMessages,
    } as unknown as ReturnType<typeof useConversationMessages>;
    vi.mocked(useConversationMessages).mockImplementation(
      (_butlerName: string, conversationId: string | null) =>
        (conversationId === "conv-2"
          ? failedHistoryResult
          : noConversationResult) as ReturnType<typeof useConversationMessages>,
    );

    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Could not load conversation history.");
    expect(screen.queryByText("No messages yet. Start the conversation below.")).toBeNull();

    const input = screen.getByPlaceholderText("Type a message...") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "Keep this draft" } });
    expect(input.value).toBe("Keep this draft");

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetchMessages).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// Unread-reply badge (bu-p6ey8.4)
// ---------------------------------------------------------------------------

describe("FloatingChatWidget — unread badge", () => {
  // The watermark follows the latest persisted assistant reply.
  function conversationsWithLatestReplyAt(latestReplyAt: string | null): ConversationSummary[] {
    return [
      { ...CONVERSATIONS[0], latest_assistant_reply_at: latestReplyAt },
      CONVERSATIONS[1],
    ];
  }

  function mockConversationTotals(latestReplyAt: string | null) {
    vi.mocked(useConversations).mockReturnValue({
      data: { data: conversationsWithLatestReplyAt(latestReplyAt), meta: {} },
      isLoading: false,
    } as unknown as ReturnType<typeof useConversations>);
  }

  it("badges the trigger when a reply lands while the panel is closed, and opening clears it", () => {
    mockConversationTotals("2026-07-05T09:00:00.000Z");
    const { rerenderWidget } = renderWidget();

    // First observation of this conversation just establishes the baseline —
    // no badge yet.
    expect(screen.queryByTestId("chat-widget-unread-badge")).toBeNull();

    // Simulate the ~60s poll surfacing a reply while the panel stayed closed.
    mockConversationTotals("2026-07-05T09:05:00.000Z");
    rerenderWidget();
    expect(screen.getByTestId("chat-widget-unread-badge")).toBeDefined();

    // Opening the panel clears the badge.
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    expect(screen.queryByTestId("chat-widget-unread-badge")).toBeNull();
  });

  it("does not badge when nothing changed since the last observation", () => {
    mockConversationTotals("2026-07-05T09:00:00.000Z");
    const { rerenderWidget } = renderWidget();
    expect(screen.queryByTestId("chat-widget-unread-badge")).toBeNull();

    // Re-poll with the exact same latest_assistant_reply_at (no reply arrived).
    mockConversationTotals("2026-07-05T09:00:00.000Z");
    rerenderWidget();
    expect(screen.queryByTestId("chat-widget-unread-badge")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Send-error classification
// ---------------------------------------------------------------------------

describe("FloatingChatWidget — send-error classification", () => {
  it("uses doctrine-compliant copy for a generic transport failure", async () => {
    mockHooksEmpty();
    createConversationMock.mockRejectedValue(new Error("network unavailable"));

    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));

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
    expect(within(banner).getByRole("button", { name: "Retry" })).toBeDefined();
  });

  it("keeps a failed optimistic message visible and retryable through an empty server sync", async () => {
    mockHooksEmpty();
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

    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));

    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "hello switchboard" } });

    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    expect(screen.getByTestId("chat-widget-error-banner")).toBeDefined();
    expect(screen.getByTestId("chat-widget-error-banner").textContent).toContain(
      "Switchboard offline",
    );

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
      fireEvent.click(screen.getByText("Retry"));
    });
    expect(sendMessageMock).toHaveBeenCalledTimes(1);
    expect(sendMessageMock.mock.calls[0][1]).toBe("conv-retry-1");
    // page_context is now attached (bu-p6ey8.4) — default capture (route
    // only, "/" has no query params) since this test renders at "/".
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
    mockHooksEmpty();
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

    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));

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
});

// ---------------------------------------------------------------------------
// Stop button — server-side cancellation (bu-ep4ks.2)
// ---------------------------------------------------------------------------

describe("FloatingChatWidget — Stop button", () => {
  /** Drive the widget into an active mid-stream state (Stop button visible,
   * a real conversation id known) by scripting a token event with no
   * trailing `done` — the awaited send call resolves while still "open". */
  async function sendAndEnterStreamingState() {
    mockHooksEmpty();
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [
      { event: "conversation_created", data: { conversation_id: "conv-stop-1", title: "New" } },
      { event: "token", data: { content: "partial response" } },
    ];

    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "hello" } });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    expect(screen.getByTestId("chat-stop-button")).toBeDefined();
  }

  it("kills the session and renders the confirmed cancellation, never claiming success beforehand", async () => {
    await sendAndEnterStreamingState();
    cancelConversationTurnMock.mockResolvedValue({
      cancelled: true,
      already_finished: false,
      session_id: "sess-1",
      message: null,
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("chat-stop-button"));
    });

    expect(cancelConversationTurnMock).toHaveBeenCalledWith("switchboard", "conv-stop-1");
    await waitFor(() => {
      expect(screen.getByText("Cancelled by owner")).toBeDefined();
    });
  });

  it("surfaces a failed cancel honestly instead of rendering calm", async () => {
    await sendAndEnterStreamingState();
    cancelConversationTurnMock.mockResolvedValue({
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
    // Stop remains actionable — the failed attempt did not lock the UI up.
    expect(
      (screen.getByTestId("chat-stop-button") as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it("quietly stops watching without a false 'stopped' claim when the turn already finished", async () => {
    await sendAndEnterStreamingState();
    cancelConversationTurnMock.mockResolvedValue({
      cancelled: false,
      already_finished: true,
      session_id: null,
      message: null,
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("chat-stop-button"));
    });

    expect(screen.queryByText("Cancelled by owner")).toBeNull();
    expect(screen.queryByTestId("chat-stop-button")).toBeNull();
  });
});
