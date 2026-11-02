// @vitest-environment jsdom
/**
 * Tests for useNavIntent: one intent cycle warms the lazy route chunk and the
 * destination's mapped query ("Unified Navigation Intent Warmup",
 * openspec/changes/unify-dashboard-navigation-intent). Both registries are
 * mocked; these tests cover only the hook's own contract.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

vi.mock("@/lib/prefetch-registry", () => ({ resolvePrefetchTarget: vi.fn() }));
vi.mock("@/lib/route-chunk-registry", () => ({ resolveRouteChunkLoader: vi.fn() }));

import { resolvePrefetchTarget } from "@/lib/prefetch-registry";
import { resolveRouteChunkLoader } from "@/lib/route-chunk-registry";
import { useNavIntent, NAV_INTENT_DELAY_MS } from "./use-nav-intent";

const mockQuery = vi.mocked(resolvePrefetchTarget);
const mockLoader = vi.mocked(resolveRouteChunkLoader);

let loader: ReturnType<typeof vi.fn>;
let queryFn: ReturnType<typeof vi.fn>;

function wrapperFor(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

function mount(to: string | null, client = new QueryClient()) {
  return renderHook(({ path }) => useNavIntent(path), {
    initialProps: { path: to },
    wrapper: wrapperFor(client),
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  loader = vi.fn(() => Promise.resolve({}));
  queryFn = vi.fn(() => Promise.resolve("data"));
  mockLoader.mockReturnValue(loader as never);
  mockQuery.mockReturnValue({ queryKey: ["mapped", "abc"], queryFn, staleTime: 60_000 });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("useNavIntent", () => {
  it("warms the chunk and the exact query only after an uninterrupted dwell", () => {
    const client = new QueryClient();
    const spy = vi.spyOn(client, "prefetchQuery");
    const { result } = mount("/sessions/abc", client);

    act(() => result.current.onPointerEnter());
    act(() => {
      vi.advanceTimersByTime(NAV_INTENT_DELAY_MS - 1);
    });
    expect(loader).not.toHaveBeenCalled();
    expect(spy).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(loader).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith({ queryKey: ["mapped", "abc"], queryFn, staleTime: 60_000 });
  });

  it("starts nothing when the pointer leaves before the dwell elapses", () => {
    const { result } = mount("/sessions/abc");
    act(() => result.current.onPointerEnter());
    act(() => {
      vi.advanceTimersByTime(NAV_INTENT_DELAY_MS - 1);
      result.current.onPointerLeave();
      vi.advanceTimersByTime(NAV_INTENT_DELAY_MS);
    });
    expect(loader).not.toHaveBeenCalled();
    expect(queryFn).not.toHaveBeenCalled();
  });

  it("warms both resources immediately on focus", () => {
    const { result } = mount("/sessions/abc");
    act(() => result.current.onFocus());
    expect(loader).toHaveBeenCalledTimes(1);
    expect(queryFn).toHaveBeenCalledTimes(1);
  });

  it("warms on activation, cancelling a pending pointer timer", () => {
    const { result } = mount("/sessions/abc");
    act(() => {
      result.current.onPointerEnter();
      result.current.onActivate();
    });
    expect(loader).toHaveBeenCalledTimes(1);
    act(() => {
      vi.advanceTimersByTime(NAV_INTENT_DELAY_MS * 2);
    });
    expect(loader).toHaveBeenCalledTimes(1);
    expect(queryFn).toHaveBeenCalledTimes(1);
  });

  it("starts each resource once when focus is followed by activation, then again in a later cycle", async () => {
    const { result } = mount("/sessions/abc");
    act(() => {
      result.current.onFocus();
      result.current.onActivate();
    });
    expect(loader).toHaveBeenCalledTimes(1);
    expect(queryFn).toHaveBeenCalledTimes(1);

    await act(async () => {
      await Promise.resolve();
    });
    act(() => result.current.onActivate());
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it("warms only the chunk when no query is mapped, and only the query when no chunk is", () => {
    mockQuery.mockReturnValue(null);
    const first = mount("/chunk-only");
    act(() => first.result.current.onFocus());
    expect(loader).toHaveBeenCalledTimes(1);
    expect(queryFn).not.toHaveBeenCalled();
    first.unmount();

    mockQuery.mockReturnValue({ queryKey: ["q"], queryFn, staleTime: 1 });
    mockLoader.mockReturnValue(null);
    const second = mount("/query-only");
    act(() => second.result.current.onFocus());
    expect(queryFn).toHaveBeenCalledTimes(1);
  });

  it("is a no-op for a null or unmapped target", () => {
    mockQuery.mockReturnValue(null);
    mockLoader.mockReturnValue(null);
    const { result } = mount(null);
    act(() => {
      result.current.onPointerEnter();
      result.current.onFocus();
      result.current.onActivate();
      vi.advanceTimersByTime(NAV_INTENT_DELAY_MS);
    });
    expect(loader).not.toHaveBeenCalled();
    expect(queryFn).not.toHaveBeenCalled();
  });

  it("still warms the chunk without a QueryClient provider", () => {
    const { result } = renderHook(() => useNavIntent("/sessions/abc"));
    expect(() => act(() => result.current.onFocus())).not.toThrow();
    expect(loader).toHaveBeenCalledTimes(1);
    expect(queryFn).not.toHaveBeenCalled();
  });

  it("contains a rejected chunk and a rejected query without cancelling the other", async () => {
    loader.mockImplementation(() => Promise.reject(new Error("offline")));
    queryFn.mockImplementation(() => Promise.reject(new Error("401")));
    const { result } = mount("/sessions/abc");
    act(() => result.current.onFocus());
    await act(async () => {
      await Promise.resolve();
    });
    expect(loader).toHaveBeenCalledTimes(1);
    expect(queryFn).toHaveBeenCalledTimes(1);
  });

  it("cancels the pending timer on unmount and when the target changes", () => {
    const { result, rerender, unmount } = mount("/sessions/abc");
    act(() => result.current.onPointerEnter());
    rerender({ path: "/sessions/def" });
    act(() => {
      vi.advanceTimersByTime(NAV_INTENT_DELAY_MS);
    });
    expect(loader).not.toHaveBeenCalled();

    act(() => result.current.onPointerEnter());
    unmount();
    act(() => {
      vi.advanceTimersByTime(NAV_INTENT_DELAY_MS);
    });
    expect(loader).not.toHaveBeenCalled();
  });
});
