// @vitest-environment jsdom
/**
 * CalendarAgendaView — RTL tests (bu-8yi687).
 *
 * Covers:
 *  - Entries grouped by day, in chronological order, with time labels.
 *  - The BUTLER: title prefix is preserved verbatim in the printed agenda.
 *  - All-day entries read "All day"; locations render when present.
 *  - The Print button invokes window.print; Close fires onClose.
 *  - Empty state renders an explicit "No events" line.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import type { UnifiedCalendarEntry } from "@/api/types.ts";

import { groupEntriesByDay } from "@/lib/calendar-agenda.ts";

import { CalendarAgendaView } from "./CalendarAgendaView.tsx";

afterEach(cleanup);

function entry(overrides: Partial<UnifiedCalendarEntry> = {}): UnifiedCalendarEntry {
  return {
    entry_id: "00000000-0000-0000-0000-000000000001",
    event_id: "00000000-0000-0000-0000-0000000000a1",
    view: "user",
    source_type: "provider_event",
    source_key: "provider:google:primary",
    title: "Team sync",
    start_at: "2026-02-22T14:00:00Z",
    end_at: "2026-02-22T15:00:00Z",
    timezone: "UTC",
    all_day: false,
    calendar_id: "primary",
    provider_event_id: "evt-1",
    butler_name: null,
    schedule_id: null,
    reminder_id: null,
    rrule: null,
    cron: null,
    until_at: null,
    status: "active",
    sync_state: "fresh",
    editable: true,
    metadata: {},
    source_butler: null,
    source_session_id: null,
    ...overrides,
  };
}

function renderAgenda(entries: UnifiedCalendarEntry[], onClose = vi.fn()) {
  render(
    <CalendarAgendaView
      entries={entries}
      rangeLabel="Feb 22 – Feb 28"
      timezone="UTC"
      view="user"
      onClose={onClose}
    />,
  );
  return onClose;
}

describe("CalendarAgendaView", () => {
  it("renders entries grouped by day with time labels and the range headline", () => {
    // Three days apart + midday UTC keeps the two entries on distinct local
    // calendar days in any plausible test timezone (CI=UTC, dev=+08).
    renderAgenda([
      entry({ start_at: "2026-02-22T12:00:00Z", end_at: "2026-02-22T13:00:00Z" }),
      entry({
        entry_id: "id-2",
        title: "Lunch",
        start_at: "2026-02-25T12:00:00Z",
        end_at: "2026-02-25T13:00:00Z",
      }),
    ]);

    expect(screen.getByText(/Agenda · Feb 22 – Feb 28/)).toBeTruthy();
    expect(screen.getByText("Team sync")).toBeTruthy();
    expect(screen.getByText("Lunch")).toBeTruthy();
    // Two distinct day sections (headings rendered as <h2>).
    const headings = screen.getAllByRole("heading", { level: 2 });
    expect(headings.length).toBe(2);
    // Event count summary.
    expect(screen.getByText(/2 events/)).toBeTruthy();
  });

  it("preserves the BUTLER: title prefix verbatim", () => {
    renderAgenda([entry({ title: "BUTLER: Daily standup" })]);
    expect(screen.getByText("BUTLER: Daily standup")).toBeTruthy();
  });

  it("labels all-day entries and shows locations", () => {
    renderAgenda([
      entry({ all_day: true, title: "Conference", metadata: { location: "Hall A" } }),
    ]);
    expect(screen.getByText("All day")).toBeTruthy();
    expect(screen.getByText(/Hall A/)).toBeTruthy();
  });

  it("invokes window.print when Print is clicked", () => {
    const printSpy = vi.fn();
    vi.stubGlobal("print", printSpy);
    renderAgenda([entry()]);
    fireEvent.click(screen.getByRole("button", { name: "Print" }));
    expect(printSpy).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });

  it("fires onClose when Close is clicked", () => {
    const onClose = renderAgenda([entry()]);
    fireEvent.click(screen.getByRole("button", { name: "Close agenda" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders an explicit empty-state when there are no entries", () => {
    renderAgenda([]);
    expect(screen.getByText("No events in this range.")).toBeTruthy();
  });
});

describe("groupEntriesByDay", () => {
  it("groups by day and sorts days and intra-day entries chronologically", () => {
    // Day 1 has two entries ~2h apart around midday UTC (same local day in any
    // plausible tz); day 2 is three days later (always a distinct group).
    const days = groupEntriesByDay([
      entry({ entry_id: "b", start_at: "2026-02-25T12:00:00Z", end_at: "2026-02-25T13:00:00Z" }),
      entry({ entry_id: "a", start_at: "2026-02-22T13:00:00Z", end_at: "2026-02-22T14:00:00Z" }),
      entry({ entry_id: "c", start_at: "2026-02-22T11:00:00Z", end_at: "2026-02-22T12:00:00Z" }),
    ]);
    expect(days.length).toBe(2);
    // Days are sorted ascending by their local-day key.
    expect(days[0].key < days[1].key).toBe(true);
    // The later day holds the single entry "b".
    expect(days[1].entries.map((e) => e.entry_id)).toEqual(["b"]);
    // Within the first day, the 11:00 entry precedes the 13:00 entry.
    expect(days[0].entries.map((e) => e.entry_id)).toEqual(["c", "a"]);
  });
});
