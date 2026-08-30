// @vitest-environment jsdom

/**
 * Calendar workspace keyed-query never-blank coverage (bu-plib7).
 *
 * Each of these hooks changes query key as the user changes a time window,
 * selected event, search phrase, or audit page. They must retain the previous
 * result while the next request is in flight; their consumers then use
 * FetchingDim and still give a terminal error precedence over that data.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...original,
    useQuery: vi.fn(() => ({ data: undefined, isFetching: false })),
  };
});

vi.mock("@/api/index.ts", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...original,
    getCalendarDayBriefing: vi.fn(),
    getCalendarMeetingPrep: vi.fn(),
    getCalendarWorkspace: vi.fn(),
    getCalendarWorkspaceAudit: vi.fn(),
    getCalendarWorkspaceConflicts: vi.fn(),
    getCalendarWorkspaceDuplicates: vi.fn(),
    searchCalendarWorkspace: vi.fn(),
  };
});

vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({
    status: "open",
    health: "healthy",
    lastEventAt: null,
    subscribe: vi.fn(),
  }),
}));

import { useQuery } from "@tanstack/react-query";
import {
  useCalendarConflicts,
  useCalendarDayBriefing,
  useCalendarDuplicates,
  useCalendarMeetingPrep,
  useCalendarOverlays,
  useCalendarWorkspaceAudit,
  useCalendarWorkspaceSearch,
} from "./use-calendar-workspace.ts";

const mockUseQuery = vi.mocked(useQuery);

function lastQueryOptions(): { placeholderData?: (previousData: unknown) => unknown } {
  const options = mockUseQuery.mock.calls.at(-1)?.[0];
  expect(options).toBeDefined();
  return options as ReturnType<typeof lastQueryOptions>;
}

const keyedHooks: Array<{ name: string; useHook: () => void }> = [
  {
    name: "overlays window",
    useHook: () => {
      useCalendarOverlays({
        start: "2026-07-01T00:00:00Z",
        end: "2026-07-08T00:00:00Z",
        timezone: "UTC",
      });
    },
  },
  {
    name: "day briefing parameters",
    useHook: () => {
      useCalendarDayBriefing({ date: "2026-07-02", timezone: "UTC" });
    },
  },
  {
    name: "meeting-prep event",
    useHook: () => {
      useCalendarMeetingPrep("event-1");
    },
  },
  {
    name: "workspace search phrase",
    useHook: () => {
      useCalendarWorkspaceSearch({ q: "planning", view: "user", timezone: "UTC" });
    },
  },
  {
    name: "duplicate-review window",
    useHook: () => {
      useCalendarDuplicates({
        view: "user",
        start: "2026-07-01T00:00:00Z",
        end: "2026-07-08T00:00:00Z",
        timezone: "UTC",
      });
    },
  },
  {
    name: "conflict-radar window",
    useHook: () => {
      useCalendarConflicts({
        start: "2026-07-01T00:00:00Z",
        end: "2026-07-08T00:00:00Z",
        timezone: "UTC",
      });
    },
  },
  {
    name: "activity audit page",
    useHook: () => {
      useCalendarWorkspaceAudit({ limit: 50, offset: 50 });
    },
  },
];

describe("calendar workspace keyed hooks — never-blank floor (bu-plib7)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it.each(keyedHooks)("keeps previous data for the $name", ({ useHook }) => {
    renderHook(useHook);

    const options = lastQueryOptions();
    const previous = { data: { marker: "previous result" } };
    expect(typeof options.placeholderData).toBe("function");
    expect(options.placeholderData?.(previous)).toBe(previous);
  });
});
