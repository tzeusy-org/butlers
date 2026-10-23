// @vitest-environment jsdom

import { describe, expect, it, vi } from "vitest";
import { act } from "react";
import { toast } from "sonner";

import { useCalendarWorkspaceSearch } from "@/hooks/use-calendar-workspace";
import {
  findButton,
  installCalendarPageHarness,
  latestWorkspaceParams,
} from "@/test/calendar-page/harness";
import {
  flush,
  setInputValue,
  setSyncState,
  setWorkspaceMetaState,
  setWorkspaceState,
  type UseWorkspaceSearchResult,
} from "@/test/calendar-page/state";

vi.mock("@/hooks/use-meeting-debriefs", async () =>
  (await import("@/test/calendar-page/mocks")).meetingDebriefsMock(),
);
vi.mock("@/hooks/use-calendar-workspace", async () =>
  (await import("@/test/calendar-page/mocks")).calendarWorkspaceHookMock(),
);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

describe("CalendarWorkspacePage search, timezone and filters", () => {
  const { container, renderPage, getSearchText } = installCalendarPageHarness();

  it("dims retained search results while the next phrase is fetching", async () => {
    vi.mocked(useCalendarWorkspaceSearch).mockReturnValue({
      isLoading: false,
      isFetching: true,
      isError: false,
      error: null,
      data: {
        data: {
          available: true,
          entries: [
            {
              entry_id: "search-stale-1",
              title: "Stale planning result",
              start_at: "2026-03-01T09:00:00Z",
              all_day: false,
              butler_name: "general",
              view: "user",
            },
          ],
        },
      },
    } as unknown as UseWorkspaceSearchResult);

    renderPage("/calendar?view=user&range=list&anchor=2026-03-01");
    await act(async () => {
      (container.querySelector('button[aria-label="Search events"]') as HTMLButtonElement)
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const input = document.querySelector(
      'input[aria-label="Search calendar events"]',
    ) as HTMLInputElement;
    await act(async () => {
      setInputValue(input, "planning");
      await new Promise((resolve) => setTimeout(resolve, 200));
    });

    expect(document.body.textContent).toContain("Stale planning result");
    const result = Array.from(document.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Stale planning result"),
    );
    expect(result?.parentElement?.parentElement?.parentElement?.className).toContain(
      "opacity-60",
    );
  });

  it("surfaces a search query error instead of retained search results", async () => {
    vi.mocked(useCalendarWorkspaceSearch).mockReturnValue({
      isLoading: false,
      isFetching: false,
      isError: true,
      error: new Error("search fetch failed"),
      data: {
        data: {
          available: true,
          entries: [
            {
              entry_id: "search-stale-2",
              title: "Stale search result",
              start_at: "2026-03-01T09:00:00Z",
              all_day: false,
              butler_name: "general",
              view: "user",
            },
          ],
        },
      },
    } as unknown as UseWorkspaceSearchResult);

    renderPage("/calendar?view=user&range=list&anchor=2026-03-01");
    await act(async () => {
      (container.querySelector('button[aria-label="Search events"]') as HTMLButtonElement)
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const input = document.querySelector(
      'input[aria-label="Search calendar events"]',
    ) as HTMLInputElement;
    await act(async () => {
      setInputValue(input, "planning");
      await new Promise((resolve) => setTimeout(resolve, 200));
    });

    const alert = document.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain("search fetch failed");
    expect(document.body.textContent).not.toContain("Stale search result");
  });

  it("renders event times in the configured workspace timezone, not browser-local", async () => {
    // The sole entry is at 09:00Z; in Asia/Singapore (UTC+8) that is 17:00.
    // Asserting 17:00 (and the absence of the raw 09:00) proves the workspace
    // timezone drives formatting regardless of the machine running the test.
    setWorkspaceMetaState(undefined, { defaultTimezone: "Asia/Singapore" });

    renderPage("/calendar?view=user&range=list&anchor=2026-03-01");

    await act(async () => {
      await flush();
    });

    const text = document.body.textContent ?? "";
    expect(text).toContain("17:00");
    expect(text).not.toContain("09:00");
  });

  it("places + labels grid events using the workspace timezone", async () => {
    // 09:00Z → 17:00 SGT. In week view the 09:00→11:00Z event must sit at the
    // 17:00 row (top offset = 17h) and carry a 17:00 label, proving placement
    // and display both follow the workspace zone, not the browser's.
    setWorkspaceState({
      data: {
        data: {
          entries: [
            {
              entry_id: "entry-grid",
              event_id: "evt-grid",
              view: "user",
              source_type: "provider_event",
              source_key: "google:primary",
              title: "Long meeting",
              start_at: "2026-03-01T09:00:00Z",
              end_at: "2026-03-01T11:00:00Z",
              timezone: "UTC",
              all_day: false,
              calendar_id: "primary",
              provider_event_id: "evt-grid-1",
              butler_name: "general",
              schedule_id: null,
              reminder_id: null,
              rrule: null,
              cron: null,
              until_at: null,
              status: "active",
              sync_state: "fresh",
              editable: true,
              metadata: {},
            },
          ],
          source_freshness: [],
          lanes: [],
          projection_version: null,
          staleness_ms: null,
          projection_freshness: null,
        },
        meta: {},
      },
    } as unknown as Parameters<typeof setWorkspaceState>[0]);
    setWorkspaceMetaState(undefined, { defaultTimezone: "Asia/Singapore" });

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");
    await act(async () => {
      await flush();
    });

    const eventButton = document.querySelector(
      '[data-calendar-entry-id="entry-grid"]',
    ) as HTMLButtonElement;
    expect(eventButton).toBeTruthy();
    // Vertical placement: 17:00 → top = 17 * HOUR_HEIGHT_PX (60px).
    expect(eventButton.style.top).toBe(`${17 * 60}px`);
    // Label / title rendered in SGT (17:00–19:00), never browser-local 09:00.
    expect(eventButton.getAttribute("title")).toContain("17:00");
    expect(eventButton.getAttribute("title")).toContain("19:00");
    expect(eventButton.getAttribute("title")).not.toContain("09:00");
  });

  // -------------------------------------------------------------------------
  // Linked-people avatars on time-grid blocks (bu-01qmd): reuse the agenda/
  // detail cluster on week/day blocks, gated by block height so short events
  // don't overflow.
  // -------------------------------------------------------------------------

  function gridEntryWith(overrides: Record<string, unknown>) {
    return {
      entry_id: "entry-grid",
      event_id: "evt-grid",
      view: "user",
      source_type: "provider_event",
      source_key: "google:primary",
      title: "Long meeting",
      start_at: "2026-03-01T09:00:00Z",
      end_at: "2026-03-01T11:00:00Z",
      timezone: "UTC",
      all_day: false,
      calendar_id: "primary",
      provider_event_id: "evt-grid-1",
      butler_name: "general",
      schedule_id: null,
      reminder_id: null,
      rrule: null,
      cron: null,
      until_at: null,
      status: "active",
      sync_state: "fresh",
      editable: true,
      metadata: {},
      ...overrides,
    };
  }

  function renderGridWith(entry: Record<string, unknown>) {
    setWorkspaceState({
      data: {
        data: {
          entries: [entry],
          source_freshness: [],
          lanes: [],
          projection_version: null,
          staleness_ms: null,
          projection_freshness: null,
        },
        meta: {},
      },
    } as unknown as Parameters<typeof setWorkspaceState>[0]);
    setWorkspaceMetaState(undefined, { defaultTimezone: "Asia/Singapore" });
    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");
  }

  it("renders linked-people avatars on a tall time-grid block", async () => {
    // 2h block (120px, >= AVATAR_MIN_BLOCK_HEIGHT_PX) with two linked people.
    renderGridWith(
      gridEntryWith({
        linked_people: [
          { entity_id: "e1", display_label: "Ada Lovelace" },
          { entity_id: "e2", display_label: "Alan Turing" },
        ],
      }),
    );
    await act(async () => {
      await flush();
    });

    const block = document.querySelector(
      '[data-calendar-entry-id="entry-grid"]',
    ) as HTMLElement;
    expect(block).toBeTruthy();
    const avatars = block.querySelector('[data-testid="linked-people-avatars"]');
    expect(avatars).toBeTruthy();
    // Non-interactive + pointer-transparent so it never intercepts the block's drag.
    expect(avatars?.className).toContain("pointer-events-none");
    expect(block.querySelectorAll('[data-testid="linked-person-avatar"]').length).toBe(2);
  });

  it("hides the avatar cluster on a short time-grid block (below the height gate)", async () => {
    // 30-minute block (30px, < AVATAR_MIN_BLOCK_HEIGHT_PX) — avatars would overflow.
    renderGridWith(
      gridEntryWith({
        end_at: "2026-03-01T09:30:00Z",
        linked_people: [{ entity_id: "e1", display_label: "Ada Lovelace" }],
      }),
    );
    await act(async () => {
      await flush();
    });

    const block = document.querySelector(
      '[data-calendar-entry-id="entry-grid"]',
    ) as HTMLElement;
    expect(block).toBeTruthy();
    expect(
      block.querySelector('[data-testid="linked-people-avatars"]'),
    ).toBeNull();
  });

  it("does not render avatars on a grid block with no linked people", async () => {
    // linked_people omitted entirely (stale-fixture shape) must not crash or render.
    renderGridWith(gridEntryWith({}));
    await act(async () => {
      await flush();
    });

    const block = document.querySelector(
      '[data-calendar-entry-id="entry-grid"]',
    ) as HTMLElement;
    expect(block).toBeTruthy();
    expect(
      block.querySelector('[data-testid="linked-people-avatars"]'),
    ).toBeNull();
  });

  it("updates query state when toggling to butler view", async () => {
    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");
    const butlerButton = findButton("Butler");
    expect(butlerButton).toBeDefined();

    await act(async () => {
      butlerButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(findButton("Butler")?.getAttribute("aria-pressed")).toBe("true");
    expect(getSearchText()).toContain("view=butler");
    expect(latestWorkspaceParams()?.view).toBe("butler");
  });

  it("applies calendar/source filters to workspace query params", async () => {
    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    const calendarSelect = container.querySelector(
      "#calendar-filter",
    ) as HTMLSelectElement;
    expect(calendarSelect).toBeDefined();

    await act(async () => {
      calendarSelect.value = "work";
      calendarSelect.dispatchEvent(new Event("change", { bubbles: true }));
      await flush();
    });

    expect(getSearchText()).toContain("calendar=work");
    expect(latestWorkspaceParams()?.sources).toEqual(["google:work"]);
  });

  it("triggers global sync-now action", async () => {
    const syncMutateAsync = vi.fn().mockResolvedValue({
      data: {
        scope: "all",
        requested_source_key: null,
        requested_source_id: null,
        request_id: "sync-all-request",
        full: false,
        targets: [],
        triggered_count: 2,
      },
      meta: {},
    });
    setSyncState({ mutateAsync: syncMutateAsync });

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    const syncButton = document.querySelector(
      'button[aria-label="Sync all sources now"]',
    ) as HTMLButtonElement;
    expect(syncButton).toBeDefined();

    await act(async () => {
      syncButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(syncMutateAsync).toHaveBeenCalledWith({ all: true });
    expect(toast.success).toHaveBeenCalledWith("Sync queued for 2 calendar owner(s).");
  });
});
