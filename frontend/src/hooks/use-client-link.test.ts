// @vitest-environment jsdom
/**
 * Tests for useClientLink (bu-8cdl1.13) -- the browser's own network link,
 * kept separate from fleet/backend health so a client-side drop never gets
 * reported as a fleet outage.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";

import { CLIENT_LINK_RECONNECT_GRACE_MS, useClientLink } from "./use-client-link";

function setNavigatorOnline(value: boolean) {
  Object.defineProperty(navigator, "onLine", { configurable: true, value });
}

afterEach(() => {
  cleanup();
  setNavigatorOnline(true);
  vi.useRealTimers();
});

describe("useClientLink", () => {
  it("starts online when navigator.onLine is true", () => {
    setNavigatorOnline(true);
    const { result } = renderHook(() => useClientLink());
    expect(result.current.status).toBe("online");
  });

  it("starts offline when navigator.onLine is false", () => {
    setNavigatorOnline(false);
    const { result } = renderHook(() => useClientLink());
    expect(result.current.status).toBe("offline");
  });

  it("transitions to offline when the browser fires the offline event", () => {
    setNavigatorOnline(true);
    const { result } = renderHook(() => useClientLink());

    act(() => {
      window.dispatchEvent(new Event("offline"));
    });

    expect(result.current.status).toBe("offline");
  });

  it("passes through reconnecting before settling back to online", () => {
    vi.useFakeTimers();
    setNavigatorOnline(false);
    const { result } = renderHook(() => useClientLink());
    expect(result.current.status).toBe("offline");

    act(() => {
      window.dispatchEvent(new Event("online"));
    });
    expect(result.current.status).toBe("reconnecting");

    act(() => {
      vi.advanceTimersByTime(CLIENT_LINK_RECONNECT_GRACE_MS);
    });
    expect(result.current.status).toBe("online");
  });

  it("restarts the reconnect grace window on a flapping connection", () => {
    vi.useFakeTimers();
    setNavigatorOnline(false);
    const { result } = renderHook(() => useClientLink());

    act(() => {
      window.dispatchEvent(new Event("online"));
    });
    act(() => {
      vi.advanceTimersByTime(CLIENT_LINK_RECONNECT_GRACE_MS - 100);
    });
    expect(result.current.status).toBe("reconnecting");

    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    expect(result.current.status).toBe("offline");

    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(result.current.status).toBe("offline");
  });
});
