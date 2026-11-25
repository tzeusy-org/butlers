/**
 * The workspace hook walks the keyset cursor to completion (bu-5ii4gj.4.2).
 *
 * The walk is capped at 20 pages so a runaway window cannot loop forever. When the cap ends it
 * with rows still behind it, `has_more` must stay true so the page can say the window is cut
 * short, and the same-title groups held back on different pages must merge rather than overwrite.
 */

import { useQuery } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return { ...original, useQuery: vi.fn((options: unknown) => options) };
});

vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({ status: "open", health: "healthy", lastEventAt: null, subscribe: vi.fn() }),
}));

vi.mock("@/api/index.ts", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/index.ts")>();
  return { ...original, getCalendarWorkspace: vi.fn() };
});

import { getCalendarWorkspace } from "@/api/index.ts";
import type { CalendarSuppressedGroup, UnifiedCalendarEntry } from "@/api/types.ts";
import { useCalendarWorkspace } from "./use-calendar-workspace.ts";

const mockGet = vi.mocked(getCalendarWorkspace);

function entry(id: string): UnifiedCalendarEntry {
  return { entry_id: id, title: `Event ${id}` } as unknown as UnifiedCalendarEntry;
}

function group(day: string, title: string, ids: string[]): CalendarSuppressedGroup {
  return { day, title, count: ids.length, entries: ids.map(entry) };
}

/** Page `n` of a window that never ends: every page says more remain. */
function endlessPage(n: number) {
  return {
    data: {
      entries: [entry(`e${n}`)],
      suppressed_groups: [group("2026-07-01", "Standup", [`s${n}a`, `s${n}b`])],
      source_freshness: [],
      lanes: [],
      next_cursor: `cursor-${n + 1}`,
      has_more: true,
    },
    meta: {},
  };
}

const PARAMS = {
  view: "user" as const,
  start: "2026-07-01T00:00:00Z",
  end: "2026-07-02T00:00:00Z",
};

/** Runs the query function the hook handed to `useQuery`; call the hook first. */
async function runQuery() {
  const options = vi.mocked(useQuery).mock.calls.at(-1)?.[0] as unknown as {
    queryFn: () => Promise<{
      data: {
        entries: UnifiedCalendarEntry[];
        suppressed_groups: CalendarSuppressedGroup[];
        has_more: boolean;
      };
    }>;
  };
  return options.queryFn();
}

describe("workspace page walk", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("stops at the page cap, keeps has_more true and merges suppressed groups across pages", async () => {
    let n = 0;
    mockGet.mockImplementation(async () => endlessPage(n++) as never);

    useCalendarWorkspace(PARAMS);
    const result = await runQuery();

    // 21 pages are on offer, but the walk follows only 20.
    expect(mockGet).toHaveBeenCalledTimes(20);
    expect(result.data.entries).toHaveLength(20);
    expect(result.data.has_more).toBe(true);
    // One group, merged: two entries from each of the 20 pages.
    expect(result.data.suppressed_groups).toHaveLength(1);
    expect(result.data.suppressed_groups[0].count).toBe(40);
    expect(result.data.suppressed_groups[0].entries).toHaveLength(40);
  });

  it("follows the cursor from each page to the next", async () => {
    let n = 0;
    mockGet.mockImplementation(async () => endlessPage(n++) as never);

    useCalendarWorkspace(PARAMS);
    await runQuery();

    expect(mockGet.mock.calls[0][0].cursor).toBeUndefined();
    expect(mockGet.mock.calls[1][0].cursor).toBe("cursor-1");
    expect(mockGet.mock.calls[19][0].cursor).toBe("cursor-19");
  });

  it("reports a complete window when the last page says nothing is left", async () => {
    mockGet
      .mockResolvedValueOnce(endlessPage(0) as never)
      .mockResolvedValueOnce({
        data: {
          entries: [entry("last")],
          suppressed_groups: [group("2026-07-01", "Standup", ["tail"])],
          source_freshness: [],
          lanes: [],
          next_cursor: null,
          has_more: false,
        },
        meta: {},
      } as never);

    useCalendarWorkspace(PARAMS);
    const result = await runQuery();

    expect(mockGet).toHaveBeenCalledTimes(2);
    expect(result.data.has_more).toBe(false);
    expect(result.data.suppressed_groups[0].count).toBe(3);
  });
});
