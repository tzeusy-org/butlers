// @vitest-environment jsdom
/**
 * Tests for useSettingsConsoleStream — WS /api/settings/stream live ticker.
 *
 * Spec: dashboard-settings-console "Settings Console Live Stream".
 *
 * Strategy: mock WebSocket globally and assert that the hook:
 * - opens a WebSocket to /api/settings/stream on mount
 * - applies a "snapshot" event as a full replace
 * - applies "header_delta" incrementally (shallow-merges header counts)
 * - applies "attention_add" (upsert by kind) and "attention_remove" (drop by kind)
 * - no longer relies on the 30 s poll — header counts update purely from the
 *   stream without any re-fetch
 * - appends the api_key query param when provided
 * - closes the socket on unmount
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, cleanup } from "@testing-library/react";

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
  simulateMessage(data: unknown): void;
  simulateOpen(): void;
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
  wsConstructorSpy.mockReset();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Import hook AFTER mocks are in place
// ---------------------------------------------------------------------------

import {
  useSettingsConsoleStream,
  applyConsoleEvent,
  type ConsoleData,
} from "./use-settings-console-stream";

const SNAPSHOT: ConsoleData = {
  header_counts: {
    active_butlers: 3,
    spend_mtd_usd: 12.5,
    open_approvals: 1,
    models_verified: 4,
    models_total: 6,
  },
  attention: [
    { tone: "red", kind: "approval", text: "1 approval waiting", action_route: "/approvals" },
  ],
  attention_truncated_count: 0,
};

// ---------------------------------------------------------------------------
// Pure reducer tests
// ---------------------------------------------------------------------------

describe("applyConsoleEvent", () => {
  it("snapshot replaces the whole payload", () => {
    const next = applyConsoleEvent(undefined, { type: "snapshot", data: SNAPSHOT });
    expect(next).toEqual(SNAPSHOT);
  });

  it("header_delta shallow-merges header counts", () => {
    const next = applyConsoleEvent(SNAPSHOT, {
      type: "header_delta",
      data: { open_approvals: 5, spend_mtd_usd: 99.9 },
    });
    expect(next?.header_counts.open_approvals).toBe(5);
    expect(next?.header_counts.spend_mtd_usd).toBe(99.9);
    // untouched fields preserved
    expect(next?.header_counts.active_butlers).toBe(3);
    expect(next?.attention).toEqual(SNAPSHOT.attention);
  });

  it("attention_add upserts by kind (no duplicates)", () => {
    const withModel = applyConsoleEvent(SNAPSHOT, {
      type: "attention_add",
      data: { tone: "amber", kind: "model", text: "1 model errored", action_route: "/settings/models" },
    });
    expect(withModel?.attention).toHaveLength(2);

    // adding the same kind again replaces, not duplicates
    const replaced = applyConsoleEvent(withModel, {
      type: "attention_add",
      data: { tone: "red", kind: "model", text: "2 models errored", action_route: "/settings/models" },
    });
    expect(replaced?.attention.filter((i) => i.kind === "model")).toHaveLength(1);
    expect(replaced?.attention.find((i) => i.kind === "model")?.text).toBe("2 models errored");
  });

  it("attention_remove drops the item with the given kind", () => {
    const next = applyConsoleEvent(SNAPSHOT, {
      type: "attention_remove",
      data: { kind: "approval" },
    });
    expect(next?.attention).toHaveLength(0);
  });

  it("ignores delta events before a snapshot exists", () => {
    expect(applyConsoleEvent(undefined, { type: "header_delta", data: { open_approvals: 9 } })).toBeUndefined();
    expect(
      applyConsoleEvent(undefined, {
        type: "attention_add",
        data: { tone: "red", kind: "x", text: "y", action_route: "/z" },
      }),
    ).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Hook tests
// ---------------------------------------------------------------------------

describe("useSettingsConsoleStream", () => {
  it("opens a WebSocket to /api/settings/stream on mount", () => {
    renderHook(() => useSettingsConsoleStream());
    expect(wsConstructorSpy).toHaveBeenCalledOnce();
    expect(wsConstructorSpy.mock.calls[0][0]).toContain("/settings/stream");
  });

  it("does not open WebSocket when enabled=false", () => {
    renderHook(() => useSettingsConsoleStream({ enabled: false }));
    expect(wsConstructorSpy).not.toHaveBeenCalled();
  });

  it("appends api_key param when provided", () => {
    renderHook(() => useSettingsConsoleStream({ apiKey: "mysecret" }));
    expect(wsConstructorSpy.mock.calls[0][0]).toContain("api_key=mysecret");
  });

  it("starts undefined until the first snapshot arrives", () => {
    const { result } = renderHook(() => useSettingsConsoleStream());
    expect(result.current.data).toBeUndefined();
  });

  it("applies snapshot, then header_delta / attention_add / attention_remove incrementally from the stream", () => {
    const { result } = renderHook(() => useSettingsConsoleStream());
    const ws = getLastWsInstance();

    // Full snapshot on connect
    act(() => ws?.simulateMessage({ type: "snapshot", data: SNAPSHOT }));
    expect(result.current.data?.header_counts.open_approvals).toBe(1);

    // header_delta — counts update purely from the stream, no re-fetch / poll
    act(() => ws?.simulateMessage({ type: "header_delta", data: { open_approvals: 4 } }));
    expect(result.current.data?.header_counts.open_approvals).toBe(4);
    expect(result.current.data?.header_counts.active_butlers).toBe(3);

    // attention_add
    act(() =>
      ws?.simulateMessage({
        type: "attention_add",
        data: { tone: "amber", kind: "spend", text: "near ceiling", action_route: "/settings/spend" },
      }),
    );
    expect(result.current.data?.attention.map((i) => i.kind)).toContain("spend");

    // attention_remove
    act(() => ws?.simulateMessage({ type: "attention_remove", data: { kind: "approval" } }));
    expect(result.current.data?.attention.map((i) => i.kind)).not.toContain("approval");
  });

  it("a later snapshot fully replaces prior live state (reconnect resync)", () => {
    const { result } = renderHook(() => useSettingsConsoleStream());
    const ws = getLastWsInstance();
    act(() => ws?.simulateMessage({ type: "snapshot", data: SNAPSHOT }));
    const fresh: ConsoleData = {
      ...SNAPSHOT,
      header_counts: { ...SNAPSHOT.header_counts, open_approvals: 42 },
    };
    act(() => ws?.simulateMessage({ type: "snapshot", data: fresh }));
    expect(result.current.data?.header_counts.open_approvals).toBe(42);
  });

  it("reports connection status transitions", () => {
    const { result } = renderHook(() => useSettingsConsoleStream());
    const ws = getLastWsInstance();
    expect(result.current.status).toBe("connecting");
    act(() => ws?.simulateOpen());
    expect(result.current.status).toBe("open");
  });

  it("closes the WebSocket on unmount", () => {
    const { unmount } = renderHook(() => useSettingsConsoleStream());
    const ws = getLastWsInstance();
    act(() => unmount());
    expect(ws?.close).toHaveBeenCalled();
  });
});
