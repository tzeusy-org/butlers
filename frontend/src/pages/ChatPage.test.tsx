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
import { act as archiveAct, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useLocation, MemoryRouter, Route, Routes } from "react-router";
import { useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";

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


import { conversationKeys as archiveKeys } from "@/hooks/conversation-keys.ts";
import * as archiveApi from "@/api/index.ts";
import * as archiveDraft from "@/hooks/use-draft";
import type { UseConversationTurnResult } from "@/hooks/use-conversation-turn.ts";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

function makeConversation(
  overrides: Partial<ConversationSummary> = {},
): ConversationSummary {
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

// The shared header's rename/archive actions read the query client.
function QueryWrapper({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }));
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function renderPage(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/chat/:conversationId" element={<ChatPage />} />
      </Routes>
    </MemoryRouter>,
    { wrapper: QueryWrapper },
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


// REQ-dashboard-chat-ui-017: keep the real metadata/cache owner; control only
// the unfinished turn boundary. Storage recovery runs in the browser companion.
function runningArchiveFixture(owner: string, allArchived = false) {
  vi.mocked(usePricingMap).mockImplementation(function useArchivePricing() {
    return useQuery({ queryKey: ["pricing-map"], queryFn: async () => ({}) });
  });
  const conversation: ConversationSummary = {
    id: "conv-1", butler_name: owner, title: "Existing thread", status: "active",
    created_at: "2026-07-03T12:00:00.000Z", updated_at: "2026-07-04T12:00:00.000Z",
    message_count: 1, routed_butler: null,
  };
  let server = { ...conversation, status: allArchived ? "archived" as const : "active" as const };
  const archived: ConversationSummary = { ...conversation, id: "archived-first", title: "Earlier archived thread", status: "archived" };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const list = archiveKeys.list(owner);
  // A cached archived-first row must never win automatic resume.
  client.setQueryData(list, { data: allArchived ? [server] : [archived, server], meta: {} });
  client.setQueryData(archiveKeys.byId(conversation.id), server);
  vi.mocked(useConversations).mockImplementation(function useArchiveList(butler, params) {
    return useQuery({ queryKey: archiveKeys.list(butler, params), queryFn: async () => ({
      data: butler === owner && (params?.status === "archived" ? server.status === "archived" : server.status === "active") ? [server] : [], meta: {},
    }), staleTime: Infinity });
  });
  const history: Message = {
    id: "saved-message", conversation_id: conversation.id, role: "user", content: "Original saved message",
    tool_calls: null, error: null, model: null, input_tokens: null, output_tokens: null,
    duration_ms: null, session_id: null, request_id: null, created_at: conversation.created_at,
  };
  vi.mocked(useConversationMessages).mockImplementation(function useArchiveMessages(butler, id) {
    return useQuery({ queryKey: archiveKeys.messages(butler, id ?? ""), queryFn: async () => ({ data: id === conversation.id ? [history] : [], meta: {} }) });
  });
  const lookup = vi.spyOn(archiveApi, "getConversationById").mockImplementation(async (id) => {
    if (id !== conversation.id) throw new Error("Unexpected fixture identity");
    return server;
  });
  let resolvePatch = () => {};
  const pending = new Promise<void>((resolve) => { resolvePatch = resolve; });
  const patch = vi.fn(async (butler: string, id: string, body: { status?: "active" | "archived"; title?: string }) => {
    expect([butler, id, body]).toEqual([owner, conversation.id, { status: "archived" }]);
    await pending;
    server = { ...server, status: "archived", updated_at: "2026-07-05T12:00:00.000Z" };
    return server;
  });
  const patchSpy = vi.spyOn(archiveApi, "updateConversation").mockImplementation(patch);
  let setRunning: (value: boolean) => void = () => {};
  const stop = vi.fn(async () => { setRunning(false); });
  const detach = vi.fn(() => { setRunning(false); });
  const reset = vi.fn(() => { setRunning(false); });
  const send = vi.fn(async () => {});
  const turn = vi.mocked(useConversationTurn).mockImplementation(function useArchiveTurn(options): UseConversationTurnResult {
    const [running, updateRunning] = useState(false);
    setRunning = updateRunning;
    const selected = options.butlerName === owner && options.activeConversationId === conversation.id;
    const active = selected && running;
    return {
      streaming: active ? { conversationId: conversation.id, messageId: "unfinished-message", content: "Unfinished partial answer", pending: false, interrupted: false, stopReady: true } : null,
      visibleMessages: selected ? [history] : [], sendError: null, setSendError: vi.fn(),
      isStreaming: active, hasActiveRuntime: active,
      contextPreview: { policy: "snapshot", label: "Chat", context: null }, includeContext: true,
      toggleIncludeContext: vi.fn(), sendText: send, handleStop: stop, abandonCurrentStream: detach, resetTurnState: reset,
    };
  });
  vi.mocked(useConversationById).mockImplementation(function useArchiveIdentity(id) {
    return useQuery({ queryKey: archiveKeys.byId(id ?? ""), queryFn: () => archiveApi.getConversationById(id!), enabled: !!id, staleTime: Infinity });
  });
  const draft = vi.spyOn(archiveDraft, "useDraft");
  return { client, list, conversation, patch, resolvePatch, stop, detach, reset, send, turn, draft,
    start: () => setRunning(true),
    dispose: () => { resolvePatch(); lookup.mockRestore(); patchSpy.mockRestore(); turn.mockRestore(); draft.mockRestore(); client.clear(); },
  };
}

describe("Archive preserves the running posture (bu-0ynlk.25)", () => {
  it("archives a running selected conversation without losing its draft, turn or owner", async () => {
    const fixture = runningArchiveFixture("general");
    function Surface() {
      const location = useLocation();
      return <><output data-testid="archive-route">{location.pathname}</output><Routes><Route path="/chat" element={<ChatPage />} /><Route path="/chat/:conversationId" element={<ChatPage />} /></Routes></>;
    }
    const mount = (client: QueryClient, path = "/chat/conv-1") => render(<MemoryRouter initialEntries={[path]}><QueryClientProvider client={client}><Surface /></QueryClientProvider></MemoryRouter>);
    const view = mount(fixture.client);
    try {

      const input = screen.getByPlaceholderText("Type a message...") as HTMLTextAreaElement;
      await waitFor(() => expect(input.disabled).toBe(false));
      await waitFor(() => expect(fixture.turn.mock.lastCall?.[0]).toMatchObject({ butlerName: "general", activeConversationId: "conv-1" }));
      fireEvent.change(input, { target: { value: "half-written thought" } });
      await archiveAct(async () => fixture.start());
      const stop = screen.getByRole("button", { name: "Stop this turn" });
      expect(stop.hasAttribute("disabled")).toBe(false);
      expect(screen.getByText("Original saved message")).toBeTruthy();
      expect(screen.getByText("Unfinished partial answer")).toBeTruthy();
      expect(fixture.turn.mock.lastCall?.[0]).toMatchObject({ butlerName: "general", activeConversationId: "conv-1" });
      expect(fixture.draft.mock.lastCall?.[1]).toEqual(["general", "conv-1"]);
      const baseline = [fixture.send.mock.calls.length, fixture.stop.mock.calls.length, fixture.detach.mock.calls.length, fixture.reset.mock.calls.length];
      const header = screen.getAllByRole("button", { name: "Actions for Existing thread (general)" }).at(-1)!;
      fireEvent.keyDown(header, { key: "Enter" });
      fireEvent.click(screen.getByRole("menuitem", { name: "Archive conversation" }));
      await waitFor(() => expect(fixture.patch).toHaveBeenCalledTimes(1));
      const conserved = () => {
        expect([fixture.send.mock.calls.length, fixture.stop.mock.calls.length, fixture.detach.mock.calls.length, fixture.reset.mock.calls.length]).toEqual(baseline);
        expect(screen.getByPlaceholderText("Type a message...")).toBe(input);
        expect(input.value).toBe("half-written thought");
        expect(screen.getByRole("button", { name: "Stop this turn" })).toBe(stop);
        expect(stop.hasAttribute("disabled")).toBe(false);
        expect(screen.getByText("Original saved message")).toBeTruthy();
        expect(screen.getByText("Unfinished partial answer")).toBeTruthy();
        expect(screen.getByTestId("archive-route").textContent).toBe("/chat/conv-1");
        expect(fixture.turn.mock.lastCall?.[0]).toMatchObject({ butlerName: "general", activeConversationId: "conv-1" });
        expect(fixture.draft.mock.lastCall?.[1]).toEqual(["general", "conv-1"]);
        expect([fixture.send.mock.calls.length, fixture.stop.mock.calls.length, fixture.detach.mock.calls.length, fixture.reset.mock.calls.length]).toEqual(baseline);
        expect(fixture.client.getQueryData(archiveKeys.byId("conv-1"))).toMatchObject({ status: "archived" });
        expect(fixture.client.getQueryData<{data: ConversationSummary[]}>(fixture.list)?.data.some((row) => row.id === "conv-1")).toBe(false);
        expect(screen.getByText(/Sending a message makes it active again/)).toBeTruthy();
      };
      conserved(); // optimistic state while PATCH is genuinely pending
      await archiveAct(async () => fixture.resolvePatch());
      await waitFor(() => expect(screen.getByRole("button", { name: "Unarchive" }).hasAttribute("disabled")).toBe(false));
      conserved(); // confirmed cache reconciliation, still the same unfinished turn
      expect(fixture.patch.mock.calls).toEqual([["general", "conv-1", {status: "archived"}]]);

      fireEvent.click(stop);
      await waitFor(() => expect(fixture.stop).toHaveBeenCalledTimes(baseline[1] + 1));
      expect(screen.queryByRole("button", {name: "Stop this turn"})).toBeNull();
      expect(input.value).toBe("half-written thought");
    } finally { view.unmount(); fixture.dispose(); }
    // Fresh mount with no active positive companion must remain a new thread.
    const archivedOnly = runningArchiveFixture("general", true);
    const fresh = mount(archivedOnly.client, "/chat");
    try {

      await waitFor(() => expect(screen.getByText("New conversation")).toBeTruthy());
      expect(archivedOnly.turn.mock.lastCall?.[0].activeConversationId).toBeNull();
      expect(archivedOnly.patch).not.toHaveBeenCalled();
    } finally { fresh.unmount(); archivedOnly.dispose(); }
  });
});
