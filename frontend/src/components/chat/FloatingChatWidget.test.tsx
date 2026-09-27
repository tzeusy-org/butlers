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
import { axe, toHaveNoViolations } from "jest-axe";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

import { FloatingChatWidget } from "./FloatingChatWidget";
import { CommandRegistryProvider, useCommandMenuActions } from "@/lib/command-registry";
import { PageContextProvider } from "@/lib/page-context.tsx";
import { __resetChatUnreadWatermarkForTests } from "@/hooks/use-chat-unread.ts";
import { OPEN_CHAT_WIDGET_EVENT } from "@/lib/shortcut-help";
import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";
import type { ConversationSummary, Message } from "@/api/types.ts";

expect.extend(toHaveNoViolations);

const announceMock = vi.fn();
vi.mock("@/lib/shell-announcer", () => ({
  announce: (text: string) => announceMock(text),
}));

vi.mock("@/hooks/use-prefers-reduced-motion", () => ({
  usePrefersReducedMotion: vi.fn(() => false),
}));

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
const getConversationMessagesMock = vi.fn();
vi.mock("@/api/index.ts", () => ({
  createConversation: (...args: unknown[]) => createConversationMock(...args),
  sendMessage: (...args: unknown[]) => sendMessageMock(...args),
  cancelConversationMessageTurn: (...args: unknown[]) =>
    cancelConversationMessageTurnMock(...args),
  getConversationMessages: (...args: unknown[]) => getConversationMessagesMock(...args),
}));

// consumeSseStream is mocked to synchronously replay a scripted event queue,
// bypassing real stream parsing entirely.
let scriptedEvents: Array<{ event: string; data: unknown }> = [];
let activeSseEventHandler: ((event: { event: string; data: unknown }) => void) | null = null;
// bu-0ynlk.7: simulates a non-abort transport failure (network reset, proxy
// drop) interrupting the stream after `scriptedEvents` has been replayed,
// rather than the stream completing normally with `done`.
let simulateStreamFailure: Error | null = null;
vi.mock("./sse-utils.ts", () => ({
  consumeSseStream: async (
    _response: Response,
    onEvent: (event: { event: string; data: unknown }) => void,
  ) => {
    activeSseEventHandler = onEvent;
    for (const evt of scriptedEvents) onEvent(evt);
    if (simulateStreamFailure) {
      const err = simulateStreamFailure;
      simulateStreamFailure = null;
      throw err;
    }
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
    failOlderThread() {
      olderMessagesResult = {
        data: undefined,
        isLoading: false,
        isError: true,
        refetch: vi.fn(),
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
    queryClient,
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
  activeSseEventHandler = null;
  simulateStreamFailure = null;
  // Safe default for the non-abort-stream-failure recovery refetch
  // (bu-0ynlk.7) -- most tests never exercise that path, but an unconfigured
  // mock returning `undefined` would throw on `.data` access if one did.
  getConversationMessagesMock.mockResolvedValue({ data: [] });
  mockHooksWithConversations();
  // useChatUnreadBadge's watermark is a real (unmocked) module-scope store —
  // reset it + localStorage so badge state never leaks across tests.
  window.localStorage.clear();
  __resetChatUnreadWatermarkForTests();
  vi.mocked(usePrefersReducedMotion).mockReturnValue(false);
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

  it("focuses its non-modal panel's composer (not the title) and restores the trigger after Escape closes it (bu-0ynlk.13)", () => {
    renderWidget();
    const trigger = screen.getByTestId("floating-chat-trigger");
    trigger.focus();

    fireEvent.click(trigger);

    const panel = screen.getByTestId("floating-chat-panel");
    expect(panel.getAttribute("aria-modal")).toBeNull();
    const input = within(panel).getByPlaceholderText("Type a message...");
    expect(document.activeElement).toBe(input);

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

  it("hides the current thread while loading, then synchronizes the selected thread", async () => {
    const { resolveOlderThread } = mockHooksForConversationRefetchGap();
    const view = renderWidget();

    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    expect(screen.getByText("Alice is child-of Bob")).toBeDefined();

    fireEvent.click(screen.getByTestId("chat-widget-history-button"));
    fireEvent.click(screen.getByText("Older thread"));

    expect(screen.queryByText("Alice is child-of Bob")).toBeNull();
    expect(screen.queryByText("No messages yet. Start the conversation below.")).toBeNull();

    resolveOlderThread();
    view.rerenderWidget();

    await waitFor(() => {
      expect(screen.getByText("Rendered when the older thread arrives")).toBeDefined();
    });
    expect(screen.queryByText("Alice is child-of Bob")).toBeNull();
  });

  it("does not expose the previous thread when the selected thread fails to load", async () => {
    const { failOlderThread } = mockHooksForConversationRefetchGap();
    const view = renderWidget();

    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    expect(screen.getByText("Alice is child-of Bob")).toBeDefined();

    fireEvent.click(screen.getByTestId("chat-widget-history-button"));
    fireEvent.click(screen.getByText("Older thread"));
    failOlderThread();
    view.rerenderWidget();

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Could not load conversation history.",
    );
    expect(screen.queryByText("Alice is child-of Bob")).toBeNull();
    expect(screen.getByText("Older thread")).toBeDefined();
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

  it("omits the page_context key entirely (not an empty object) when the ContextChip is detached", async () => {
    mockHooksEmpty();
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [{ event: "done", data: {} }];

    renderWidget("/entities/concentration?predicate=child-of");
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));

    fireEvent.click(screen.getByTestId("context-chip-remove"));

    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "Alice is child-of Bob" } });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    const payload = createConversationMock.mock.calls[0][1] as Record<string, unknown>;
    expect(payload).not.toHaveProperty("page_context");
    expect(payload.message).toBe("Alice is child-of Bob");
  });

  it("re-attaches context for the next message after a detached send", async () => {
    mockHooksEmpty();
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [{ event: "done", data: {} }];

    renderWidget("/entities/concentration?predicate=child-of");
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    fireEvent.click(screen.getByTestId("context-chip-remove"));

    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "first message" } });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });
    expect(createConversationMock.mock.calls[0][1]).not.toHaveProperty("page_context");

    // The chip must have reset to "attached" for the next composition.
    expect(screen.getByTestId("context-chip").getAttribute("data-included")).toBe("true");

    fireEvent.change(input, { target: { value: "second message" } });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });
    expect(createConversationMock.mock.calls[1][1]).toEqual(
      expect.objectContaining({ page_context: expect.any(Object) }),
    );
  });

  it("never attaches page_context on a policy 'none' route (/secrets)", async () => {
    mockHooksEmpty();
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [{ event: "done", data: {} }];

    renderWidget("/secrets");
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));

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

describe("FloatingChatWidget — stream-failure recovery (bu-0ynlk.7)", () => {
  it("recovers silently when the reply already landed despite a non-abort stream failure", async () => {
    mockHooksEmpty();
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [
      {
        event: "conversation_created",
        data: { conversation_id: "conv-recovered-1", title: null },
      },
    ];
    simulateStreamFailure = new Error("network reset");
    getConversationMessagesMock.mockResolvedValue({
      data: [
        {
          id: "reply-1",
          conversation_id: "conv-recovered-1",
          role: "assistant",
          content: "Here's your answer.",
          tool_calls: null,
          error: null,
          model: null,
          input_tokens: null,
          output_tokens: null,
          duration_ms: null,
          session_id: null,
          request_id: null,
          created_at: "2027-01-01T00:00:00.000Z",
        },
      ],
    });

    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));

    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "hello" } });

    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    await waitFor(() => {
      expect(getConversationMessagesMock).toHaveBeenCalledWith("switchboard", "conv-recovered-1");
    });
    expect(screen.queryByTestId("chat-widget-error-banner")).toBeNull();
  });

  it("still shows the failure banner when the refetch proves no reply landed", async () => {
    mockHooksEmpty();
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [
      {
        event: "conversation_created",
        data: { conversation_id: "conv-unrecovered-1", title: null },
      },
    ];
    simulateStreamFailure = new Error("network reset");
    getConversationMessagesMock.mockResolvedValue({ data: [] });

    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));

    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "hello" } });

    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    await waitFor(() => {
      expect(getConversationMessagesMock).toHaveBeenCalledWith(
        "switchboard",
        "conv-unrecovered-1",
      );
    });
    expect(screen.getByTestId("chat-widget-error-banner").textContent).toContain(
      "Failed to send message.",
    );
  });
});

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

  it("does not offer retry when TURN_OUTCOME_UNKNOWN prevents a safe replay", async () => {
    mockHooksEmpty();
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

    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
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
    mockHooksEmpty();
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

    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
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
});

// ---------------------------------------------------------------------------
// Stop button — server-side cancellation (bu-ep4ks.2)
// ---------------------------------------------------------------------------

describe("FloatingChatWidget — Stop button", () => {
  /** Drive the widget into an active mid-stream state (Stop button visible,
   * a real conversation id known) by scripting a token event with no
   * trailing `done` — the awaited send call resolves while still "open". */
  async function sendAndEnterStreamingState({
    conversationCreated = true,
    token = true,
  }: { conversationCreated?: boolean; token?: boolean } = {}) {
    mockHooksEmpty();
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [
      ...(conversationCreated
        ? [{ event: "conversation_created", data: { conversation_id: "conv-stop-1", title: "New" } }]
        : []),
      ...(token ? [{ event: "token", data: { content: "partial response" } }] : []),
    ];

    const view = renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    const input = screen.getByPlaceholderText("Type a message...");
    fireEvent.change(input, { target: { value: "hello" } });
    await act(async () => {
      fireEvent.click(screen.getByTitle("Send message"));
    });

    expect(screen.getByTestId("chat-stop-button")).toBeDefined();
    return view;
  }

  it("does not send Stop until the create response proves the durable turn exists", async () => {
    mockHooksEmpty();
    let resolveCreate!: (response: Response) => void;
    createConversationMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveCreate = resolve;
        }),
    );

    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
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
    expect(screen.getByTestId("chat-stop-status").textContent).toBe("This turn was stopped.");
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
    mockHooksEmpty();
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [
      { event: "conversation_created", data: { conversation_id: "conv-stop-route", title: null } },
      { event: "dispatch_accepted", data: { routed_butler: "finance" } },
    ];
    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
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

    expect(screen.getByTestId("chat-stop-status").textContent).toBe("Stopping this turn.");
    expect(screen.queryByTestId("chat-activity-status")).toBeNull();
    expect(screen.queryByRole("link", { name: "finance" })).toBeNull();

    await act(async () => {
      activeSseEventHandler?.({
        event: "error",
        data: { code: "SESSION_CANCELLED", message: "This turn was stopped before routing." },
      });
    });
    expect(screen.getByText("Cancelled by owner")).toBeDefined();
    expect(screen.getByTestId("chat-stop-status").textContent).toBe("This turn was stopped.");
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
    expect(screen.getByTestId("chat-stop-status").textContent).toBe("This turn was stopped.");
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
    expect(screen.getByTestId("chat-stop-status").textContent).toBe("This turn was stopped.");
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
    expect(screen.getByTestId("chat-stop-status").textContent).toBe("Stopping this turn.");
    fireEvent.click(screen.getByTestId("chat-widget-new-button"));

    scriptedEvents = [
      { event: "conversation_created", data: { conversation_id: "conv-stop-2", title: "New" } },
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
    // Stop remains actionable — the failed attempt did not lock the UI up.
    expect(
      (screen.getByTestId("chat-stop-button") as HTMLButtonElement).disabled,
    ).toBe(false);
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
// Global 'c' shortcut (bu-0ynlk.13)
// ---------------------------------------------------------------------------

describe("FloatingChatWidget — global 'c' shortcut (bu-0ynlk.13)", () => {
  it("opens the widget and focuses the composer when closed", () => {
    renderWidget();
    expect(screen.queryByTestId("floating-chat-panel")).toBeNull();

    act(() => {
      window.dispatchEvent(new CustomEvent(OPEN_CHAT_WIDGET_EVENT));
    });

    const panel = screen.getByTestId("floating-chat-panel");
    const input = within(panel).getByPlaceholderText("Type a message...");
    expect(document.activeElement).toBe(input);
  });

  it("refocuses the composer when already open", () => {
    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    const panel = screen.getByTestId("floating-chat-panel");
    const input = within(panel).getByPlaceholderText("Type a message...");

    const closeButton = screen.getByTestId("chat-widget-close-button");
    closeButton.focus();
    expect(document.activeElement).toBe(closeButton);

    act(() => {
      window.dispatchEvent(new CustomEvent(OPEN_CHAT_WIDGET_EVENT));
    });

    expect(document.activeElement).toBe(input);
  });
});

// ---------------------------------------------------------------------------
// Header touch targets + keyboard-viewport-aware height (bu-0ynlk.13)
// ---------------------------------------------------------------------------

describe("FloatingChatWidget — header touch targets and height (bu-0ynlk.13)", () => {
  it("renders header buttons at icon-sm (>=32px) inside a gap-1.5 container", () => {
    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));

    const historyButton = screen.getByTestId("chat-widget-history-button");
    const newButton = screen.getByTestId("chat-widget-new-button");
    const closeButton = screen.getByTestId("chat-widget-close-button");
    for (const button of [historyButton, newButton, closeButton]) {
      expect(button.className).toContain("size-8");
      expect(button.className).not.toContain("size-6");
    }

    const container = closeButton.parentElement as HTMLElement;
    expect(container.className).toContain("gap-1.5");
  });

  it("uses a dvh/visualViewport height, never the fixed 70vh (bu-0ynlk.13)", () => {
    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    const panel = screen.getByTestId("floating-chat-panel");
    expect(panel.className).not.toContain("70vh");
    expect(panel.className).toContain("dvh");
  });
});

// ---------------------------------------------------------------------------
// WCAG 2.2 2.4.11 focus-not-obscured guard (bu-0ynlk.13)
// ---------------------------------------------------------------------------

describe("FloatingChatWidget — focus-not-obscured guard (bu-0ynlk.13)", () => {
  function mockRect(el: HTMLElement, rect: Partial<DOMRect>) {
    vi.spyOn(el, "getBoundingClientRect").mockReturnValue({
      top: 0,
      bottom: 0,
      left: 0,
      right: 0,
      width: 0,
      height: 0,
      x: 0,
      y: 0,
      toJSON: () => {},
      ...rect,
    } as DOMRect);
  }

  function focusBehindPanel() {
    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    const panel = screen.getByTestId("floating-chat-panel");
    mockRect(panel, { top: 100, bottom: 400, left: 100, right: 400 });

    const behind = document.createElement("button");
    behind.textContent = "behind the panel";
    document.body.appendChild(behind);
    mockRect(behind, { top: 150, bottom: 180, left: 150, right: 200 });
    const scrollIntoView = vi.fn();
    behind.scrollIntoView = scrollIntoView;

    fireEvent.focusIn(behind);

    return { scrollIntoView, behind };
  }

  it("scrolls a focused element clear of the panel's footprint", () => {
    const { scrollIntoView, behind } = focusBehindPanel();
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "nearest" });
    behind.remove();
  });

  it("scrolls instantly under prefers-reduced-motion", () => {
    vi.mocked(usePrefersReducedMotion).mockReturnValue(true);
    const { scrollIntoView, behind } = focusBehindPanel();
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "auto", block: "nearest" });
    behind.remove();
  });
});

// ---------------------------------------------------------------------------
// Unread state routed through the shell announcer (bu-0ynlk.13)
// ---------------------------------------------------------------------------

describe("FloatingChatWidget — unread reply announced via shell announcer (bu-0ynlk.13)", () => {
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

  it("announces once when a reply lands while closed, not on every subsequent poll", () => {
    mockConversationTotals("2026-07-05T09:00:00.000Z");
    const { rerenderWidget } = renderWidget();
    expect(announceMock).not.toHaveBeenCalled();

    mockConversationTotals("2026-07-05T09:05:00.000Z");
    rerenderWidget();
    expect(announceMock).toHaveBeenCalledTimes(1);

    // A later poll surfacing the SAME unread state must not re-announce.
    rerenderWidget();
    expect(announceMock).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// axe (bu-0ynlk.13)
// ---------------------------------------------------------------------------

describe("FloatingChatWidget — axe", () => {
  it("has no axe violations on the open widget", async () => {
    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    const panel = screen.getByTestId("floating-chat-panel");
    const results = await axe(panel, { rules: { "color-contrast": { enabled: false } } });
    expect(results).toHaveNoViolations();
  });

  it("has no axe violations on the empty state", async () => {
    mockHooksEmpty();
    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    const panel = screen.getByTestId("floating-chat-panel");
    expect(screen.getByText("New conversation")).toBeDefined();
    const results = await axe(panel, { rules: { "color-contrast": { enabled: false } } });
    expect(results).toHaveNoViolations();
  });
});
