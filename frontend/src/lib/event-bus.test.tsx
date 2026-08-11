// @vitest-environment jsdom
/**
 * Tests for EventBusProvider / useEventBus / useBusEvent (bu-qvnce.14 slice 1).
 *
 * Strategy: mock useEventStream so we control exactly when/what onEvent
 * fires, then assert that:
 * - subscribe(type, cb) delivers only events matching that type
 * - multiple listeners for the same type all fire
 * - replayed vs live metadata is forwarded through unchanged
 * - unsubscribing (effect cleanup / unmount) stops further delivery
 * - useEventBus()/useBusEvent() throw outside a provider (wiring-bug guard)
 * - status/lastEventAt pass through from the underlying useEventStream
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act, cleanup } from "@testing-library/react";
import type { ReactNode } from "react";
import type { EventBusHealth } from "@/hooks/use-event-stream";

// ---------------------------------------------------------------------------
// Mock useEventStream — capture the onEvent callback so tests can drive it
// directly instead of standing up a real WebSocket.
// ---------------------------------------------------------------------------

type CapturedEvent = { type: string; ts: number; data: Record<string, unknown> };
type CapturedMeta = { replayed: boolean };

let capturedOnEvent: ((event: CapturedEvent, meta: CapturedMeta) => void) | null = null;
let mockHealth: EventBusHealth = "healthy";

const mockUseEventStream = vi.fn((opts?: { onEvent?: typeof capturedOnEvent }) => {
  capturedOnEvent = opts?.onEvent ?? null;
  return { status: "open", health: mockHealth, lastEventAt: 123, disconnect: vi.fn() };
});

vi.mock("@/hooks/use-event-stream", () => ({
  useEventStream: (opts?: unknown) => mockUseEventStream(opts as never),
}));

afterEach(() => {
  cleanup();
  capturedOnEvent = null;
  mockHealth = "healthy";
});

// ---------------------------------------------------------------------------
// Import AFTER mocks are in place
// ---------------------------------------------------------------------------

import { EventBusProvider, useEventBus, useBusEvent } from "./event-bus";

function wrapper({ children }: { children: ReactNode }) {
  return <EventBusProvider>{children}</EventBusProvider>;
}

describe("EventBusProvider / useEventBus", () => {
  it("exposes status, health, and lastEventAt from the underlying useEventStream", () => {
    mockHealth = "late";
    const { result } = renderHook(() => useEventBus(), { wrapper });
    expect(result.current.status).toBe("open");
    expect(result.current.health).toBe("late");
    expect(result.current.lastEventAt).toBe(123);
  });

  it("throws when used outside an EventBusProvider", () => {
    expect(() => renderHook(() => useEventBus())).toThrow(/EventBusProvider/);
  });

  it("delivers an event only to listeners subscribed to its type", () => {
    const spendListener = vi.fn();
    const approvalListener = vi.fn();
    renderHook(
      () => {
        useBusEvent("spend", spendListener);
        useBusEvent("approval", approvalListener);
      },
      { wrapper },
    );

    act(() => {
      capturedOnEvent?.({ type: "spend", ts: 1, data: { cost_usd: 1 } }, { replayed: false });
    });

    expect(spendListener).toHaveBeenCalledTimes(1);
    expect(approvalListener).not.toHaveBeenCalled();
  });

  it("delivers to every listener subscribed to the same type", () => {
    const first = vi.fn();
    const second = vi.fn();
    renderHook(
      () => {
        useBusEvent("session", first);
        useBusEvent("session", second);
      },
      { wrapper },
    );

    act(() => {
      capturedOnEvent?.({ type: "session", ts: 1, data: {} }, { replayed: false });
    });

    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("forwards the replayed flag unchanged", () => {
    const listener = vi.fn();
    renderHook(() => useBusEvent("issue", listener), { wrapper });

    act(() => {
      capturedOnEvent?.({ type: "issue", ts: 1, data: {} }, { replayed: true });
    });

    expect(listener).toHaveBeenCalledWith(expect.objectContaining({ type: "issue" }), {
      replayed: true,
    });
  });

  it("stops delivering to a listener once its component unmounts", () => {
    const listener = vi.fn();
    const { unmount } = renderHook(() => useBusEvent("notification", listener), { wrapper });

    act(() => {
      unmount();
    });
    act(() => {
      capturedOnEvent?.({ type: "notification", ts: 1, data: {} }, { replayed: false });
    });

    expect(listener).not.toHaveBeenCalled();
  });

  it("does not resubscribe when an inline listener changes identity every render", () => {
    let renders = 0;
    const seen: number[] = [];
    const { rerender } = renderHook(
      () => {
        renders += 1;
        // Inline arrow -- a new function identity every render, exactly the
        // pattern useBusEvent's ref-indirection exists to tolerate.
        useBusEvent("spend", (event) => {
          seen.push((event.data.cost_usd as number) ?? 0);
        });
      },
      { wrapper },
    );

    rerender();
    rerender();
    expect(renders).toBe(3);

    act(() => {
      capturedOnEvent?.({ type: "spend", ts: 1, data: { cost_usd: 7 } }, { replayed: false });
    });

    // Exactly one delivery -- if each render had resubscribed a fresh
    // listener without cleaning up the previous one, this would be 3.
    expect(seen).toEqual([7]);
  });

  it("skips subscribing when enabled=false", () => {
    const listener = vi.fn();
    renderHook(() => useBusEvent("spend", listener, false), { wrapper });

    act(() => {
      capturedOnEvent?.({ type: "spend", ts: 1, data: {} }, { replayed: false });
    });

    expect(listener).not.toHaveBeenCalled();
  });
});
