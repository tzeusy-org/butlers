// @vitest-environment jsdom
/**
 * Tests for useRouteChunkPrefetchOnIntent (bu-ep4ks.15): hover/focus
 * "intent" -> route JS-chunk prefetch via lib/route-chunk-registry.ts.
 *
 * The registry itself is mocked here -- these tests only cover the hook's
 * own contract: intent delay, cancel-on-leave, unmapped no-op, and that a
 * loader rejection never becomes an uncaught rejection.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook, act } from "@testing-library/react";

vi.mock("@/lib/route-chunk-registry", () => ({
  resolveRouteChunkLoader: vi.fn(),
}));

import { resolveRouteChunkLoader } from "@/lib/route-chunk-registry";
import {
  useRouteChunkPrefetchOnIntent,
  ROUTE_CHUNK_PREFETCH_INTENT_DELAY_MS,
} from "./use-route-chunk-prefetch-on-intent";

const mockResolveRouteChunkLoader = vi.mocked(resolveRouteChunkLoader);

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("useRouteChunkPrefetchOnIntent", () => {
  it("does not load the chunk immediately -- only after the intent delay elapses", () => {
    const loader = vi.fn(() => Promise.resolve({ default: () => null }));
    mockResolveRouteChunkLoader.mockReturnValue(loader);
    const { result } = renderHook(() => useRouteChunkPrefetchOnIntent("/mapped"));

    act(() => {
      result.current.schedule();
    });
    expect(loader).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(ROUTE_CHUNK_PREFETCH_INTENT_DELAY_MS);
    });
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it("loads immediately for keyboard focus", () => {
    const loader = vi.fn(() => Promise.resolve({ default: () => null }));
    mockResolveRouteChunkLoader.mockReturnValue(loader);
    const { result } = renderHook(() => useRouteChunkPrefetchOnIntent("/keyboard"));

    act(() => result.current.onFocus());

    expect(loader).toHaveBeenCalledTimes(1);
  });

  it("starts a pending pointer warmup when activation wins the race", () => {
    const loader = vi.fn(() => Promise.resolve({ default: () => null }));
    mockResolveRouteChunkLoader.mockReturnValue(loader);
    const { result } = renderHook(() => useRouteChunkPrefetchOnIntent("/activate"));

    act(() => {
      result.current.onPointerEnter();
      result.current.onActivate?.();
    });

    expect(loader).toHaveBeenCalledTimes(1);
  });

  it("cancels the pending load if intent ends before the delay elapses", () => {
    const loader = vi.fn(() => Promise.resolve({ default: () => null }));
    mockResolveRouteChunkLoader.mockReturnValue(loader);
    const { result } = renderHook(() => useRouteChunkPrefetchOnIntent("/mapped"));

    act(() => {
      result.current.schedule();
      result.current.cancel();
      vi.advanceTimersByTime(ROUTE_CHUNK_PREFETCH_INTENT_DELAY_MS);
    });
    expect(loader).not.toHaveBeenCalled();
  });

  it("is a no-op for a path unmapped in the registry", () => {
    mockResolveRouteChunkLoader.mockReturnValue(null);
    const { result } = renderHook(() => useRouteChunkPrefetchOnIntent("/unmapped"));

    act(() => {
      result.current.schedule();
      vi.advanceTimersByTime(ROUTE_CHUNK_PREFETCH_INTENT_DELAY_MS);
    });
    expect(mockResolveRouteChunkLoader).toHaveBeenCalledWith("/unmapped");
  });

  it("is a no-op for a null path", () => {
    const { result } = renderHook(() => useRouteChunkPrefetchOnIntent(null));

    act(() => {
      result.current.schedule();
      vi.advanceTimersByTime(ROUTE_CHUNK_PREFETCH_INTENT_DELAY_MS);
    });
    expect(mockResolveRouteChunkLoader).not.toHaveBeenCalled();
  });

  it("swallows a loader rejection instead of surfacing an uncaught rejection", async () => {
    const loader = vi.fn(() => Promise.reject(new Error("chunk load failed")));
    mockResolveRouteChunkLoader.mockReturnValue(loader);
    const { result } = renderHook(() => useRouteChunkPrefetchOnIntent("/mapped"));

    act(() => {
      result.current.schedule();
      vi.advanceTimersByTime(ROUTE_CHUNK_PREFETCH_INTENT_DELAY_MS);
    });
    expect(loader).toHaveBeenCalledTimes(1);
    // Flush the rejected promise's microtask under real timers so its
    // .catch(() => {}) has a chance to run before the test ends.
    await act(async () => {
      vi.useRealTimers();
      await Promise.resolve();
      vi.useFakeTimers();
    });
  });
});
