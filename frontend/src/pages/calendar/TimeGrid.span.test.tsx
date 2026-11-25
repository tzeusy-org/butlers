// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it } from "vitest";

import type { UnifiedCalendarEntry } from "@/api/types.ts";
import { bucketEntriesByDay } from "@/lib/calendar-span";
import { TimeGrid } from "@/pages/calendar/TimeGrid";

const TZ = "UTC";

function entry(
  id: string,
  start: string,
  end: string,
  allDay = false,
): UnifiedCalendarEntry {
  return {
    entry_id: id,
    title: id,
    start_at: start,
    end_at: end,
    all_day: allDay,
    status: "active",
    linked_people: [],
  } as unknown as UnifiedCalendarEntry;
}

function renderGrid(entries: UnifiedCalendarEntry[]) {
  const weekDays = [
    new Date(2026, 10, 23),
    new Date(2026, 10, 24),
    new Date(2026, 10, 25),
  ];
  return render(
    <TimeGrid
      range="week"
      weekDays={weekDays}
      entriesByDay={bucketEntriesByDay(entries, TZ)}
      overlaysEnabled={false}
      overlaysByDayMap={new Map()}
      overlapEntryIds={new Set()}
      defaultTimezone={TZ}
      timeGridRef={createRef<HTMLDivElement>()}
      onOpenDetail={() => {}}
    />,
  );
}

describe("TimeGrid multi-day spans", () => {
  it("shows an all-day span on each covered day with continuation arrows", () => {
    renderGrid([
      entry("Retreat", "2026-11-23T00:00:00Z", "2026-11-25T00:00:00Z", true),
    ]);
    const chips = document.querySelectorAll(
      '[data-calendar-entry-id="Retreat"]',
    );
    expect(chips).toHaveLength(2);
    expect(chips[0].textContent).toBe("Retreat → (continues next day)");
    expect(chips[1].textContent).toBe(
      "← (continued from previous day) Retreat",
    );
    // The arrow is decorative; the phrase is what assistive tech reads.
    expect(
      chips[0].querySelector('[aria-hidden="true"]')?.textContent,
    ).toContain("→");
    expect(chips[0].querySelector(".sr-only")?.textContent).toContain(
      "continues next day",
    );
    expect(chips[0].querySelector("[aria-label]")).toBeNull();
  });

  it("clips a timed span per day and marks the continuation", () => {
    renderGrid([
      entry("Flight", "2026-11-23T22:00:00Z", "2026-11-24T06:00:00Z"),
    ]);
    const blocks = document.querySelectorAll(
      '[data-calendar-entry-id="Flight"]',
    );
    expect(blocks).toHaveLength(2);
    expect(blocks[0].getAttribute("data-continues-to-next")).toBe("true");
    expect(blocks[1].getAttribute("data-continues-from-prev")).toBe("true");
  });

  it("places overlapping events side by side", () => {
    renderGrid([
      entry("A", "2026-11-23T09:00:00Z", "2026-11-23T11:00:00Z"),
      entry("B", "2026-11-23T10:00:00Z", "2026-11-23T12:00:00Z"),
    ]);
    expect(screen.getByTitle(/· A$/).getAttribute("style")).toContain(
      "width: calc(50% - 4px)",
    );
    expect(screen.getByTitle(/· B$/).getAttribute("style")).toContain(
      "left: calc(50% + 2px)",
    );
  });
});
