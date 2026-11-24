// @vitest-environment jsdom
/**
 * ChatDock — degraded recent-conversations note (bu-0ynlk.11 corrections
 * pass, query-coercion gate finding). A failed conversations-list fetch must
 * not resolve to the same "start a new conversation" silence as a genuinely
 * fresh, conversation-free dock.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act as archiveAct, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useLocation, MemoryRouter } from "react-router";

import type { Message } from "@/api/types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

vi.mock("@/hooks/use-conversations.ts", () => ({
  useConversations: vi.fn(),
  useConversationMessages: vi.fn(),
}));
vi.mock("@/hooks/use-pricing-map.ts", () => ({ usePricingMap: vi.fn() }));
vi.mock("@/hooks/use-conversation-turn.ts", () => ({ useConversationTurn: vi.fn() }));

import { ChatDock } from "./ChatDock";
import { useConversations, useConversationMessages } from "@/hooks/use-conversations.ts";
import { usePricingMap } from "@/hooks/use-pricing-map.ts";
import { useConversationTurn } from "@/hooks/use-conversation-turn.ts";


import { conversationKeys as archiveKeys } from "@/hooks/conversation-keys.ts";
import * as archiveApi from "@/api/index.ts";
import * as archiveDraft from "@/hooks/use-draft";
import type { UseConversationTurnResult } from "@/hooks/use-conversation-turn.ts";
import type { ConversationSummary } from "@/api/types.ts";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

function mockTurn(visibleMessages: Message[] = []) {
  vi.mocked(useConversationTurn).mockReturnValue({
    streaming: null,
    visibleMessages,
    sendError: null,
    setSendError: vi.fn(),
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

function renderDock() {
  return render(
    <MemoryRouter>
      <ChatDock onClose={vi.fn()} />
    </MemoryRouter>,
    { wrapper: QueryWrapper },
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.localStorage.clear();
});

describe("ChatDock — degraded recent-conversations note (bu-0ynlk.11)", () => {
  it("names the degraded conversations source instead of a silent empty resume", () => {
    vi.mocked(useConversations).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as AnyMock);
    vi.mocked(useConversationMessages).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as AnyMock);
    vi.mocked(usePricingMap).mockReturnValue({ data: null } as AnyMock);
    mockTurn([]);

    renderDock();

    expect(screen.getByTestId("chat-dock-conversations-degraded")).toBeTruthy();
  });

  it("does not render the degraded note for a genuinely empty (not errored) conversations list", () => {
    vi.mocked(useConversations).mockReturnValue({
      data: { data: [] },
      isLoading: false,
      isError: false,
    } as AnyMock);
    vi.mocked(useConversationMessages).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as AnyMock);
    vi.mocked(usePricingMap).mockReturnValue({ data: null } as AnyMock);
    mockTurn([]);

    renderDock();

    expect(screen.queryByTestId("chat-dock-conversations-degraded")).toBeNull();
  });
});

describe("ChatDock — resize handle (bu-pnijwc)", () => {
  const DOCK_WIDTH_KEY = "butlers.chat-dock-width";

  function mockHappyPath() {
    vi.mocked(useConversations).mockReturnValue({
      data: { data: [] },
      isLoading: false,
      isError: false,
    } as AnyMock);
    vi.mocked(useConversationMessages).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as AnyMock);
    vi.mocked(usePricingMap).mockReturnValue({ data: null } as AnyMock);
    mockTurn([]);
  }

  function getSeparator() {
    return screen.getByRole("separator", { name: "Resize chat dock" });
  }

  function dockWidthPx() {
    return (screen.getByTestId("chat-dock") as HTMLElement).style.width;
  }

  // jsdom does not implement pointer capture; ChatDock's mouse-drag path
  // calls `setPointerCapture` unconditionally on pointerdown, so dispatching
  // a real PointerEvent would otherwise throw inside the handler.
  function firePointer(target: EventTarget, type: string, clientX: number) {
    act(() => {
      target.dispatchEvent(
        new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 1, clientX }),
      );
    });
  }

  beforeEach(() => {
    if (typeof Element.prototype.setPointerCapture !== "function") {
      Element.prototype.setPointerCapture = () => {};
    }
    Object.defineProperty(window, "innerWidth", { value: 1000, writable: true });
    mockHappyPath();
  });

  it("resizes with ArrowLeft/ArrowRight and clamps to the 360-560 range", () => {
    renderDock();
    const separator = getSeparator();

    expect(dockWidthPx()).toBe("400px");

    fireEvent.keyDown(separator, { key: "ArrowLeft" });
    expect(dockWidthPx()).toBe("416px");

    fireEvent.keyDown(separator, { key: "ArrowRight" });
    fireEvent.keyDown(separator, { key: "ArrowRight" });
    expect(dockWidthPx()).toBe("384px");

    for (let i = 0; i < 20; i++) fireEvent.keyDown(separator, { key: "ArrowLeft" });
    expect(dockWidthPx()).toBe("560px");

    for (let i = 0; i < 20; i++) fireEvent.keyDown(separator, { key: "ArrowRight" });
    expect(dockWidthPx()).toBe("360px");
  });

  it("ignores keys other than ArrowLeft/ArrowRight on the separator", () => {
    renderDock();
    const separator = getSeparator();

    fireEvent.keyDown(separator, { key: "Enter" });
    fireEvent.keyDown(separator, { key: "Tab" });

    expect(dockWidthPx()).toBe("400px");
  });

  it("resizes by dragging and persists the final width on pointerup", () => {
    renderDock();
    const separator = getSeparator();

    firePointer(separator, "pointerdown", 600);
    firePointer(window, "pointermove", 476); // 1000 - 476 = 524
    expect(dockWidthPx()).toBe("524px");

    firePointer(window, "pointermove", 700); // 1000 - 700 = 300 -> clamp to 360
    expect(dockWidthPx()).toBe("360px");

    firePointer(window, "pointerup", 700);
    expect(window.localStorage.getItem(DOCK_WIDTH_KEY)).toBe("360");
  });

  it("clamps drag resize at the maximum width and persists it", () => {
    renderDock();
    const separator = getSeparator();

    firePointer(separator, "pointerdown", 600);
    firePointer(window, "pointermove", 100); // 1000 - 100 = 900 -> clamp to 560
    expect(dockWidthPx()).toBe("560px");

    firePointer(window, "pointerup", 100);
    expect(window.localStorage.getItem(DOCK_WIDTH_KEY)).toBe("560");
  });

  it("only tracks pointermove between pointerdown and pointerup", () => {
    renderDock();
    const separator = getSeparator();

    // No pointerdown yet: pointermove on window must not resize.
    firePointer(window, "pointermove", 500);
    expect(dockWidthPx()).toBe("400px");

    firePointer(separator, "pointerdown", 600);
    firePointer(window, "pointermove", 476); // 1000 - 476 = 524
    expect(dockWidthPx()).toBe("524px");

    firePointer(window, "pointerup", 476);
    firePointer(window, "pointermove", 300); // after pointerup: must not resize
    expect(dockWidthPx()).toBe("524px");
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
  const draft = vi.spyOn(archiveDraft, "useDraft");
  return { client, list, conversation, patch, resolvePatch, stop, detach, reset, send, turn, draft,
    start: () => setRunning(true),
    dispose: () => { resolvePatch(); lookup.mockRestore(); patchSpy.mockRestore(); turn.mockRestore(); draft.mockRestore(); client.clear(); },
  };
}

describe("Archive preserves the running posture (bu-0ynlk.25)", () => {
  it("archives a running selected conversation without losing its draft, turn or owner", async () => {
    const fixture = runningArchiveFixture("switchboard");
    function Surface() {
      const location = useLocation();
      return <><output data-testid="archive-route">{location.pathname}</output><ChatDock onClose={vi.fn()} /></>;
    }
    const mount = (client: QueryClient, path = "/") => render(<MemoryRouter initialEntries={[path]}><QueryClientProvider client={client}><Surface /></QueryClientProvider></MemoryRouter>);
    const view = mount(fixture.client);
    try {

      const input = screen.getByPlaceholderText("Type a message...") as HTMLTextAreaElement;
      await waitFor(() => expect(input.disabled).toBe(false));
      await waitFor(() => expect(fixture.turn.mock.lastCall?.[0]).toMatchObject({ butlerName: "switchboard", activeConversationId: "conv-1" }));
      fireEvent.change(input, { target: { value: "half-written thought" } });
      await archiveAct(async () => fixture.start());
      const stop = screen.getByRole("button", { name: "Stop this turn" });
      expect(stop.hasAttribute("disabled")).toBe(false);
      expect(screen.getByText("Original saved message")).toBeTruthy();
      expect(screen.getByText("Unfinished partial answer")).toBeTruthy();
      expect(fixture.turn.mock.lastCall?.[0]).toMatchObject({ butlerName: "switchboard", activeConversationId: "conv-1" });
      expect(fixture.draft.mock.lastCall?.[1]).toEqual(["switchboard", "conv-1"]);
      const baseline = [fixture.send.mock.calls.length, fixture.stop.mock.calls.length, fixture.detach.mock.calls.length, fixture.reset.mock.calls.length];
      const header = screen.getAllByRole("button", { name: "Actions for Existing thread (switchboard)" }).at(-1)!;
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
        expect(screen.getByTestId("archive-route").textContent).toBe("/");
        expect(fixture.turn.mock.lastCall?.[0]).toMatchObject({ butlerName: "switchboard", activeConversationId: "conv-1" });
        expect(fixture.draft.mock.lastCall?.[1]).toEqual(["switchboard", "conv-1"]);
        expect([fixture.send.mock.calls.length, fixture.stop.mock.calls.length, fixture.detach.mock.calls.length, fixture.reset.mock.calls.length]).toEqual(baseline);
        expect(fixture.client.getQueryData(archiveKeys.byId("conv-1"))).toMatchObject({ status: "archived" });
        expect(fixture.client.getQueryData<{data: ConversationSummary[]}>(fixture.list)?.data.some((row) => row.id === "conv-1")).toBe(false);
        expect(screen.getByText(/Sending a message makes it active again/)).toBeTruthy();
      };
      conserved(); // optimistic state while PATCH is genuinely pending
      await archiveAct(async () => fixture.resolvePatch());
      await waitFor(() => expect(screen.getByRole("button", { name: "Unarchive" }).hasAttribute("disabled")).toBe(false));
      conserved(); // confirmed cache reconciliation, still the same unfinished turn
      expect(fixture.patch.mock.calls).toEqual([["switchboard", "conv-1", {status: "archived"}]]);

      fireEvent.click(stop);
      await waitFor(() => expect(fixture.stop).toHaveBeenCalledTimes(baseline[1] + 1));
      expect(screen.queryByRole("button", {name: "Stop this turn"})).toBeNull();
      expect(input.value).toBe("half-written thought");
    } finally { view.unmount(); fixture.dispose(); }
    // Fresh mount with no active positive companion must remain a new thread.
    const archivedOnly = runningArchiveFixture("switchboard", true);
    const fresh = mount(archivedOnly.client, "/");
    try {

      await waitFor(() => expect(screen.getByText("New conversation")).toBeTruthy());
      expect(archivedOnly.turn.mock.lastCall?.[0].activeConversationId).toBeNull();
      expect(archivedOnly.patch).not.toHaveBeenCalled();
    } finally { fresh.unmount(); archivedOnly.dispose(); }
  });
});
