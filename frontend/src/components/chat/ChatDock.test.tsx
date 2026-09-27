// @vitest-environment jsdom
/**
 * ChatDock — degraded recent-conversations note (bu-0ynlk.11 corrections
 * pass, query-coercion gate finding). A failed conversations-list fetch must
 * not resolve to the same "start a new conversation" silence as a genuinely
 * fresh, conversation-free dock.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

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
