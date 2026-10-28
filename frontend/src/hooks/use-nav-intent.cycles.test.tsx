// @vitest-environment jsdom
/**
 * useNavIntent cycle boundaries and rejection containment ("Unified Navigation
 * Intent Warmup", openspec/changes/unify-dashboard-navigation-intent): ending a
 * cycle by blur, pointer leave or a target change lets the next signal start a
 * distinct cycle, and one cycle submits each resource at most once.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

vi.mock("@/lib/prefetch-registry", () => ({ resolvePrefetchTarget: vi.fn() }));
vi.mock("@/lib/route-chunk-registry", () => ({ resolveRouteChunkLoader: vi.fn() }));

import { resolvePrefetchTarget } from "@/lib/prefetch-registry";
import { resolveRouteChunkLoader } from "@/lib/route-chunk-registry";
import { useNavIntent, NAV_INTENT_DELAY_MS } from "./use-nav-intent";

const mockQuery = vi.mocked(resolvePrefetchTarget);
const mockLoader = vi.mocked(resolveRouteChunkLoader);

let loadedPaths: string[];

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
  loadedPaths = [];
  mockLoader.mockImplementation(((path: string) => () => {
    loadedPaths.push(path);
    return Promise.resolve({});
  }) as never);
  mockQuery.mockReturnValue(null);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("useNavIntent cycle boundaries", () => {
  it("blur then refocus with no pointer starts a new cycle", () => {
    const { result } = mount("/a");
    act(() => result.current.onFocus());
    act(() => result.current.onBlur());
    act(() => result.current.onFocus());
    expect(loadedPaths).toEqual(["/a", "/a"]);
  });

  it("focus held through a blur of the pointer keeps one cycle", () => {
    const { result } = mount("/a");
    act(() => result.current.onFocus());
    act(() => result.current.onPointerEnter());
    act(() => result.current.onPointerLeave());
    act(() => result.current.onFocus());
    expect(loadedPaths).toEqual(["/a"]);
  });

  it("pointer dwell, leave, then a second dwell starts a new cycle", () => {
    const { result } = mount("/a");
    act(() => result.current.onPointerEnter());
    act(() => {
      vi.advanceTimersByTime(NAV_INTENT_DELAY_MS);
    });
    act(() => result.current.onPointerLeave());
    act(() => result.current.onPointerEnter());
    act(() => {
      vi.advanceTimersByTime(NAV_INTENT_DELAY_MS);
    });
    expect(loadedPaths).toEqual(["/a", "/a"]);
  });

  it("a target change warms the new target on the next signal", () => {
    const { result, rerender } = mount("/a");
    act(() => result.current.onFocus());
    rerender({ path: "/b" });
    act(() => result.current.onFocus());
    expect(loadedPaths).toEqual(["/a", "/b"]);
  });
});

describe("useNavIntent once-per-cycle query submission", () => {
  it("submits prefetchQuery exactly once for focus plus activation", () => {
    mockQuery.mockReturnValue({
      queryKey: ["mapped", "abc"],
      queryFn: () => Promise.resolve("data"),
      staleTime: 60_000,
    });
    const client = new QueryClient();
    const spy = vi.spyOn(client, "prefetchQuery");
    const { result } = mount("/sessions/abc", client);
    act(() => result.current.onFocus());
    act(() => result.current.onActivate());
    act(() => result.current.onFocus());
    expect(spy).toHaveBeenCalledTimes(1);
    expect(loadedPaths).toEqual(["/sessions/abc"]);
  });
});

describe("useNavIntent failure containment", () => {
  it("never lets a rejected chunk load escape as an unhandled rejection", async () => {
    vi.useRealTimers();
    const unhandled: unknown[] = [];
    const onUnhandled = (reason: unknown) => unhandled.push(reason);
    process.on("unhandledRejection", onUnhandled);
    let calls = 0;
    // A plain function, not vi.fn: vitest marks a vi.fn's returned rejection as handled.
    mockLoader.mockReturnValue((() => {
      calls += 1;
      return Promise.reject(new Error("offline"));
    }) as never);
    try {
      const { result } = mount("/x");
      act(() => result.current.onFocus());
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(calls).toBe(1);
      expect(unhandled).toEqual([]);
    } finally {
      process.off("unhandledRejection", onUnhandled);
    }
  });

  it("never lets a rejected query prefetch escape as an unhandled rejection", async () => {
    vi.useRealTimers();
    const unhandled: unknown[] = [];
    const onUnhandled = (reason: unknown) => unhandled.push(reason);
    process.on("unhandledRejection", onUnhandled);
    mockQuery.mockReturnValue({
      queryKey: ["mapped", "x"],
      queryFn: () => Promise.resolve("data"),
      staleTime: 0,
    });
    const client = new QueryClient();
    client.prefetchQuery = (() => Promise.reject(new Error("offline"))) as never;
    try {
      const { result } = mount("/x", client);
      act(() => result.current.onFocus());
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(unhandled).toEqual([]);
    } finally {
      process.off("unhandledRejection", onUnhandled);
    }
  });

  it("contains a synchronously throwing loader", () => {
    mockLoader.mockReturnValue((() => {
      throw new Error("sync");
    }) as never);
    const { result } = mount("/x");
    expect(() => act(() => result.current.onFocus())).not.toThrow();
  });
});
