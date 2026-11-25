// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getVerdict = vi.fn();
vi.mock("@/api/index.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/index.ts")>()),
  getCalendarDayVerdict: () => getVerdict(),
}));
vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({ status: "open", health: "healthy", lastEventAt: null, subscribe: vi.fn() }),
}));

import { useDayVerdict } from "./use-calendar-workspace.ts";

const verdict = (end: string) => ({
  data: {
    available: true,
    date: "2026-11-26",
    zone: "Asia/Singapore",
    relative: "today",
    freshness: { state: "current", as_of: null, sources_unreadable: [], sources_total: 1 },
    current: { ref: "a", start: "2026-11-26T14:00:00+08:00", end, title: "t", people_count: 0, door: "/x" },
    next: null,
    committed_minutes: 60,
    committed_count: 1,
    tentative_count: 0,
    free_window: null,
    empty: false,
  },
});

beforeEach(() => {
  getVerdict.mockReset();
});
afterEach(() => vi.useRealTimers());

describe("useDayVerdict", () => {
  it("re-reads exactly when the current meeting ends, and keeps re-arming", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-26T06:20:00Z")); // 14:20 SGT
    getVerdict.mockResolvedValue(verdict("2026-11-26T15:00:00+08:00"));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    // The poll sweep is off so only the boundary timer can cause a re-read.
    renderHook(() => useDayVerdict({}, { refetchInterval: false }), { wrapper });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(getVerdict).toHaveBeenCalledTimes(1);

    // Just before 15:00 nothing re-reads; at 15:00 it does.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(39 * 60_000);
    });
    expect(getVerdict).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2 * 60_000);
    });
    expect(getVerdict).toHaveBeenCalledTimes(2);

    // An unchanged answer must still re-arm the next boundary (18:00 here).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3 * 3600_000);
    });
    expect(getVerdict).toHaveBeenCalledTimes(3);
  });
});
