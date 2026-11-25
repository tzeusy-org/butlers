// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getNow = vi.fn();
vi.mock("@/api/client.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client.ts")>()),
  getNow: () => getNow(),
}));

import { useNow } from "./use-now.ts";

const card = (freshUntil: string) => ({
  data: {
    as_of: "2026-11-26T06:00:00Z",
    headline: "",
    all_current: true,
    text: "",
    lines: [
      {
        kind: "owner.agenda",
        label: "Next",
        state: "known",
        text: "Free until 16:00",
        stale: false,
        source: "calendar",
        age_seconds: 0,
        door: null,
        fresh_until: freshUntil,
      },
    ],
  },
});

beforeEach(() => {
  getNow.mockReset();
});
afterEach(() => vi.useRealTimers());

describe("useNow", () => {
  it("re-reads when the agenda line's freshness ends, and keeps re-arming", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-26T06:00:00Z"));
    getNow.mockResolvedValue(card("2026-11-26T06:30:00Z"));
    const client = new QueryClient();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    renderHook(() => useNow(), { wrapper });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(getNow).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(29 * 60_000);
    });
    expect(getNow).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2 * 60_000);
    });
    expect(getNow).toHaveBeenCalledTimes(2);

    // The new answer is already expired, so an unchanged card must not stop the re-reads.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(getNow.mock.calls.length).toBeGreaterThanOrEqual(3);
  });
});
