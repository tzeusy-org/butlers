// @vitest-environment jsdom
/**
 * usePrefersReducedMotion — unit tests
 *
 * Verifies:
 *  - returns true when matchMedia reports prefers-reduced-motion: reduce
 *  - returns false when matchMedia reports no preference
 *  - updates reactively when the media query changes
 *
 * bead: bu-gnna7
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, cleanup } from "@testing-library/react";
import { usePrefersReducedMotion } from "./use-prefers-reduced-motion";

// ---------------------------------------------------------------------------
// matchMedia mock helpers
// ---------------------------------------------------------------------------

type ChangeHandler = (e: MediaQueryListEvent) => void;

function makeMatchMediaMock(matches: boolean) {
  const listeners: ChangeHandler[] = [];
  const mq = {
    matches,
    addEventListener: vi.fn((_event: string, handler: ChangeHandler) => {
      listeners.push(handler);
    }),
    removeEventListener: vi.fn((_event: string, handler: ChangeHandler) => {
      const idx = listeners.indexOf(handler);
      if (idx !== -1) listeners.splice(idx, 1);
    }),
    // Helper to fire a change event from tests
    _fire(nextMatches: boolean) {
      mq.matches = nextMatches;
      listeners.forEach((h) => h({ matches: nextMatches } as MediaQueryListEvent));
    },
  };
  return mq;
}

describe("usePrefersReducedMotion", () => {
  let originalMatchMedia: typeof window.matchMedia;

  beforeEach(() => {
    originalMatchMedia = window.matchMedia;
  });

  afterEach(() => {
    window.matchMedia = originalMatchMedia;
    cleanup();
    vi.restoreAllMocks();
  });

  it("returns true when prefers-reduced-motion: reduce is active", () => {
    const mq = makeMatchMediaMock(true);
    window.matchMedia = vi.fn(() => mq as unknown as MediaQueryList);

    const { result } = renderHook(() => usePrefersReducedMotion());
    expect(result.current).toBe(true);
  });

  it("returns false when prefers-reduced-motion is not set", () => {
    const mq = makeMatchMediaMock(false);
    window.matchMedia = vi.fn(() => mq as unknown as MediaQueryList);

    const { result } = renderHook(() => usePrefersReducedMotion());
    expect(result.current).toBe(false);
  });

  it("registers a 'change' listener on mount", () => {
    const mq = makeMatchMediaMock(false);
    window.matchMedia = vi.fn(() => mq as unknown as MediaQueryList);

    renderHook(() => usePrefersReducedMotion());
    expect(mq.addEventListener).toHaveBeenCalledWith("change", expect.any(Function));
  });

  it("removes the 'change' listener on unmount", () => {
    const mq = makeMatchMediaMock(false);
    window.matchMedia = vi.fn(() => mq as unknown as MediaQueryList);

    const { unmount } = renderHook(() => usePrefersReducedMotion());
    unmount();
    expect(mq.removeEventListener).toHaveBeenCalledWith("change", expect.any(Function));
  });

  it("updates from false to true when media query fires a change event", () => {
    const mq = makeMatchMediaMock(false);
    window.matchMedia = vi.fn(() => mq as unknown as MediaQueryList);

    const { result } = renderHook(() => usePrefersReducedMotion());
    expect(result.current).toBe(false);

    act(() => {
      mq._fire(true);
    });

    expect(result.current).toBe(true);
  });

  it("updates from true to false when media query fires a change event", () => {
    const mq = makeMatchMediaMock(true);
    window.matchMedia = vi.fn(() => mq as unknown as MediaQueryList);

    const { result } = renderHook(() => usePrefersReducedMotion());
    expect(result.current).toBe(true);

    act(() => {
      mq._fire(false);
    });

    expect(result.current).toBe(false);
  });
});
