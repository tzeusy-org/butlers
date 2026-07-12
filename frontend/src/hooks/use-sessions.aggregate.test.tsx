// @vitest-environment jsdom
//
// useSessionAggregate keying contract: the aggregate is window-true and keyed
// on the FILTER params only. Paging (cursor/offset/limit) must NOT re-fetch it,
// but a filter change must.

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const mockGetSessionAggregate = vi.fn();

vi.mock("@/api/index.ts", () => ({
  getSessionAggregate: (...args: unknown[]) => mockGetSessionAggregate(...args),
  // The hook module also imports these; provide inert stubs.
  getSessions: vi.fn(),
  getSession: vi.fn(),
  getButlerSessions: vi.fn(),
}));

// useSessionAggregate (and its sibling hooks in this module) now call
// useBusAwarePollInterval (bu-01r64.3), which reads the shared
// EventBusProvider context -- stub it rather than wrapping every renderHook
// call here in a real provider; these tests only care about query-key/fetch
// wiring, not bus-driven polling cadence.
vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({ status: "open", lastEventAt: null, subscribe: vi.fn() }),
}));

import { useSessionAggregate } from "./use-sessions";

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetSessionAggregate.mockResolvedValue({ data: {}, meta: {} });
});

describe("useSessionAggregate", () => {
  it("strips cursor/offset/limit before calling the endpoint", async () => {
    const Wrapper = makeWrapper();
    renderHook(
      () => useSessionAggregate({ butler: "health", cursor: "c1", offset: 40, limit: 20 }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(mockGetSessionAggregate).toHaveBeenCalledTimes(1));
    expect(mockGetSessionAggregate).toHaveBeenCalledWith({ butler: "health" });
  });

  it("does NOT refetch when only the cursor changes, but DOES when a filter changes", async () => {
    const Wrapper = makeWrapper();
    const { rerender } = renderHook(({ p }) => useSessionAggregate(p), {
      wrapper: Wrapper,
      initialProps: { p: { butler: "health", cursor: "c1" } as Record<string, string> },
    });

    await waitFor(() => expect(mockGetSessionAggregate).toHaveBeenCalledTimes(1));

    // Only the cursor changed → same filter key → no new fetch.
    rerender({ p: { butler: "health", cursor: "c2" } });
    await Promise.resolve();
    expect(mockGetSessionAggregate).toHaveBeenCalledTimes(1);

    // A filter changed → new key → refetch.
    rerender({ p: { butler: "finance", cursor: "c2" } });
    await waitFor(() => expect(mockGetSessionAggregate).toHaveBeenCalledTimes(2));
  });
});
