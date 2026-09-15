// @vitest-environment jsdom
/**
 * useConversationTurn — the shared send/stream/stop/retry state machine
 * extracted from ChatPanel.tsx and FloatingChatWidget.tsx (bu-0ynlk.11).
 *
 * Exhaustive edge-case coverage of this exact reducer already exists at the
 * component seam (ChatPanel.test.tsx, FloatingChatWidget.test.tsx — Stop
 * races, send-error classification, dispatch receipts, etc.); this file
 * pins the hook's own six transitions in isolation, per the bead's
 * acceptance criteria, so a future edit to the hook fails fast without
 * requiring the full component suites.
 */

import { useMemo, useState } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

import { PageContextProvider } from "@/lib/page-context.tsx";
import type { Message } from "@/api/types.ts";
import { optimisticUserMessageId } from "@/components/chat/message-reconciliation.ts";
import { useConversationTurn } from "./use-conversation-turn.ts";

// ---------------------------------------------------------------------------
// Mocks — same approach as ChatPanel.test.tsx / FloatingChatWidget.test.tsx:
// consumeSseStream synchronously replays a scripted event queue.
// ---------------------------------------------------------------------------

const createConversationMock = vi.fn();
const sendMessageMock = vi.fn();
const cancelConversationMessageTurnMock = vi.fn();
const getConversationMessagesMock = vi.fn();
vi.mock("@/api/index.ts", () => ({
  createConversation: (...args: unknown[]) => createConversationMock(...args),
  sendMessage: (...args: unknown[]) => sendMessageMock(...args),
  cancelConversationMessageTurn: (...args: unknown[]) =>
    cancelConversationMessageTurnMock(...args),
  // bu-0ynlk.7's non-abort stream-failure refetch recovery, folded into this
  // hook during the bu-0ynlk.11 rebase -- exhaustive coverage of that path
  // lives at the component seam (FloatingChatWidget.test.tsx), same as the
  // rest of this reducer; this stub only keeps the import resolvable.
  getConversationMessages: (...args: unknown[]) => getConversationMessagesMock(...args),
}));

let scriptedEvents: Array<{ event: string; data: unknown }> = [];
vi.mock("@/components/chat/sse-utils.ts", () => ({
  consumeSseStream: async (
    _response: Response,
    onEvent: (event: { event: string; data: unknown }) => void,
  ) => {
    for (const evt of scriptedEvents) onEvent(evt);
  },
}));

// ---------------------------------------------------------------------------
// Harness — the hook needs a caller-owned activeConversationId; this wraps
// it in ordinary useState the way ChatPanel/FloatingChatWidget do.
// ---------------------------------------------------------------------------

function useHarness() {
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  // A stable reference, matching how TanStack Query keeps `data` referentially
  // stable across renders until a real refetch completes -- an inline object
  // literal here would re-trigger the hook's messagesData-sync effect (and
  // its setState) on every render, an infinite loop this harness must not
  // introduce.
  const messagesData = useMemo(() => ({ data: [] as Message[] }), []);
  const turn = useConversationTurn({
    butlerName: "switchboard",
    activeConversationId,
    setActiveConversationId,
    messagesData,
  });
  return { ...turn, activeConversationId };
}

function renderTurn() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");
  const view = renderHook(() => useHarness(), {
    wrapper: ({ children }) => (
      <MemoryRouter initialEntries={["/"]}>
        <QueryClientProvider client={queryClient}>
          <PageContextProvider>{children}</PageContextProvider>
        </QueryClientProvider>
      </MemoryRouter>
    ),
  });
  return { ...view, invalidateQueries };
}

beforeEach(() => {
  scriptedEvents = [];
  createConversationMock.mockReset().mockResolvedValue({ ok: true });
  sendMessageMock.mockReset().mockResolvedValue({ ok: true });
  cancelConversationMessageTurnMock.mockReset();
  getConversationMessagesMock.mockReset().mockResolvedValue({ data: [] });
});

describe("useConversationTurn", () => {
  it("send: creates a new conversation and adds an optimistic user bubble", async () => {
    const { result } = renderTurn();

    await act(async () => {
      await result.current.sendText("hello there");
    });

    expect(createConversationMock).toHaveBeenCalledWith(
      "switchboard",
      expect.objectContaining({ message: "hello there" }),
      expect.anything(),
    );
    expect(result.current.visibleMessages).toHaveLength(1);
    expect(result.current.visibleMessages[0]).toMatchObject({
      role: "user",
      content: "hello there",
    });
    // No scripted events means consumeSseStream returns before "done" —
    // the turn is left pending and Stop-ready, same as real mid-stream state.
    expect(result.current.streaming).toMatchObject({ pending: true, stopReady: true });
  });

  it("token: accumulates streamed content and clears the pending flag", async () => {
    scriptedEvents = [{ event: "token", data: { content: "Hi" } }];
    const { result } = renderTurn();

    await act(async () => {
      await result.current.sendText("hello");
    });

    expect(result.current.streaming).toMatchObject({ content: "Hi", pending: false });
  });

  it("complete: clears streaming and invalidates the conversation caches", async () => {
    // message_complete's cache-invalidation branch needs a known conversation
    // id -- conversation_created supplies it within the same turn, same as a
    // real new-conversation send.
    scriptedEvents = [
      { event: "conversation_created", data: { conversation_id: "c1", title: null } },
      {
        event: "message_complete",
        data: { message_id: "m1", tool_calls: [], sources: [] },
      },
    ];
    const { result, invalidateQueries } = renderTurn();

    await act(async () => {
      await result.current.sendText("hello");
    });

    expect(result.current.streaming).toBeNull();
    expect(invalidateQueries).toHaveBeenCalled();
  });

  it("error: classifies a non-cancellation SSE error and surfaces it as sendError", async () => {
    scriptedEvents = [
      { event: "error", data: { code: "SWITCHBOARD_UNAVAILABLE", message: "Switchboard offline" } },
    ];
    const { result } = renderTurn();

    await act(async () => {
      await result.current.sendText("hello");
    });

    expect(result.current.streaming).toBeNull();
    expect(result.current.sendError).toMatchObject({ kind: "offline", message: "Switchboard offline" });
  });

  it("stop: cancels the durable turn and renders the confirmed cancellation", async () => {
    const { result } = renderTurn();

    // No scripted events -> the turn stays pending + stopReady (see the
    // "send" case above), which is a precondition for handleStop to act.
    await act(async () => {
      await result.current.sendText("hello");
    });
    expect(result.current.streaming?.stopReady).toBe(true);

    cancelConversationMessageTurnMock.mockResolvedValue({
      cancelled: true,
      already_finished: false,
      conversation_id: "c1",
    });

    await act(async () => {
      await result.current.handleStop();
    });

    await waitFor(() => {
      expect(result.current.streaming?.cancelled).toBe(true);
    });
  });

  it("retry: reusing the same messageId keeps one optimistic bubble, not a duplicate", async () => {
    const { result } = renderTurn();

    await act(async () => {
      await result.current.sendText("failed text", "retry-id-1");
    });
    expect(result.current.visibleMessages).toHaveLength(1);
    expect(result.current.visibleMessages[0].id).toBe(optimisticUserMessageId("retry-id-1"));

    await act(async () => {
      await result.current.sendText("failed text", "retry-id-1");
    });

    expect(result.current.visibleMessages).toHaveLength(1);
    expect(sendMessageMock).not.toHaveBeenCalled();
    expect(createConversationMock).toHaveBeenCalledTimes(2);
  });
});
