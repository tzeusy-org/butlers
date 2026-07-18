/**
 * Calendar projections are live-invalidated by the fleet bus. These queries
 * therefore use polling only as a reconciliation sweep, while retaining a
 * fast fallback whenever the socket is not healthy.
 */

import { useQuery } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...original,
    useQuery: vi.fn((options: unknown) => options),
  };
});

let status: "connecting" | "open" | "reconnecting" | "closed" = "open";
vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({ status, lastEventAt: null, subscribe: vi.fn() }),
}));

vi.mock("@/api/index.ts", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...original,
    getCalendarDayBriefing: vi.fn(),
    getCalendarWorkspace: vi.fn(),
  };
});

import { POLL_BUS_DOWN_FALLBACK_MS, POLL_BUS_RECONCILE_MS } from "@/lib/poll-policy";
import {
  useCalendarDayBriefing,
  useCalendarWorkspace,
} from "./use-calendar-workspace.ts";

function lastRefetchInterval(): unknown {
  const call = vi.mocked(useQuery).mock.calls.at(-1);
  return (call?.[0] as { refetchInterval?: unknown }).refetchInterval;
}

describe("calendar workspace bus-aware polling", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    status = "open";
  });

  it("uses the five-minute reconciliation sweep while the bus is open", () => {
    useCalendarWorkspace({
      view: "user",
      start: "2026-07-01T00:00:00Z",
      end: "2026-07-02T00:00:00Z",
    });

    expect(lastRefetchInterval()).toBe(POLL_BUS_RECONCILE_MS);
  });

  it("falls back to 30-second polling while the bus reconnects", () => {
    status = "reconnecting";
    useCalendarDayBriefing({ date: "2026-07-02" });

    expect(lastRefetchInterval()).toBe(POLL_BUS_DOWN_FALLBACK_MS);
  });
});
