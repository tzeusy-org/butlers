// @vitest-environment jsdom
/**
 * useMediaQuery — unit tests. Mirrors use-prefers-reduced-motion.test.ts's
 * matchMedia mock shape (bu-0ynlk.11).
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act, cleanup } from "@testing-library/react";
import { useMediaQuery } from "./use-media-query";

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
    _fire(nextMatches: boolean) {
      mq.matches = nextMatches;
      listeners.forEach((h) => h({ matches: nextMatches } as MediaQueryListEvent));
    },
  };
  return mq;
}

describe("useMediaQuery", () => {
  const originalMatchMedia = window.matchMedia;

  afterEach(() => {
    window.matchMedia = originalMatchMedia;
    cleanup();
    vi.restoreAllMocks();
  });

  it("returns the query's initial match state", () => {
    const mq = makeMatchMediaMock(true);
    window.matchMedia = vi.fn(() => mq as unknown as MediaQueryList);

    const { result } = renderHook(() => useMediaQuery("(min-width: 1280px)"));
    expect(result.current).toBe(true);
  });

  it("updates reactively when the query's match state changes", () => {
    const mq = makeMatchMediaMock(false);
    window.matchMedia = vi.fn(() => mq as unknown as MediaQueryList);

    const { result } = renderHook(() => useMediaQuery("(min-width: 1280px)"));
    expect(result.current).toBe(false);

    act(() => mq._fire(true));

    expect(result.current).toBe(true);
  });

  it("removes its listener on unmount", () => {
    const mq = makeMatchMediaMock(false);
    window.matchMedia = vi.fn(() => mq as unknown as MediaQueryList);

    const { unmount } = renderHook(() => useMediaQuery("(min-width: 1280px)"));
    unmount();
    expect(mq.removeEventListener).toHaveBeenCalledWith("change", expect.any(Function));
  });
});
