// @vitest-environment jsdom
/**
 * useUndoWindow — generic confirm-or-undo-window scheduler (bu-ep4ks.11).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

import { useUndoWindow } from "./use-undo-window";

describe("useUndoWindow", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not run immediately, and runs once the window elapses", () => {
    vi.useFakeTimers();
    const run = vi.fn();
    const { result } = renderHook(() => useUndoWindow("test-immediate"));

    act(() => {
      result.current.schedule("a", run, 1000);
    });
    expect(run).not.toHaveBeenCalled();
    expect(result.current.isScheduled("a")).toBe(true);

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(run).toHaveBeenCalledTimes(1);
    expect(result.current.isScheduled("a")).toBe(false);
  });

  it("cancel() prevents the scheduled run from ever firing", () => {
    vi.useFakeTimers();
    const run = vi.fn();
    const { result } = renderHook(() => useUndoWindow("test-cancel"));

    act(() => {
      result.current.schedule("a", run, 1000);
    });
    act(() => {
      result.current.cancel("a");
    });
    expect(result.current.isScheduled("a")).toBe(false);

    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(run).not.toHaveBeenCalled();
  });

  it("scheduling twice on the same id is a no-op — no double-fire", () => {
    vi.useFakeTimers();
    const run = vi.fn();
    const { result } = renderHook(() => useUndoWindow("test-double-fire"));

    let first: boolean | undefined;
    let second: boolean | undefined;
    act(() => {
      first = result.current.schedule("a", run, 1000);
      second = result.current.schedule("a", run, 1000);
    });
    expect(first).toBe(true);
    expect(second).toBe(false);

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("cancelling an id with nothing scheduled is a safe no-op", () => {
    const { result } = renderHook(() => useUndoWindow("test-noop-cancel"));
    expect(() => act(() => result.current.cancel("missing"))).not.toThrow();
  });

  it("namespaces ids so two unrelated hook instances never collide on the same raw id", () => {
    vi.useFakeTimers();
    const runA = vi.fn();
    const runB = vi.fn();
    const { result: a } = renderHook(() => useUndoWindow("ns-a"));
    const { result: b } = renderHook(() => useUndoWindow("ns-b"));

    act(() => {
      a.current.schedule("same-id", runA, 1000);
      b.current.schedule("same-id", runB, 1000);
    });
    expect(a.current.isScheduled("same-id")).toBe(true);
    expect(b.current.isScheduled("same-id")).toBe(true);

    act(() => {
      a.current.cancel("same-id");
    });
    expect(a.current.isScheduled("same-id")).toBe(false);
    expect(b.current.isScheduled("same-id")).toBe(true);

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(runA).not.toHaveBeenCalled();
    expect(runB).toHaveBeenCalledTimes(1);
  });
});
