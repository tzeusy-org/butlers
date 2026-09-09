// @vitest-environment jsdom
/**
 * ChatDock — degraded recent-conversations note (bu-0ynlk.11 corrections
 * pass, query-coercion gate finding). A failed conversations-list fetch must
 * not resolve to the same "start a new conversation" silence as a genuinely
 * fresh, conversation-free dock.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import type { Message } from "@/api/types";

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

function renderDock() {
  return render(
    <MemoryRouter>
      <ChatDock onClose={vi.fn()} />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
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
