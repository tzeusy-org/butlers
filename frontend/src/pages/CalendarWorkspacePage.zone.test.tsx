// @vitest-environment jsdom

import { describe, expect, it, vi } from "vitest";

import { installCalendarPageHarness } from "@/test/calendar-page/harness";
import { setWorkspaceMetaState, setWorkspaceState } from "@/test/calendar-page/state";

vi.mock("@/hooks/use-meeting-debriefs", async () =>
  (await import("@/test/calendar-page/mocks")).meetingDebriefsMock(),
);
vi.mock("@/hooks/use-calendar-workspace", async () =>
  (await import("@/test/calendar-page/mocks")).calendarWorkspaceHookMock(),
);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function entry(id: string, title: string, start: string, end: string, sourceTz: string | null) {
  return {
    entry_id: id,
    event_id: `evt-${id}`,
    view: "user" as const,
    source_type: "provider_event" as const,
    source_key: "provider:google:primary",
    title,
    start_at: start,
    end_at: end,
    timezone: "Australia/Sydney",
    source_timezone: sourceTz,
    all_day: false,
    calendar_id: "primary",
    provider_event_id: `p-${id}`,
    butler_name: null,
    schedule_id: null,
    reminder_id: null,
    rrule: null,
    cron: null,
    until_at: null,
    status: "confirmed",
    sync_state: "fresh" as const,
    editable: false,
    metadata: {},
  };
}

describe("CalendarWorkspacePage zone-honest times", () => {
  const { container, renderPage } = installCalendarPageHarness();

  it("shows a London event's own 09:00 beside the Sydney clock, and nothing on a Sydney event", () => {
    setWorkspaceMetaState(undefined, { defaultTimezone: "Australia/Sydney" });
    setWorkspaceState({
      data: {
        data: {
          entries: [
            // 08:00Z is 09:00 in London (BST) and 18:00 in Sydney.
            entry("london", "Supplier call", "2026-07-06T18:00:00+10:00", "2026-07-06T19:00:00+10:00", "Europe/London"),
            entry("sydney", "Standup", "2026-07-06T12:00:00+10:00", "2026-07-06T13:00:00+10:00", "Australia/Sydney"),
            entry("unknown", "Mystery", "2026-07-07T12:00:00+10:00", "2026-07-07T13:00:00+10:00", null),
          ],
          source_freshness: [],
          lanes: [],
          next_cursor: null,
          has_more: false,
        },
        meta: {},
      },
    });

    renderPage("/calendar?view=user&range=week&anchor=2026-07-06");

    const london = container.querySelector('[data-calendar-entry-id="london"]');
    const sydney = container.querySelector('[data-calendar-entry-id="sydney"]');
    const unknown = container.querySelector('[data-calendar-entry-id="unknown"]');
    expect(london?.querySelector('[data-testid="source-zone-time"]')?.textContent).toContain(
      "09:00 London",
    );
    expect(london?.textContent).toContain("18:00");
    expect(sydney).not.toBeNull();
    expect(sydney?.querySelector('[data-testid="source-zone-time"]')).toBeNull();
    // An unknown zone adds no label in the grid: it is never guessed.
    expect(unknown?.querySelector('[data-testid="source-zone-time"]')).toBeNull();
  });
});
