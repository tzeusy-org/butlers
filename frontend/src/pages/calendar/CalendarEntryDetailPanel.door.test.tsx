// @vitest-environment jsdom
/** The drawer offers a door to the record a claim row was written for. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import type { UnifiedCalendarEntry } from "@/api/types.ts";

vi.mock("@/hooks/use-calendar-workspace", () => ({
  useCalendarWorkspaceEntry: () => ({ data: undefined }),
  useMutateCalendarWorkspaceButlerEvent: () => ({ isPending: false, mutateAsync: vi.fn() }),
}));
vi.mock("@/components/calendar/MeetingPrepRail", () => ({
  MeetingPrepRailContainer: () => null,
}));

import { CalendarEntryDetailPanel } from "./CalendarEntryDetailPanel";

afterEach(cleanup);

function row(claim: Record<string, unknown> | null): UnifiedCalendarEntry {
  return {
    entry_id: "00000000-0000-0000-0000-000000000009",
    view: "butler",
    source_type: "butler_reminder",
    source_key: "finance",
    title: "Electric Co",
    start_at: "2026-02-22T09:00:00+08:00",
    end_at: "2026-02-22T09:15:00+08:00",
    timezone: "Asia/Singapore",
    all_day: false,
    butler_name: "finance",
    status: "confirmed",
    editable: false,
    metadata: { event_metadata: claim ? { claim } : {} },
    linked_people: [],
  } as unknown as UnifiedCalendarEntry;
}

function renderPanel(entry: UnifiedCalendarEntry) {
  return render(
    <MemoryRouter>
      <CalendarEntryDetailPanel
        entry={entry}
        onClose={() => {}}
        butlerMutation={{ isPending: false } as never}
        timezone="Asia/Singapore"
      />
    </MemoryRouter>,
  );
}

describe("claim door", () => {
  it("links to the record a claim row was written for", () => {
    renderPanel(row({ claim_key: "finance:bill:b1", door: { route: "/finance/bills", ref: "b1" } }));
    const door = screen.getByTestId("detail-claim-door");
    expect(door.getAttribute("href")).toBe("/finance/bills");
  });

  it("shows no door for a row without a claim", () => {
    renderPanel(row(null));
    expect(screen.queryByTestId("detail-claim-door")).toBeNull();
  });
});
