// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { msUntilNextLocalMidnight, useLocalDayStart } from "@/pages/calendar/use-local-day";

describe("useLocalDayStart", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("computes the delay to the next local midnight", () => {
    expect(msUntilNextLocalMidnight(new Date(2026, 10, 26, 23, 0, 0))).toBe(60 * 60 * 1000);
  });

  it("rolls to the next day at local midnight in a long-lived tab", () => {
    vi.setSystemTime(new Date(2026, 10, 26, 23, 59, 0));
    const { result } = renderHook(() => useLocalDayStart());
    expect(result.current.getDate()).toBe(26);

    act(() => {
      vi.advanceTimersByTime(2 * 60 * 1000);
    });
    expect(result.current.getDate()).toBe(27);
  });

  it("keeps the same identity while the day is unchanged", () => {
    vi.setSystemTime(new Date(2026, 10, 26, 8, 0, 0));
    const { result } = renderHook(() => useLocalDayStart());
    const first = result.current;
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(result.current).toBe(first);
  });

  it("re-syncs when a hidden tab becomes visible after midnight", () => {
    vi.setSystemTime(new Date(2026, 10, 26, 23, 0, 0));
    const { result } = renderHook(() => useLocalDayStart());
    vi.setSystemTime(new Date(2026, 10, 27, 7, 0, 0));
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(result.current.getDate()).toBe(27);
  });

  it("re-reads the day on window focus when the timer never fired (OS sleep)", () => {
    vi.setSystemTime(new Date(2026, 10, 26, 23, 0, 0));
    const { result } = renderHook(() => useLocalDayStart());
    // The clock jumps past midnight without any timer advancing, as after sleep.
    vi.setSystemTime(new Date(2026, 10, 27, 7, 0, 0));
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    expect(result.current.getDate()).toBe(27);
  });

  it("re-reads the day on pageshow", () => {
    vi.setSystemTime(new Date(2026, 10, 26, 23, 0, 0));
    const { result } = renderHook(() => useLocalDayStart());
    vi.setSystemTime(new Date(2026, 10, 27, 7, 0, 0));
    act(() => {
      window.dispatchEvent(new Event("pageshow"));
    });
    expect(result.current.getDate()).toBe(27);
  });

  it("never waits longer than a minute between re-reads", () => {
    vi.setSystemTime(new Date(2026, 10, 26, 8, 0, 0));
    const { result } = renderHook(() => useLocalDayStart());
    vi.setSystemTime(new Date(2026, 10, 27, 7, 0, 0));
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(result.current.getDate()).toBe(27);
  });
});
