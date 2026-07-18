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
  useCalendarConflicts,
  useCalendarDayBriefing,
  useCalendarDuplicates,
  useCalendarOverlays,
  useCalendarProposals,
  useCalendarWorkspaceAudit,
  useCalendarWorkspaceEntry,
  useCalendarWorkspaceMeta,
  useCalendarWorkspaceSearch,
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

  const calendarBusCoveredViews = [
    {
      name: "workspace",
      invoke: () =>
        useCalendarWorkspace({
          view: "user",
          start: "2026-07-01T00:00:00Z",
          end: "2026-07-02T00:00:00Z",
        }),
    },
    {
      name: "overlays",
      invoke: () =>
        useCalendarOverlays({
          start: "2026-07-01T00:00:00Z",
          end: "2026-07-02T00:00:00Z",
        }),
    },
    { name: "day briefing", invoke: () => useCalendarDayBriefing({ date: "2026-07-02" }) },
    {
      name: "proposals",
      invoke: () =>
        useCalendarProposals({
          start: "2026-07-01T00:00:00Z",
          end: "2026-07-02T00:00:00Z",
        }),
    },
    {
      name: "search",
      invoke: () => useCalendarWorkspaceSearch({ q: "planning", view: "user" }),
    },
    { name: "workspace metadata", invoke: () => useCalendarWorkspaceMeta() },
    { name: "entry", invoke: () => useCalendarWorkspaceEntry("entry-1") },
    {
      name: "duplicates",
      invoke: () =>
        useCalendarDuplicates({
          view: "user",
          start: "2026-07-01T00:00:00Z",
          end: "2026-07-02T00:00:00Z",
        }),
    },
    {
      name: "conflicts",
      invoke: () =>
        useCalendarConflicts({
          start: "2026-07-01T00:00:00Z",
          end: "2026-07-02T00:00:00Z",
        }),
    },
    { name: "audit", invoke: () => useCalendarWorkspaceAudit() },
  ];

  it.each(calendarBusCoveredViews)(
    "uses the five-minute reconciliation sweep for bus-covered $name while connected",
    ({ invoke }) => {
      invoke();

      expect(lastRefetchInterval()).toBe(POLL_BUS_RECONCILE_MS);
    },
  );

  it.each(calendarBusCoveredViews)(
    "uses the 30-second fallback for bus-covered $name while disconnected",
    ({ invoke }) => {
      status = "closed";
      invoke();

      expect(lastRefetchInterval()).toBe(POLL_BUS_DOWN_FALLBACK_MS);
    },
  );
});
