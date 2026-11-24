// @vitest-environment jsdom

import { describe, expect, it, vi } from "vitest";

import { installCalendarPageHarness } from "@/test/calendar-page/harness";
import { setWorkspaceState } from "@/test/calendar-page/state";

vi.mock("@/hooks/use-meeting-debriefs", async () =>
  (await import("@/test/calendar-page/mocks")).meetingDebriefsMock(),
);
vi.mock("@/hooks/use-calendar-workspace", async () =>
  (await import("@/test/calendar-page/mocks")).calendarWorkspaceHookMock(),
);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function entry(i: number) {
  return {
    entry_id: `standup-${i}`,
    event_id: `evt-standup-${i}`,
    view: "user" as const,
    source_type: "provider_event" as const,
    source_key: "provider:google:primary",
    title: "Standup",
    start_at: `2026-03-01T${String(i % 24).padStart(2, "0")}:00:00Z`,
    end_at: `2026-03-01T${String(i % 24).padStart(2, "0")}:15:00Z`,
    timezone: "UTC",
    all_day: false,
    calendar_id: "primary",
    provider_event_id: `p-${i}`,
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

describe("CalendarWorkspacePage suppression disclosure", () => {
  const { container, renderPage } = installCalendarPageHarness();

  it("counts a suppressed same-title group and expands it to its entries", () => {
    setWorkspaceState({
      data: {
        data: {
          entries: [],
          suppressed_groups: [
            {
              title: "Standup",
              day: "2026-03-01",
              count: 3,
              entries: [entry(1), entry(2), entry(3)],
            },
          ],
          source_freshness: [],
          lanes: [],
          next_cursor: null,
          has_more: false,
        },
        meta: {},
      },
    });

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    const group = container.querySelector('[data-testid="calendar-suppressed-group"]');
    expect(group?.textContent).toContain("3 hidden: Standup x3 on 2026-03-01");
    expect(group?.querySelectorAll("li").length).toBe(3);
    expect(container.querySelector('[data-testid="calendar-truncated"]')).toBeNull();
  });

  it("says so when the page cap left rows unfetched", () => {
    setWorkspaceState({
      data: {
        data: {
          entries: [entry(1)],
          source_freshness: [],
          lanes: [],
          next_cursor: "more",
          has_more: true,
        },
        meta: {},
      },
    });

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    expect(container.querySelector('[data-testid="calendar-truncated"]')?.textContent).toContain(
      "not shown",
    );
  });
});
