// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useVisualViewportHeight } from "./use-visual-viewport-height.ts";

function installVisualViewport(initialHeight: number) {
  const listeners = new Set<() => void>();
  const vv = {
    height: initialHeight,
    addEventListener: (_: string, cb: () => void) => listeners.add(cb),
    removeEventListener: (_: string, cb: () => void) => listeners.delete(cb),
  };
  Object.defineProperty(window, "visualViewport", { value: vv, configurable: true });
  return {
    resize(next: number) {
      vv.height = next;
      for (const cb of listeners) cb();
    },
  };
}

afterEach(() => {
  Reflect.deleteProperty(window, "visualViewport");
});

describe("useVisualViewportHeight", () => {
  it("returns null when visualViewport is unavailable", () => {
    const { result } = renderHook(() => useVisualViewportHeight());
    expect(result.current).toBeNull();
  });

  it("returns the current visualViewport height and updates on resize", () => {
    const { resize } = installVisualViewport(700);
    const { result } = renderHook(() => useVisualViewportHeight());
    expect(result.current).toBe(700);

    act(() => resize(420));
    expect(result.current).toBe(420);
  });

  it("removes its resize listener on unmount", () => {
    const removeEventListener = vi.fn();
    Object.defineProperty(window, "visualViewport", {
      value: { height: 600, addEventListener: vi.fn(), removeEventListener },
      configurable: true,
    });

    const { unmount } = renderHook(() => useVisualViewportHeight());
    unmount();
    expect(removeEventListener).toHaveBeenCalledWith("resize", expect.any(Function));
  });
});
