// @vitest-environment jsdom
/**
 * Tests for useApprovalsStream — §8.3 WS live events hook.
 *
 * Strategy: mock WebSocket globally, spy on useQueryClient, and assert that:
 * - A WebSocket is created on mount with the correct URL
 * - Snapshot events call onEvent but do NOT trigger cache invalidation
 * - State-transition events (approved/rejected/…) trigger cache invalidation
 * - Ping events are passed to onEvent but do not trigger invalidation
 * - The api_key query param is appended when provided
 * - The hook closes the socket on unmount
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, cleanup } from "@testing-library/react";

// ---------------------------------------------------------------------------
// Mock @tanstack/react-query (useQueryClient)
// ---------------------------------------------------------------------------

const mockInvalidateQueries = vi.fn();
const mockQueryClient = { invalidateQueries: mockInvalidateQueries };

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => mockQueryClient,
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
  /** Test helper: simulate server sending a message */
  simulateMessage(data: unknown): void;
  /** Test helper: simulate close */
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

  simulateMessage(data: unknown): void {
    this.onmessage?.({
      data: JSON.stringify(data),
    } as MessageEvent);
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
  mockInvalidateQueries.mockClear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Import hook AFTER mocks are in place
// ---------------------------------------------------------------------------

import { useApprovalsStream } from "./use-approvals-stream";

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useApprovalsStream", () => {
  it("opens a WebSocket to /api/approvals/stream on mount", () => {
    renderHook(() => useApprovalsStream());
    expect(wsConstructorSpy).toHaveBeenCalledOnce();
    expect(wsConstructorSpy.mock.calls[0][0]).toContain("/approvals/stream");
  });

  it("appends api_key param when provided", () => {
    renderHook(() => useApprovalsStream({ apiKey: "mysecret" }));
    expect(wsConstructorSpy.mock.calls[0][0]).toContain("api_key=mysecret");
  });

  it("does not open WebSocket when enabled=false", () => {
    renderHook(() => useApprovalsStream({ enabled: false }));
    expect(wsConstructorSpy).not.toHaveBeenCalled();
  });

  it("calls onEvent for each received message", () => {
    const onEvent = vi.fn();
    renderHook(() => useApprovalsStream({ onEvent }));
    act(() => {
      getLastWsInstance()?.simulateMessage({ kind: "approved", ts: 1, approval_id: "abc" });
    });
    expect(onEvent).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "approved", approval_id: "abc" }),
    );
  });

  it("invalidates approvals queries on state-transition events", () => {
    renderHook(() => useApprovalsStream());
    act(() => {
      getLastWsInstance()?.simulateMessage({
        kind: "approved",
        ts: 1,
        approval_id: "abc",
        snapshot: false,
      });
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ["approvals", "flat"] }),
    );
    expect(mockInvalidateQueries).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ["approvals", "history"] }),
    );
    expect(mockInvalidateQueries).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ["approvals", "detail", "abc"] }),
    );
  });

  it("does NOT invalidate queries for snapshot events", () => {
    renderHook(() => useApprovalsStream());
    act(() => {
      getLastWsInstance()?.simulateMessage({
        kind: "approved",
        ts: 1,
        approval_id: "abc",
        snapshot: true,
      });
    });
    expect(mockInvalidateQueries).not.toHaveBeenCalled();
  });

  it("does NOT invalidate queries for ping events", () => {
    renderHook(() => useApprovalsStream());
    act(() => {
      getLastWsInstance()?.simulateMessage({ kind: "ping", ts: 1 });
    });
    expect(mockInvalidateQueries).not.toHaveBeenCalled();
  });

  it("closes the WebSocket on unmount", () => {
    const { unmount } = renderHook(() => useApprovalsStream());
    const ws = getLastWsInstance();
    act(() => {
      unmount();
    });
    expect(ws?.close).toHaveBeenCalled();
  });
});
