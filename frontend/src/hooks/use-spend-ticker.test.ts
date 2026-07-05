// @vitest-environment jsdom
/**
 * Tests for useSpendTicker (bu-qvnce.14 slice 2 -- replaces use-spend-stream's
 * bespoke WebSocket with a subscription to the shared EventBusProvider).
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

type CapturedListener =
  | ((event: { type: string; ts: number; data: Record<string, unknown> }, meta: { replayed: boolean }) => void)
  | null;

let capturedListener: CapturedListener = null;
let capturedType: string | null = null;

vi.mock("@/lib/event-bus", () => ({
  useBusEvent: (type: string, listener: CapturedListener) => {
    capturedType = type;
    capturedListener = listener;
  },
}));

afterEach(() => {
  capturedListener = null;
  capturedType = null;
});

import { useSpendTicker } from "./use-spend-ticker";

describe("useSpendTicker", () => {
  it("subscribes to the 'spend' bus event type", () => {
    renderHook(() => useSpendTicker());
    expect(capturedType).toBe("spend");
  });

  it("starts at 0", () => {
    const { result } = renderHook(() => useSpendTicker());
    expect(result.current.streamedCostUsd).toBe(0);
  });

  it("accumulates cost_usd from live (non-replayed) call events", () => {
    const { result } = renderHook(() => useSpendTicker());

    act(() => {
      capturedListener?.(
        { type: "spend", ts: 1, data: { kind: "call", cost_usd: 0.5 } },
        { replayed: false },
      );
    });
    expect(result.current.streamedCostUsd).toBeCloseTo(0.5);

    act(() => {
      capturedListener?.(
        { type: "spend", ts: 2, data: { kind: "call", cost_usd: 1.25 } },
        { replayed: false },
      );
    });
    expect(result.current.streamedCostUsd).toBeCloseTo(1.75);
  });

  it("ignores replayed (snapshot) events -- already in the baseline", () => {
    const { result } = renderHook(() => useSpendTicker());

    act(() => {
      capturedListener?.(
        { type: "spend", ts: 1, data: { kind: "call", cost_usd: 5 } },
        { replayed: true },
      );
    });
    expect(result.current.streamedCostUsd).toBe(0);
  });

  it("ignores non-call spend payloads (e.g. a stray ping-shaped event)", () => {
    const { result } = renderHook(() => useSpendTicker());

    act(() => {
      capturedListener?.({ type: "spend", ts: 1, data: { kind: "ping" } }, { replayed: false });
    });
    expect(result.current.streamedCostUsd).toBe(0);
  });

  it("treats a missing/non-numeric cost_usd as 0 rather than NaN-poisoning the total", () => {
    const { result } = renderHook(() => useSpendTicker());

    act(() => {
      capturedListener?.({ type: "spend", ts: 1, data: { kind: "call" } }, { replayed: false });
    });
    expect(result.current.streamedCostUsd).toBe(0);

    act(() => {
      capturedListener?.(
        { type: "spend", ts: 2, data: { kind: "call", cost_usd: 2 } },
        { replayed: false },
      );
    });
    expect(result.current.streamedCostUsd).toBe(2);
  });
});
