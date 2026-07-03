// @vitest-environment jsdom
//
// useIngestionEventsHistogram (bu-4utdw.6): plumbing for the status-aware
// timeline hour strip. Verifies:
// - the query key factory is deterministic and params-scoped
// - from/to are forwarded verbatim to getIngestionEventsHistogram
// - the query is disabled (no fetch) when from or to is missing, or when
//   options.enabled is explicitly false
// - the query is enabled (fetch fires) once both from and to are present

import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const mockGetIngestionEventsHistogram = vi.fn();

vi.mock("@/api/index.ts", () => ({
  // Function under test.
  getIngestionEventsHistogram: (...args: unknown[]) =>
    mockGetIngestionEventsHistogram(...args),
  // The hook module also imports these — provide inert stubs so the mock
  // covers the whole "@/api/index.ts" surface it pulls from.
  listIngestionEvents: vi.fn(),
  getIngestionEvent: vi.fn(),
  getIngestionEventSessions: vi.fn(),
  getIngestionEventRollup: vi.fn(),
  getIngestionWindowRollup: vi.fn(),
  getIngestionEventReplays: vi.fn(),
  getIngestionEventSenderContact: vi.fn(),
  getIngestionEventPayload: vi.fn(),
}));

import { ingestionEventKeys, useIngestionEventsHistogram } from "./use-ingestion-events.ts";

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  mockGetIngestionEventsHistogram.mockResolvedValue({ buckets: [], bucket: "1m" });
});

describe("ingestionEventKeys.histogram", () => {
  it("is scoped by the full params object", () => {
    const p1 = { from: "2026-01-01T00:00:00Z", to: "2026-01-02T00:00:00Z" };
    const p2 = { from: "2026-01-01T00:00:00Z", to: "2026-01-03T00:00:00Z" };
    expect(ingestionEventKeys.histogram(p1)).toEqual([
      "ingestion",
      "events-histogram",
      p1,
    ]);
    expect(ingestionEventKeys.histogram(p1)).not.toEqual(
      ingestionEventKeys.histogram(p2),
    );
  });
});

describe("useIngestionEventsHistogram", () => {
  it("fetches with from/to (and optional filters) forwarded verbatim", async () => {
    const Wrapper = makeWrapper();
    const params = {
      from: "2026-01-01T00:00:00Z",
      to: "2026-01-02T00:00:00Z",
      bucket: "5m" as const,
      channels: "email",
      statuses: "ingested,error",
      q: "alice",
    };
    renderHook(() => useIngestionEventsHistogram(params), { wrapper: Wrapper });

    await waitFor(() =>
      expect(mockGetIngestionEventsHistogram).toHaveBeenCalledTimes(1),
    );
    expect(mockGetIngestionEventsHistogram).toHaveBeenCalledWith(params);
  });

  it("does not fetch when 'from' is missing", async () => {
    const Wrapper = makeWrapper();
    renderHook(
      () => useIngestionEventsHistogram({ from: "", to: "2026-01-02T00:00:00Z" }),
      { wrapper: Wrapper },
    );

    await Promise.resolve();
    expect(mockGetIngestionEventsHistogram).not.toHaveBeenCalled();
  });

  it("does not fetch when 'to' is missing", async () => {
    const Wrapper = makeWrapper();
    renderHook(
      () => useIngestionEventsHistogram({ from: "2026-01-01T00:00:00Z", to: "" }),
      { wrapper: Wrapper },
    );

    await Promise.resolve();
    expect(mockGetIngestionEventsHistogram).not.toHaveBeenCalled();
  });

  it("fetches when both 'from' and 'to' are missing but 'trace_id' is present (bu-1f81d)", async () => {
    // A trace-scoped query auto-widens to the trace's own event bounds
    // server-side — the client must not gate the fetch on from/to in that
    // case, or a trace older than the range picker's window would never
    // populate the hour strip.
    const Wrapper = makeWrapper();
    const params = { trace_id: "trace-abc-123" };
    renderHook(() => useIngestionEventsHistogram(params), { wrapper: Wrapper });

    await waitFor(() =>
      expect(mockGetIngestionEventsHistogram).toHaveBeenCalledTimes(1),
    );
    expect(mockGetIngestionEventsHistogram).toHaveBeenCalledWith(params);
  });

  it("does not fetch when options.enabled is false, even with from/to present", async () => {
    const Wrapper = makeWrapper();
    renderHook(
      () =>
        useIngestionEventsHistogram(
          { from: "2026-01-01T00:00:00Z", to: "2026-01-02T00:00:00Z" },
          { enabled: false },
        ),
      { wrapper: Wrapper },
    );

    await Promise.resolve();
    expect(mockGetIngestionEventsHistogram).not.toHaveBeenCalled();
  });

  it("re-fetches when params change (new query key)", async () => {
    const Wrapper = makeWrapper();
    const { rerender } = renderHook(
      ({ p }) => useIngestionEventsHistogram(p),
      {
        wrapper: Wrapper,
        initialProps: {
          p: { from: "2026-01-01T00:00:00Z", to: "2026-01-02T00:00:00Z" },
        },
      },
    );

    await waitFor(() =>
      expect(mockGetIngestionEventsHistogram).toHaveBeenCalledTimes(1),
    );

    rerender({ p: { from: "2026-01-01T00:00:00Z", to: "2026-01-03T00:00:00Z" } });

    await waitFor(() =>
      expect(mockGetIngestionEventsHistogram).toHaveBeenCalledTimes(2),
    );
  });
});
