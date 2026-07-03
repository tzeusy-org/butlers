// @vitest-environment jsdom
/**
 * Tests for useTimelineLedger — the live-tail + pagination state powering
 * the rebuilt /timeline fleet chronicle (bu-86c4c.10).
 *
 * Covers:
 * - Pinned-to-now rendering: the freshest head page is shown directly.
 * - Load older: commits a snapshot, appends an older page, unpins.
 * - New-events counting while unpinned (does not silently reorder the list).
 * - showNewEvents(): jumps back to the live head, discards accumulated history.
 * - A filter change resets pinned/committed state.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import type { TimelineEvent, TimelineResponse } from "@/api/types.ts";

const mockGetTimeline = vi.fn();

vi.mock("@/api/index.ts", () => ({
  getTimeline: (...args: unknown[]) => mockGetTimeline(...args),
}));

import { useTimelineLedger } from "./use-timeline-ledger";

function makeEvent(id: string, timestamp: string, overrides: Partial<TimelineEvent> = {}): TimelineEvent {
  return {
    id,
    type: "session",
    butler: "home",
    timestamp,
    summary: `event ${id}`,
    is_heartbeat: false,
    data: {},
    ...overrides,
  };
}

function response(events: TimelineEvent[], meta: Partial<TimelineResponse["meta"]> = {}): TimelineResponse {
  return {
    data: events,
    meta: {
      cursor: null,
      has_more: false,
      heartbeat_rollup: { ticks: 0, butlers: 0, failed: 0 },
      degraded_sources: [],
      ...meta,
    },
  };
}

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return {
    client,
    Wrapper: function Wrapper({ children }: { children: ReactNode }) {
      return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useTimelineLedger", () => {
  it("renders the live head page directly while pinned", async () => {
    const page1 = [makeEvent("e3", "2026-07-04T14:32:00Z"), makeEvent("e2", "2026-07-04T14:31:00Z")];
    mockGetTimeline.mockResolvedValue(response(page1, { has_more: true, cursor: "cur-1" }));

    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useTimelineLedger({}), { wrapper: Wrapper });

    await waitFor(() => expect(result.current.events).toHaveLength(2));
    expect(result.current.pinned).toBe(true);
    expect(result.current.hasMore).toBe(true);
    expect(result.current.newCount).toBe(0);
  });

  it("loadMore commits a snapshot, appends the older page, and unpins", async () => {
    const page1 = [makeEvent("e2", "2026-07-04T14:32:00Z")];
    const olderPage = response([makeEvent("e1", "2026-07-04T13:00:00Z")], { has_more: false });
    mockGetTimeline.mockResolvedValueOnce(response(page1, { has_more: true, cursor: "cur-1" }));
    mockGetTimeline.mockResolvedValueOnce(olderPage);

    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useTimelineLedger({}), { wrapper: Wrapper });

    await waitFor(() => expect(result.current.events).toHaveLength(1));

    act(() => {
      result.current.loadMore();
    });

    await waitFor(() => expect(result.current.events).toHaveLength(2));
    expect(result.current.pinned).toBe(false);
    expect(result.current.events.map((e) => e.id)).toEqual(["e2", "e1"]);
    expect(result.current.hasMore).toBe(false);
  });

  it("counts newly-arrived head events while unpinned instead of merging them silently", async () => {
    const page1 = [makeEvent("e2", "2026-07-04T14:32:00Z")];
    mockGetTimeline.mockResolvedValueOnce(response(page1, { has_more: true, cursor: "cur-1" }));
    mockGetTimeline.mockResolvedValueOnce(response(page1, { has_more: true, cursor: "cur-1" })); // loadMore's own fetch (same baseline, no older page in this test)

    const { client, Wrapper } = makeWrapper();
    const { result } = renderHook(() => useTimelineLedger({}), { wrapper: Wrapper });

    await waitFor(() => expect(result.current.events).toHaveLength(1));

    act(() => {
      result.current.loadMore();
    });
    await waitFor(() => expect(result.current.pinned).toBe(false));

    // A new event lands — subsequent head-query fetches return it.
    const page1WithNew = [makeEvent("e3", "2026-07-04T14:40:00Z"), ...page1];
    mockGetTimeline.mockResolvedValue(response(page1WithNew, { has_more: true, cursor: "cur-1" }));

    await act(async () => {
      await client.invalidateQueries({ queryKey: ["timeline"] });
    });

    await waitFor(() => expect(result.current.newCount).toBe(1));
    // The rendered list itself does not silently grow while unpinned.
    expect(result.current.events).toHaveLength(2);
  });

  it("showNewEvents jumps back to the live head and clears newCount", async () => {
    const page1 = [makeEvent("e2", "2026-07-04T14:32:00Z")];
    mockGetTimeline.mockResolvedValueOnce(response(page1, { has_more: true, cursor: "cur-1" }));
    mockGetTimeline.mockResolvedValueOnce(response(page1, { has_more: true, cursor: "cur-1" }));

    const { client, Wrapper } = makeWrapper();
    const { result } = renderHook(() => useTimelineLedger({}), { wrapper: Wrapper });

    await waitFor(() => expect(result.current.events).toHaveLength(1));
    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.pinned).toBe(false));

    const page1WithNew = [makeEvent("e3", "2026-07-04T14:40:00Z"), ...page1];
    mockGetTimeline.mockResolvedValue(response(page1WithNew, { has_more: true, cursor: "cur-1" }));
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["timeline"] });
    });
    await waitFor(() => expect(result.current.newCount).toBe(1));

    act(() => result.current.showNewEvents());

    expect(result.current.pinned).toBe(true);
    expect(result.current.newCount).toBe(0);
    await waitFor(() => expect(result.current.events.map((e) => e.id)).toEqual(["e3", "e2"]));
  });

  it("resets pinned/committed state when filters change", async () => {
    const page1 = [makeEvent("e2", "2026-07-04T14:32:00Z")];
    const olderPage = response([makeEvent("e1", "2026-07-04T13:00:00Z")], { has_more: false });
    mockGetTimeline.mockResolvedValueOnce(response(page1, { has_more: true, cursor: "cur-1" }));
    mockGetTimeline.mockResolvedValueOnce(olderPage);
    mockGetTimeline.mockResolvedValue(response([makeEvent("e4", "2026-07-04T15:00:00Z")]));

    const { Wrapper } = makeWrapper();
    const { result, rerender } = renderHook(({ f }) => useTimelineLedger(f), {
      wrapper: Wrapper,
      initialProps: { f: {} as { event_type?: string[] } },
    });

    await waitFor(() => expect(result.current.events).toHaveLength(1));
    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.pinned).toBe(false));

    rerender({ f: { event_type: ["error"] } });

    await waitFor(() => expect(result.current.pinned).toBe(true));
    expect(result.current.newCount).toBe(0);
  });
});
