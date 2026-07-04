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
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
vi.mock("@/api/index.ts", () => ({
  createConversation: (...args: unknown[]) => createConversationMock(...args),
  sendMessage: (...args: unknown[]) => sendMessageMock(...args),
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
    total_input_tokens: 10,
    total_output_tokens: 20,
    total_duration_ms: 500,
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
    total_input_tokens: 5,
    total_output_tokens: 5,
    total_duration_ms: 200,
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

  it("opens the panel and hides the trigger on click", () => {
    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    expect(screen.getByTestId("floating-chat-panel")).toBeDefined();
    expect(screen.queryByTestId("floating-chat-trigger")).toBeNull();
  });

  it("closes the panel and restores the trigger on close click", () => {
    renderWidget();
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    fireEvent.click(screen.getByTestId("chat-widget-close-button"));
    expect(screen.queryByTestId("floating-chat-panel")).toBeNull();
    expect(screen.getByTestId("floating-chat-trigger")).toBeDefined();
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

    expect(createConversationMock.mock.calls[0][1]).toEqual({
      message: "Alice is child-of Bob",
      page_context: {
        route: "/entities/concentration",
        query_params: { predicate: "child-of" },
      },
    });
  });
});

// ---------------------------------------------------------------------------
// Unread-reply badge (bu-p6ey8.4)
// ---------------------------------------------------------------------------

describe("FloatingChatWidget — unread badge", () => {
  function conversationsWithOutputTokens(outputTokens: number): ConversationSummary[] {
    return [{ ...CONVERSATIONS[0], total_output_tokens: outputTokens }, CONVERSATIONS[1]];
  }

  function mockConversationTotals(outputTokens: number) {
    vi.mocked(useConversations).mockReturnValue({
      data: { data: conversationsWithOutputTokens(outputTokens), meta: {} },
      isLoading: false,
    } as unknown as ReturnType<typeof useConversations>);
  }

  it("badges the trigger when a reply lands while the panel is closed, and opening clears it", () => {
    mockConversationTotals(20);
    const { rerenderWidget } = renderWidget();

    // First observation of this conversation just establishes the baseline —
    // no badge yet.
    expect(screen.queryByTestId("chat-widget-unread-badge")).toBeNull();

    // Simulate the ~60s poll surfacing a reply while the panel stayed closed.
    mockConversationTotals(55);
    rerenderWidget();
    expect(screen.getByTestId("chat-widget-unread-badge")).toBeDefined();

    // Opening the panel clears the badge.
    fireEvent.click(screen.getByTestId("floating-chat-trigger"));
    expect(screen.queryByTestId("chat-widget-unread-badge")).toBeNull();
  });

  it("does not badge when nothing changed since the last observation", () => {
    mockConversationTotals(20);
    const { rerenderWidget } = renderWidget();
    expect(screen.queryByTestId("chat-widget-unread-badge")).toBeNull();

    // Re-poll with the exact same totals (no reply arrived).
    mockConversationTotals(20);
    rerenderWidget();
    expect(screen.queryByTestId("chat-widget-unread-badge")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Send-error classification
// ---------------------------------------------------------------------------

describe("FloatingChatWidget — send-error classification", () => {
  it("shows a retryable offline banner on SWITCHBOARD_UNAVAILABLE", async () => {
    mockHooksEmpty();
    sendMessageMock.mockResolvedValue({ ok: true } as Response);
    createConversationMock.mockResolvedValue({ ok: true } as Response);
    scriptedEvents = [
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

    // Retry re-sends the same failed text through the same submit path.
    createConversationMock.mockClear();
    scriptedEvents = [{ event: "done", data: {} }];
    await act(async () => {
      fireEvent.click(screen.getByText("Retry"));
    });
    expect(createConversationMock).toHaveBeenCalledTimes(1);
    // page_context is now attached (bu-p6ey8.4) — default capture (route
    // only, "/" has no query params) since this test renders at "/".
    expect(createConversationMock.mock.calls[0][1]).toEqual({
      message: "hello switchboard",
      page_context: { route: "/" },
    });
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
