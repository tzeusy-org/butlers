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

describe("CalendarWorkspacePage recurring instance capping", () => {
  const { container, renderPage } = installCalendarPageHarness();

  function makeRecurringEntries(
    scheduleId: string,
    count: number,
    dayPrefix: string = "2026-03-01",
  ) {
    return Array.from({ length: count }, (_, i) => ({
      linked_people: [], source_butler: null, source_session_id: null,
      entry_id: `entry-${scheduleId}-${dayPrefix}-${i}`,
      event_id: `evt-entry-${scheduleId}-${dayPrefix}-${i}`,
      view: "butler" as const,
      source_type: "scheduled_task" as const,
      source_key: "internal_scheduler:general",
      title: "Hourly check",
      start_at: `${dayPrefix}T${String(i % 24).padStart(2, "0")}:00:00Z`,
      end_at: `${dayPrefix}T${String(i % 24).padStart(2, "0")}:15:00Z`,
      timezone: "UTC",
      all_day: false,
      calendar_id: null,
      provider_event_id: null,
      butler_name: "general",
      schedule_id: scheduleId,
      reminder_id: null,
      rrule: "RRULE:FREQ=HOURLY",
      cron: "0 * * * *",
      until_at: null,
      status: "active",
      sync_state: "fresh" as const,
      editable: true,
      metadata: {},
    }));
  }

  it("shows overflow indicator in butler lane table when >10 instances share same schedule_id per day", () => {
    setWorkspaceState({
      data: {
        data: {
entries_source_available: true,
people_source_available: true,
sources_available: true,
has_domain_context: false,
          entries: makeRecurringEntries("sched-hourly", 12),
          source_freshness: [],
          lanes: [
            {
              lane_id: "general",
              butler_name: "general",
              title: "General lane",
              source_keys: ["internal_scheduler:general"],
            },
          ],
          next_cursor: null,
          has_more: false,
        },
        meta: {},
      },
    });
    setWorkspaceMetaState({
      data: {
        data: {
sources_available: true,
          capabilities: {
            views: ["user", "butler"],
            filters: { butlers: true, sources: true, timezone: true },
            sync: { global: true, by_source: true },
          },
          connected_sources: [],
          writable_calendars: [],
          lane_definitions: [
            {
              lane_id: "general",
              butler_name: "general",
              title: "General lane",
              source_keys: ["internal_scheduler:general"],
            },
          ],
          default_timezone: "UTC",
          primary_calendar_id: null,
        },
        meta: {},
      },
    });

    renderPage("/calendar?view=butler&range=week&anchor=2026-03-01");

    // Should see overflow indicator
    expect(container.textContent).toContain("2 more instances");
    // Should not show all 12
    const rows = Array.from(
      container.querySelectorAll('[data-testid="butler-lane-row"]'),
    ).filter((r) => r.textContent?.includes("Hourly check"));
    // 10 visible rows + 1 overflow row
    expect(rows.length).toBe(11);
  });

  it("does not show overflow indicator when exactly 10 instances per day", () => {
    setWorkspaceState({
      data: {
        data: {
entries_source_available: true,
people_source_available: true,
sources_available: true,
has_domain_context: false,
          entries: makeRecurringEntries("sched-exact10", 10),
          source_freshness: [],
          lanes: [
            {
              lane_id: "general",
              butler_name: "general",
              title: "General lane",
              source_keys: ["internal_scheduler:general"],
            },
          ],
          next_cursor: null,
          has_more: false,
        },
        meta: {},
      },
    });
    setWorkspaceMetaState({
      data: {
        data: {
sources_available: true,
          capabilities: {
            views: ["user", "butler"],
            filters: { butlers: true, sources: true, timezone: true },
            sync: { global: true, by_source: true },
          },
          connected_sources: [],
          writable_calendars: [],
          lane_definitions: [
            {
              lane_id: "general",
              butler_name: "general",
              title: "General lane",
              source_keys: ["internal_scheduler:general"],
            },
          ],
          default_timezone: "UTC",
          primary_calendar_id: null,
        },
        meta: {},
      },
    });

    renderPage("/calendar?view=butler&range=week&anchor=2026-03-01");

    expect(container.textContent).not.toContain("more instance");
    const rows = Array.from(
      container.querySelectorAll('[data-testid="butler-lane-row"]'),
    ).filter((r) => r.textContent?.includes("Hourly check"));
    expect(rows.length).toBe(10);
  });

  it("groups overflows per parent event separately — different schedule_ids are not merged", () => {
    const entriesA = makeRecurringEntries("sched-A", 12);
    const entriesB = makeRecurringEntries("sched-B", 6);
    setWorkspaceState({
      data: {
        data: {
entries_source_available: true,
people_source_available: true,
sources_available: true,
has_domain_context: false,
          entries: [...entriesA, ...entriesB],
          source_freshness: [],
          lanes: [
            {
              lane_id: "general",
              butler_name: "general",
              title: "General lane",
              source_keys: ["internal_scheduler:general"],
            },
          ],
          next_cursor: null,
          has_more: false,
        },
        meta: {},
      },
    });
    setWorkspaceMetaState({
      data: {
        data: {
sources_available: true,
          capabilities: {
            views: ["user", "butler"],
            filters: { butlers: true, sources: true, timezone: true },
            sync: { global: true, by_source: true },
          },
          connected_sources: [],
          writable_calendars: [],
          lane_definitions: [
            {
              lane_id: "general",
              butler_name: "general",
              title: "General lane",
              source_keys: ["internal_scheduler:general"],
            },
          ],
          default_timezone: "UTC",
          primary_calendar_id: null,
        },
        meta: {},
      },
    });

    renderPage("/calendar?view=butler&range=week&anchor=2026-03-01");

    // sched-A: 10 visible + 1 overflow; sched-B: 6 visible, no overflow
    // Total visible rows: 16 + 1 overflow row
    const overflowText = Array.from(
      container.querySelectorAll('[data-testid="butler-lane-row"]'),
    ).filter((el) => el.textContent?.includes("more instance"));
    expect(overflowText.length).toBe(1);
    expect(overflowText[0]?.textContent).toContain("2 more instance");
  });

  it("entries on different days with same schedule_id are capped independently", () => {
    const day1Entries = makeRecurringEntries("sched-daily", 12, "2026-03-01");
    const day2Entries = makeRecurringEntries("sched-daily", 12, "2026-03-02");
    setWorkspaceState({
      data: {
        data: {
entries_source_available: true,
people_source_available: true,
sources_available: true,
has_domain_context: false,
          entries: [...day1Entries, ...day2Entries],
          source_freshness: [],
          lanes: [
            {
              lane_id: "general",
              butler_name: "general",
              title: "General lane",
              source_keys: ["internal_scheduler:general"],
            },
          ],
          next_cursor: null,
          has_more: false,
        },
        meta: {},
      },
    });
    setWorkspaceMetaState({
      data: {
        data: {
sources_available: true,
          capabilities: {
            views: ["user", "butler"],
            filters: { butlers: true, sources: true, timezone: true },
            sync: { global: true, by_source: true },
          },
          connected_sources: [],
          writable_calendars: [],
          lane_definitions: [
            {
              lane_id: "general",
              butler_name: "general",
              title: "General lane",
              source_keys: ["internal_scheduler:general"],
            },
          ],
          default_timezone: "UTC",
          primary_calendar_id: null,
        },
        meta: {},
      },
    });

    renderPage("/calendar?view=butler&range=week&anchor=2026-03-01");

    // Each day: 10 visible + 1 overflow = 2 overflow rows total
    const overflowText = Array.from(
      container.querySelectorAll('[data-testid="butler-lane-row"]'),
    ).filter((el) => el.textContent?.includes("more instance"));
    expect(overflowText.length).toBe(2);
  });
});
