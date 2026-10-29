// @vitest-environment jsdom

import { describe, expect, it, vi } from "vitest";
import { act } from "react";

import {
  useCalendarMeetingPrep,
  useCalendarWorkspaceEntry,
} from "@/hooks/use-calendar-workspace";
import {
  findButton,
  installCalendarPageHarness,
  openDetailPanel,
} from "@/test/calendar-page/harness";
import {
  flush,
  type UseEntryDetailResult,
  setEntryDetailState,
  setWorkspaceState,
} from "@/test/calendar-page/state";

vi.mock("@/hooks/use-meeting-debriefs", async () =>
  (await import("@/test/calendar-page/mocks")).meetingDebriefsMock(),
);
vi.mock("@/hooks/use-calendar-workspace", async () =>
  (await import("@/test/calendar-page/mocks")).calendarWorkspaceHookMock(),
);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

describe("CalendarWorkspacePage detail panel", () => {
  const { container, renderPage } = installCalendarPageHarness();

  // The detail panel fetches the fresh server copy of the selected entry
  // (bu-rgq00): grid row shown immediately, upgraded in place on success,
  // graceful fallback (grid row + degraded note) on error.

  it("fetches the selected entry's fresh copy via useCalendarWorkspaceEntry keyed on its id (bu-rgq00)", async () => {
    renderPage("/calendar?view=user&range=list&anchor=2026-03-01");
    await openDetailPanel();

    // The panel dispatches the single-entry read keyed on the selected id.
    const calls = vi.mocked(useCalendarWorkspaceEntry).mock.calls;
    expect(calls.some((c) => c[0] === "entry-1")).toBe(true);
  });

  it("upgrades the panel in place when the fresh server copy lands (bu-rgq00)", async () => {
    // Fresh copy differs from the grid row: renamed title + provenance the grid
    // window did not carry. Both must flow through the same rendering path.
    setEntryDetailState({
      data: {
        data: {
          entry_id: "entry-1",
          event_id: "evt-entry-1",
          view: "user",
          source_type: "provider_event",
          source_key: "google:primary",
          title: "Renamed on server",
          start_at: "2026-03-01T09:00:00Z",
          end_at: "2026-03-01T09:30:00Z",
          timezone: "UTC",
          all_day: false,
          calendar_id: "primary",
          provider_event_id: "evt-1",
          butler_name: "general",
          schedule_id: null,
          reminder_id: null,
          rrule: null,
          cron: null,
          until_at: null,
          status: "active",
          sync_state: "fresh",
          editable: true,
          source_butler: "finance",
          metadata: { description: "Daily planning", location: "Desk" },
        },
        meta: {},
      },
    } as unknown as Partial<UseEntryDetailResult>);

    renderPage("/calendar?view=user&range=list&anchor=2026-03-01");
    await openDetailPanel();

    const panel = container.querySelector('[data-testid="entry-detail-panel"]');
    // Provider events are read-only: the title shows the fresh server copy,
    // not the grid-row title, and there is no edit input.
    expect(panel?.querySelector('[data-testid="detail-title"]')?.textContent).toBe(
      "Renamed on server",
    );
    expect(panel?.querySelector('[data-testid="detail-title-input"]')).toBeNull();
    // Provenance only present on the fresh copy — proves it rendered, not the grid row.
    expect(
      panel?.querySelector('[data-testid="detail-source-butler"]')?.textContent,
    ).toContain("finance");
    // No degraded note on the success path.
    expect(
      panel?.querySelector('[data-testid="detail-degraded-note"]'),
    ).toBeNull();
  });

  it("falls back to the grid row + a non-blocking degraded note when the fresh fetch errors (bu-rgq00)", async () => {
    setEntryDetailState({
      isError: true,
      error: new Error("entry read failed"),
      data: undefined,
    });

    renderPage("/calendar?view=user&range=list&anchor=2026-03-01");
    await openDetailPanel();

    const panel = container.querySelector('[data-testid="entry-detail-panel"]');
    expect(panel).not.toBeNull();
    // Grid-row data still rendered — never a blank panel.
    expect(panel?.querySelector('[data-testid="detail-title"]')?.textContent).toBe(
      "Morning planning",
    );
    // Degraded note surfaced (non-blocking).
    expect(
      panel?.querySelector('[data-testid="detail-degraded-note"]'),
    ).not.toBeNull();
  });

  it("renders provenance and sync state chip when entry has source_butler", async () => {
    setWorkspaceState({
      data: {
        data: {
entries_source_available: true,
people_source_available: true,
sources_available: true,
has_domain_context: false,
          entries: [
            {
linked_people: [],
              entry_id: "entry-prov",
              event_id: "evt-entry-prov",
              view: "user" as const,
              source_type: "provider_event" as const,
              source_key: "google:primary",
              title: "Provenance test",
              start_at: "2026-03-01T09:00:00Z",
              end_at: "2026-03-01T09:30:00Z",
              timezone: "UTC",
              all_day: false,
              calendar_id: "primary",
              provider_event_id: "evt-prov",
              butler_name: "general",
              schedule_id: null,
              reminder_id: null,
              rrule: null,
              cron: null,
              until_at: null,
              status: "active",
              sync_state: "stale" as const,
              editable: true,
              metadata: {},
              source_butler: "general",
              source_session_id: "sess-abc",
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

    renderPage("/calendar?view=user&range=list&anchor=2026-03-01");

    // Click Detail to open panel
    const detailButton = findButton("Detail");
    await act(async () => {
      detailButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const panel = container.querySelector(
      '[data-testid="entry-detail-panel"]',
    );
    expect(panel).toBeDefined();

    // Provenance: source_butler name visible
    const sourceButlerEl = panel?.querySelector(
      '[data-testid="detail-source-butler"]',
    );
    expect(sourceButlerEl).toBeDefined();
    expect(sourceButlerEl?.textContent).toContain("general");

    // Provenance: session link visible
    const sessionLink = panel?.querySelector(
      '[data-testid="detail-session-link"]',
    );
    expect(sessionLink).toBeDefined();

    // sync_state chip visible (non-null sync_state → chip rendered)
    expect(panel?.textContent).toContain("stale");
  });

  it("keys the meeting-prep rail on event_id (calendar_events.id), not entry_id (bu-jemrk)", async () => {
    setWorkspaceState({
      data: {
        data: {
entries_source_available: true,
people_source_available: true,
sources_available: true,
has_domain_context: false,
          entries: [
            {
linked_people: [],
              entry_id: "instance-entry-1",
              event_id: "evt-backing-1",
              view: "user" as const,
              source_type: "provider_event" as const,
              source_key: "google:primary",
              title: "Prep keying test",
              start_at: "2026-03-01T09:00:00Z",
              end_at: "2026-03-01T09:30:00Z",
              timezone: "UTC",
              all_day: false,
              calendar_id: "primary",
              provider_event_id: "evt-prov",
              butler_name: "general",
              schedule_id: null,
              reminder_id: null,
              rrule: null,
              cron: null,
              until_at: null,
              status: "active",
              sync_state: null,
              editable: true,
              metadata: {},
              source_butler: null,
              source_session_id: null,
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

    renderPage("/calendar?view=user&range=list&anchor=2026-03-01");

    const detailButton = findButton("Detail");
    await act(async () => {
      detailButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    // The prep rail must fetch using the backing calendar_events.id, not the
    // per-instance entry_id — otherwise contributions never match.
    const prepCalls = vi.mocked(useCalendarMeetingPrep).mock.calls;
    expect(prepCalls.length).toBeGreaterThan(0);
    const eventIdArgs = prepCalls.map((call) => call[0]);
    expect(eventIdArgs).toContain("evt-backing-1");
    expect(eventIdArgs).not.toContain("instance-entry-1");
  });

  it("closes the panel when close button is pressed", async () => {
    renderPage("/calendar?view=user&range=list&anchor=2026-03-01");

    const detailButton = findButton("Detail");
    await act(async () => {
      detailButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(
      container.querySelector('[data-testid="entry-detail-panel"]'),
    ).toBeDefined();

    const closeBtn = container.querySelector(
      'button[aria-label="Close detail panel"]',
    ) as HTMLButtonElement;
    expect(closeBtn).toBeDefined();
    await act(async () => {
      closeBtn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(
      container.querySelector('[data-testid="entry-detail-panel"]'),
    ).toBeNull();
  });
});
