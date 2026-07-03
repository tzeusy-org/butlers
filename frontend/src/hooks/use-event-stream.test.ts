// @vitest-environment jsdom
/**
 * Tests for useEventStream — the multiplexed /api/events/stream hook
 * (bu-86c4c.8, §JARVIS audit move 5).
 *
 * Strategy: mock WebSocket globally, mock the declarative cache-patch
 * registry, and assert that:
 * - A WebSocket is created on mount with the correct URL
 * - status transitions connecting -> open on onopen
 * - status transitions open -> reconnecting on an unexpected close, then
 *   back to open on the next successful connect
 * - Non-snapshot events are routed through applyFleetEvent() and onEvent
 * - Snapshot events replay each buffered event through applyFleetEvent()
 *   and onEvent, not through the cache patch keyed on "snapshot" itself
 * - The api_key query param is appended when provided
 * - The hook closes the socket on unmount and reports status "closed"
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, cleanup, waitFor } from "@testing-library/react";

// ---------------------------------------------------------------------------
// Mock @tanstack/react-query (useQueryClient)
// ---------------------------------------------------------------------------

const mockQueryClient = { __marker: "qc" };

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => mockQueryClient,
}));

// ---------------------------------------------------------------------------
// Mock the declarative registry
// ---------------------------------------------------------------------------

const mockApplyFleetEvent = vi.fn();

vi.mock("@/hooks/event-cache-registry", () => ({
  applyFleetEvent: (...args: unknown[]) => mockApplyFleetEvent(...args),
}));

// ---------------------------------------------------------------------------
// Mock WebSocket
// ---------------------------------------------------------------------------

interface MockWsInstance {
  url: string;
  onopen: ((ev: Event) => void) | null;
  onmessage: ((ev: MessageEvent) => void) | null;
  onerror: ((ev: Event) => void) | null;
  onclose: ((ev: CloseEvent) => void) | null;
  close: ReturnType<typeof vi.fn>;
  simulateOpen(): void;
  simulateMessage(data: unknown): void;
  simulateClose(code?: number): void;
}

const instances: MockWebSocket[] = [];
const wsConstructorSpy = vi.fn();

class MockWebSocket implements MockWsInstance {
  url: string;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  close = vi.fn();

  constructor(url: string) {
    this.url = url;
    wsConstructorSpy(url);
    instances.push(this);
  }

  simulateOpen(): void {
    this.onopen?.({} as Event);
  }

  simulateMessage(data: unknown): void {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }

  simulateClose(code = 1000): void {
    this.onclose?.({ code } as CloseEvent);
  }
}

function getLastWsInstance(): MockWsInstance | null {
  return instances.length > 0 ? instances[instances.length - 1] : null;
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.stubGlobal("WebSocket", MockWebSocket);
  instances.length = 0;
  wsConstructorSpy.mockClear();
  mockApplyFleetEvent.mockClear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Import hook AFTER mocks are in place
// ---------------------------------------------------------------------------

import { useEventStream } from "./use-event-stream";

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useEventStream", () => {
  it("opens a WebSocket to /events/stream on mount", () => {
    renderHook(() => useEventStream());
    expect(wsConstructorSpy).toHaveBeenCalledOnce();
    expect(wsConstructorSpy.mock.calls[0][0]).toContain("/events/stream");
  });

  it("appends api_key param when provided", () => {
    renderHook(() => useEventStream({ apiKey: "mysecret" }));
    expect(wsConstructorSpy.mock.calls[0][0]).toContain("api_key=mysecret");
  });

  it("does not open WebSocket when enabled=false", () => {
    renderHook(() => useEventStream({ enabled: false }));
    expect(wsConstructorSpy).not.toHaveBeenCalled();
  });

  it("starts in 'connecting' status, transitions to 'open' on connect", () => {
    const { result } = renderHook(() => useEventStream());
    expect(result.current.status).toBe("connecting");

    act(() => {
      getLastWsInstance()?.simulateOpen();
    });
    expect(result.current.status).toBe("open");
  });

  it("transitions open -> reconnecting on an unexpected close", () => {
    const { result } = renderHook(() => useEventStream());
    act(() => {
      getLastWsInstance()?.simulateOpen();
    });
    expect(result.current.status).toBe("open");

    act(() => {
      getLastWsInstance()?.simulateClose();
    });
    expect(result.current.status).toBe("reconnecting");
  });

  it("routes non-snapshot events through applyFleetEvent and onEvent", () => {
    const onEvent = vi.fn();
    renderHook(() => useEventStream({ onEvent }));
    const event = { type: "approval", ts: 1, data: { kind: "created", approval_id: "abc" } };
    act(() => {
      getLastWsInstance()?.simulateMessage(event);
    });
    expect(mockApplyFleetEvent).toHaveBeenCalledWith(mockQueryClient, event);
    expect(onEvent).toHaveBeenCalledWith(event);
  });

  it("replays each buffered event in a snapshot through applyFleetEvent and onEvent", () => {
    const onEvent = vi.fn();
    renderHook(() => useEventStream({ onEvent }));
    const bufferedA = { type: "spend", ts: 1, data: { butler: "atlas" } };
    const bufferedB = { type: "session", ts: 2, data: { phase: "started" } };
    act(() => {
      getLastWsInstance()?.simulateMessage({
        type: "snapshot",
        ts: 3,
        events: [bufferedA, bufferedB],
      });
    });
    expect(mockApplyFleetEvent).toHaveBeenCalledWith(mockQueryClient, bufferedA);
    expect(mockApplyFleetEvent).toHaveBeenCalledWith(mockQueryClient, bufferedB);
    expect(onEvent).toHaveBeenCalledWith(bufferedA);
    expect(onEvent).toHaveBeenCalledWith(bufferedB);
    expect(mockApplyFleetEvent).toHaveBeenCalledTimes(2);
  });

  it("does not throw and ignores malformed message payloads", () => {
    renderHook(() => useEventStream());
    expect(() => {
      act(() => {
        getLastWsInstance()?.onmessage?.({ data: "not json" } as MessageEvent);
      });
    }).not.toThrow();
    expect(mockApplyFleetEvent).not.toHaveBeenCalled();
  });

  it("updates lastEventAt on message receipt", () => {
    const { result } = renderHook(() => useEventStream());
    expect(result.current.lastEventAt).toBeNull();
    act(() => {
      getLastWsInstance()?.simulateMessage({ type: "heartbeat", ts: 1, data: {} });
    });
    expect(result.current.lastEventAt).not.toBeNull();
  });

  it("closes the WebSocket on unmount", () => {
    const { unmount } = renderHook(() => useEventStream());
    const ws = getLastWsInstance();
    act(() => {
      unmount();
    });
    // Note: React does not re-render after unmount, so result.current cannot
    // observe the "closed" status transition here — see the disconnect()
    // test below for that assertion while still mounted.
    expect(ws?.close).toHaveBeenCalled();
  });

  it("disconnect() closes the socket and sets status to 'closed'", async () => {
    const { result } = renderHook(() => useEventStream());
    const ws = getLastWsInstance();
    act(() => {
      result.current.disconnect();
    });
    expect(ws?.close).toHaveBeenCalled();
    await waitFor(() => expect(result.current.status).toBe("closed"));
  });
});
