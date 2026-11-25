// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { format } from "date-fns";
import { dayViewDoor } from "@/lib/calendar-doors";
import { MemoryRouter } from "react-router";
import { cleanup, render as mount, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import type { DayVerdictResponse } from "@/api/types";
import {
  CalendarVerdictOpener,
  type DayVerdictState,
} from "@/components/calendar/CalendarVerdictOpener";

function verdictFixture(overrides: Partial<DayVerdictResponse> = {}): DayVerdictResponse {
  return {
    available: true,
    date: "2026-11-27",
    zone: "Asia/Singapore",
    relative: "tomorrow",
    freshness: { state: "current", as_of: null, sources_unreadable: [], sources_total: 1 },
    current: null,
    next: null,
    committed_minutes: 0,
    committed_count: 0,
    tentative_count: 0,
    free_window: null,
    empty: true,
    ...overrides,
  };
}

function dayVerdict(
  overrides: Partial<DayVerdictResponse> = {},
  state: Partial<DayVerdictState> = {},
): DayVerdictState {
  return { data: verdictFixture(overrides), isLoading: false, isError: false, ...state };
}

const busy: Partial<DayVerdictResponse> = {
  empty: false,
  next: {
    ref: "inst-1",
    start: "2026-11-27T14:00:00+08:00",
    end: "2026-11-27T15:00:00+08:00",
    title: "Design review",
    people_count: 2,
    door: "/calendar?entry=inst-1",
  },
  committed_minutes: 150,
  committed_count: 3,
  free_window: {
    start: "2026-11-27T16:00:00+08:00",
    end: "2026-11-27T20:00:00+08:00",
    minutes: 240,
  },
};

function render(overrides: Partial<React.ComponentProps<typeof CalendarVerdictOpener>> = {}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <CalendarVerdictOpener
        dayVerdict={dayVerdict()}
        workspaceLoading={false}
        workspaceError={false}
        sourceFreshnessLoading={false}
        sourceFreshnessError={false}
        freshnessDetail={null}
        conflictScanEnabled={true}
        conflictLoading={false}
        conflictError={false}
        conflictsAvailable={true}
        conflicts={[]}
        {...overrides}
      />
    </MemoryRouter>,
  );
}

describe("CalendarVerdictOpener", () => {
  it("states a calm day only when the verdict is earned empty and every source is available", () => {
    const html = render();

    expect(html).toContain("Nothing on tomorrow (Fri 27 Nov), no scheduling conflicts in view");
    expect(html).toContain("calendar-verdict-all-clear");
  });

  it("opens with next commitment, committed hours and the longest free window, in that order", () => {
    const html = render({ dayVerdict: dayVerdict(busy) });

    expect(html).not.toContain("calendar-verdict-all-clear");
    const next = html.indexOf("Next Design review at 14:00");
    const committed = html.indexOf("2h 30m committed across 3 meetings");
    const free = html.indexOf("Longest free window 16:00-20:00");
    expect(next).toBeGreaterThan(-1);
    expect(committed).toBeGreaterThan(next);
    expect(free).toBeGreaterThan(committed);
    expect(html).toContain('href="/calendar?entry=inst-1"');
  });

  it("puts what needs the owner between the next commitment and committed hours", () => {
    const html = render({
      dayVerdict: dayVerdict(busy),
      invitations: [
        {
          entry_id: "inv-1",
          title: "Invite",
          start_at: "2026-11-27T09:00:00Z",
          end_at: "2026-11-27T10:00:00Z",
          timezone: "UTC",
          butler_name: "general",
          organizer: null,
          organizer_source: "unknown" as const,
          conflict_issues: [],
        },
      ],
    });

    const next = html.indexOf("Next Design review");
    const needs = html.indexOf("1 unanswered invitation");
    const committed = html.indexOf("committed across");
    expect(next).toBeGreaterThan(-1);
    expect(needs).toBeGreaterThan(next);
    expect(committed).toBeGreaterThan(needs);
  });

  it("makes committed hours and the free window doors to the calendar day view", () => {
    const html = render({ dayVerdict: dayVerdict(busy) });
    const day = "view=user&amp;range=day&amp;anchor=2026-11-27";

    // The committed-hours clause opens that date's day view.
    expect(html).toMatch(new RegExp(`href="/calendar\\?${day}"[^>]*>2h 30m committed`));
    // The free window opens it scrolled to the window's start, in the browser's own zone.
    const start = format(new Date("2026-11-27T16:00:00+08:00"), "HH:mm").replace(":", "%3A");
    expect(html).toMatch(
      new RegExp(`href="/calendar\\?${day}&amp;time=${start}"[^>]*>Longest free window`),
    );
  });

  it("builds a day-view door without a time when none is given", () => {
    expect(dayViewDoor("2026-11-27")).toBe("/calendar?view=user&range=day&anchor=2026-11-27");
  });

  it("makes the next clause a door that selects the entry", async () => {
    try {
      mount(
        <MemoryRouter>
          <CalendarVerdictOpener
            dayVerdict={dayVerdict(busy)}
            workspaceLoading={false}
            workspaceError={false}
            sourceFreshnessLoading={false}
            sourceFreshnessError={false}
            freshnessDetail={null}
            conflictScanEnabled={true}
            conflictLoading={false}
            conflictError={false}
            conflictsAvailable={true}
            conflicts={[]}
          />
        </MemoryRouter>,
      );
      const link = screen.getByRole("link", { name: /Next Design review at 14:00/ });
      expect(link.getAttribute("href")).toBe("/calendar?entry=inst-1");
    } finally {
      cleanup();
    }
  });

  it("names a busy day with a stale read as possibly out of date", () => {
    const html = render({
      dayVerdict: dayVerdict({
        ...busy,
        free_window: null,
        freshness: {
          state: "stale",
          as_of: "2026-11-26T20:00:00+08:00",
          sources_unreadable: [],
          sources_total: 1,
        },
      }),
    });

    expect(html).toContain("calendar read as of 20:00, may be out of date");
    expect(html).not.toContain("Longest free window");
  });

  it("never calls a day clear when the verdict is unavailable or failed", () => {
    const unavailable = render({
      dayVerdict: dayVerdict({ available: false, empty: false }),
    });
    expect(unavailable).toContain("calendar day verdict unavailable");
    expect(unavailable).not.toContain("calendar-verdict-all-clear");
    expect(unavailable).not.toContain("Nothing on");

    const failed = render({ dayVerdict: { data: null, isLoading: false, isError: true } });
    expect(failed).toContain("calendar day verdict unavailable");
    expect(failed).not.toContain("calendar-verdict-all-clear");
    expect(failed).not.toMatch(/clear|quiet|nothing on/i);
  });

  it("shows a skeleton, not a verdict, while the day verdict loads", () => {
    const html = render({ dayVerdict: { data: null, isLoading: true, isError: false } });

    expect(html).not.toContain("calendar-verdict-all-clear");
    expect(html).toContain("calendar-verdict-skeleton");
  });

  it("does not call a day with only tentative holds empty", () => {
    const html = render({ dayVerdict: dayVerdict({ empty: false, tentative_count: 1 }) });

    expect(html).toContain("No committed meetings tomorrow (Fri 27 Nov)");
    expect(html).not.toContain("Nothing on");
  });

  it("names an unavailable conflict scan instead of calling the week quiet", () => {
    const html = render({ conflictsAvailable: false });

    expect(html).toContain("calendar conflict scan unavailable");
    expect(html).not.toContain("calendar-verdict-all-clear");
    const unknown = render({ invitationsAvailable: false });
    expect(unknown).toContain("calendar invitations unavailable");
    expect(unknown).not.toContain("No unanswered invitations.");
    expect(unknown).not.toContain("calendar-verdict-all-clear");
    const loading = render({ invitationsLoading: true });
    expect(loading).toContain("Loading invitations");
    expect(loading).not.toContain("No unanswered invitations.");
    expect(loading).not.toContain("calendar-verdict-all-clear");
    const refreshing = render({ invitationsRefreshing: true });
    expect(refreshing).toContain("Refreshing invitations");
    expect(refreshing).not.toContain("No unanswered invitations.");
    expect(refreshing).not.toContain("calendar-verdict-all-clear");
  });

  it("adds a concrete conflict clause when the scan finds conflicts", () => {
    const html = render({ conflicts: [{} as never] });

    expect(html).toContain("1 scheduling conflict in view");
    expect(html).not.toContain("calendar-verdict-all-clear");
  });

  it("opens only actual invitation and radar evidence doors with the keyboard", async () => {
    const open = vi.fn();
    const more = vi.fn();
    const issue = { kind: "overlap" as const, date: "2026-07-01", summary: "Actual overlap",
      severity: "warning" as const, proposal_ids: [], events: [{ entry_id: "invited", title: "Invitation",
        start_at: "2026-07-01T09:00:00Z", end_at: "2026-07-01T10:00:00Z", timezone: "UTC", status: "confirmed" }] };
    const props = { dayVerdict: dayVerdict(), workspaceLoading: false,
      workspaceError: false, sourceFreshnessLoading: false, sourceFreshnessError: false,
      freshnessDetail: null, conflictScanEnabled: true, conflictLoading: false, conflictError: false,
      conflictsAvailable: true, conflicts: [], invitations: [{ entry_id: "invited", title: "Invitation",
        start_at: "2026-07-01T09:00:00Z", end_at: "2026-07-01T10:00:00Z", timezone: "UTC", butler_name: "general",
        organizer: null, organizer_source: "unknown" as const, conflict_issues: [issue] }],
      invitationsHasMore: true, onOpenInvitation: open, onMoreInvitations: more };
    try {
      mount(<MemoryRouter><CalendarVerdictOpener {...props} /></MemoryRouter>);
      const user = userEvent.setup();
      expect(screen.getByText("Organizer unknown")).toBeTruthy();
      expect(screen.queryByTestId("calendar-verdict-all-clear")).toBeNull();
      await user.tab(); // the "unanswered invitation" clause, a door to the list below
      expect(document.activeElement?.textContent).toMatch(/unanswered invitation/);
      await user.tab();
      await user.keyboard("{Enter}");
      expect(open).toHaveBeenCalledWith("invited");
      await user.tab();
      await user.keyboard("{Enter}");
      expect(screen.getByRole("region", { name: "Invitation conflict evidence" }).textContent).toContain("Actual overlap");
      await user.click(screen.getByRole("button", { name: "More invitations" }));
      expect(more).toHaveBeenCalledTimes(1);
    } finally { cleanup(); }
  });
});
