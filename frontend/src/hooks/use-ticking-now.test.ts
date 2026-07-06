// @vitest-environment jsdom
/**
 * Tests for useTickingNow (bu-ptaub) -- a wall-clock `now` that advances on
 * its own via a local interval, independent of any data refetch.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

import { useTickingNow } from "./use-ticking-now";

afterEach(() => {
  vi.useRealTimers();
});

describe("useTickingNow", () => {
  it("initializes to the current time", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-06T00:00:00.000Z"));

    const { result } = renderHook(() => useTickingNow());
    expect(result.current).toBe(Date.parse("2026-07-06T00:00:00.000Z"));
  });

  it("advances on its own once the interval fires, with no external trigger", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-06T00:00:00.000Z"));

    const { result } = renderHook(() => useTickingNow(15_000));
    const initial = result.current;

    act(() => {
      vi.advanceTimersByTime(15_000);
    });

    expect(result.current).toBe(initial + 15_000);
  });

  it("clears its interval on unmount", () => {
    vi.useFakeTimers();
    const clearSpy = vi.spyOn(globalThis, "clearInterval");

    const { unmount } = renderHook(() => useTickingNow(15_000));
    unmount();

    expect(clearSpy).toHaveBeenCalled();
    clearSpy.mockRestore();
  });
});
