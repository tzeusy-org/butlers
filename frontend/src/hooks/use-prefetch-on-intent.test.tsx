// @vitest-environment jsdom
/**
 * Tests for usePrefetchOnIntent (bu-qvnce.14 slice 4, deferred from PR
 * #2927): hover/focus "intent" -> speculative prefetch via the
 * route-registry prefetch map (lib/prefetch-registry.ts).
 *
 * The registry itself is mocked here -- these tests only cover the hook's
 * own contract: intent delay, cancel-on-leave, unmapped no-op, and
 * respecting the resolved target's staleTime.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

vi.mock("@/lib/prefetch-registry", () => ({
  resolvePrefetchTarget: vi.fn(),
}));

import { resolvePrefetchTarget } from "@/lib/prefetch-registry";
import { usePrefetchOnIntent, PREFETCH_INTENT_DELAY_MS } from "./use-prefetch-on-intent";

const mockResolvePrefetchTarget = vi.mocked(resolvePrefetchTarget);

function makeWrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("usePrefetchOnIntent", () => {
  it("does not prefetch immediately -- only after the intent delay elapses", () => {
    const queryFn = vi.fn(() => Promise.resolve("data"));
    mockResolvePrefetchTarget.mockReturnValue({
      queryKey: ["mapped"],
      queryFn,
      staleTime: 60_000,
    });
    const client = new QueryClient();
    const { result } = renderHook(() => usePrefetchOnIntent("/mapped"), {
      wrapper: makeWrapper(client),
    });

    act(() => {
      result.current.schedule();
    });
    expect(queryFn).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(PREFETCH_INTENT_DELAY_MS - 1);
    });
    expect(queryFn).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(queryFn).toHaveBeenCalledTimes(1);
  });

  it("cancel before the delay elapses skips the fetch entirely (pointer-sweep case)", () => {
    const queryFn = vi.fn(() => Promise.resolve("data"));
    mockResolvePrefetchTarget.mockReturnValue({
      queryKey: ["mapped"],
      queryFn,
      staleTime: 60_000,
    });
    const client = new QueryClient();
    const { result } = renderHook(() => usePrefetchOnIntent("/mapped"), {
      wrapper: makeWrapper(client),
    });

    act(() => {
      result.current.schedule();
      vi.advanceTimersByTime(PREFETCH_INTENT_DELAY_MS / 2);
      result.current.cancel();
      vi.advanceTimersByTime(PREFETCH_INTENT_DELAY_MS);
    });

    expect(queryFn).not.toHaveBeenCalled();
  });

  it("an unmapped target is a no-op -- no fetch, even after the delay", () => {
    mockResolvePrefetchTarget.mockReturnValue(null);
    const client = new QueryClient();
    const prefetchSpy = vi.spyOn(client, "prefetchQuery");
    const { result } = renderHook(() => usePrefetchOnIntent("/not-mapped"), {
      wrapper: makeWrapper(client),
    });

    act(() => {
      result.current.schedule();
      vi.advanceTimersByTime(PREFETCH_INTENT_DELAY_MS);
    });

    expect(prefetchSpy).not.toHaveBeenCalled();
  });

  it("a null/undefined `to` is a no-op without even consulting the registry", () => {
    const client = new QueryClient();
    const { result } = renderHook(() => usePrefetchOnIntent(null), {
      wrapper: makeWrapper(client),
    });

    act(() => {
      result.current.schedule();
      vi.advanceTimersByTime(PREFETCH_INTENT_DELAY_MS);
    });

    expect(mockResolvePrefetchTarget).not.toHaveBeenCalled();
  });

  it("respects the resolved target's staleTime -- a repeat schedule within the window does not refetch", () => {
    const queryFn = vi.fn(() => Promise.resolve("data"));
    mockResolvePrefetchTarget.mockReturnValue({
      queryKey: ["mapped"],
      queryFn,
      staleTime: 60_000,
    });
    const client = new QueryClient();
    const { result } = renderHook(() => usePrefetchOnIntent("/mapped"), {
      wrapper: makeWrapper(client),
    });

    act(() => {
      result.current.schedule();
      vi.advanceTimersByTime(PREFETCH_INTENT_DELAY_MS);
    });
    expect(queryFn).toHaveBeenCalledTimes(1);

    // Hover away and back a second later -- well within the 60s staleTime --
    // must not re-fetch (the whole point of passing staleTime through).
    act(() => {
      result.current.schedule();
      vi.advanceTimersByTime(PREFETCH_INTENT_DELAY_MS);
    });
    expect(queryFn).toHaveBeenCalledTimes(1);
  });

  it("cancels any pending timer on unmount", () => {
    const queryFn = vi.fn(() => Promise.resolve("data"));
    mockResolvePrefetchTarget.mockReturnValue({
      queryKey: ["mapped"],
      queryFn,
      staleTime: 60_000,
    });
    const client = new QueryClient();
    const { result, unmount } = renderHook(() => usePrefetchOnIntent("/mapped"), {
      wrapper: makeWrapper(client),
    });

    act(() => {
      result.current.schedule();
    });
    unmount();
    act(() => {
      vi.advanceTimersByTime(PREFETCH_INTENT_DELAY_MS);
    });

    expect(queryFn).not.toHaveBeenCalled();
  });
});
