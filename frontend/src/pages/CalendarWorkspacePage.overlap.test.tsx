// @vitest-environment jsdom

import { act } from "react";
import { describe, expect, it, vi } from "vitest";

import { installCalendarPageHarness } from "@/test/calendar-page/harness";
import { flush, setWorkspaceState } from "@/test/calendar-page/state";

vi.mock("@/hooks/use-meeting-debriefs", async () =>
  (await import("@/test/calendar-page/mocks")).meetingDebriefsMock(),
);
vi.mock("@/hooks/use-calendar-workspace", async () =>
  (await import("@/test/calendar-page/mocks")).calendarWorkspaceHookMock(),
);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function block(id: string, title: string, start: string, end: string) {
  return {
    entry_id: id,
    event_id: `evt-${id}`,
    view: "user" as const,
    source_type: "provider_event" as const,
    source_key: "provider:google:primary",
    title,
    start_at: start,
    end_at: end,
    timezone: "UTC",
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

describe("CalendarWorkspacePage overlapping blocks", () => {
  const { container, renderPage } = installCalendarPageHarness();

  it("opens the detail panel of the lower of two overlapping blocks, not the one above it", async () => {
    setWorkspaceState({
      data: {
        data: {
          entries: [
            block("upper", "Board review", "2026-03-02T09:00:00Z", "2026-03-02T11:00:00Z"),
            block("lower", "Vendor call", "2026-03-02T10:00:00Z", "2026-03-02T11:30:00Z"),
          ],
          source_freshness: [],
          lanes: [],
          next_cursor: null,
          has_more: false,
        },
        meta: {},
      },
    });

    renderPage("/calendar?view=user&range=week&anchor=2026-03-02");

    const upper = container.querySelector<HTMLElement>('[data-calendar-entry-id="upper"]');
    const lower = container.querySelector<HTMLElement>('[data-calendar-entry-id="lower"]');
    expect(upper).not.toBeNull();
    expect(lower).not.toBeNull();
    // Two blocks that overlap in time share the day, side by side, in different columns.
    expect(upper!.style.left).not.toBe(lower!.style.left);

    await act(async () => {
      lower!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const panel = container.querySelector('[data-testid="entry-detail-panel"]');
    expect(panel).not.toBeNull();
    expect(panel?.textContent).toContain("Vendor call");
    expect(panel?.textContent).not.toContain("Board review");
  });
});
