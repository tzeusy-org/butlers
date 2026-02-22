// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";

import CalendarWorkspacePage from "@/pages/CalendarWorkspacePage";
import { useCalendarWorkspace, useCalendarWorkspaceMeta } from "@/hooks/use-calendar-workspace";

vi.mock("@/hooks/use-calendar-workspace", () => ({
  useCalendarWorkspace: vi.fn(),
  useCalendarWorkspaceMeta: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

type UseWorkspaceResult = ReturnType<typeof useCalendarWorkspace>;
type UseWorkspaceMetaResult = ReturnType<typeof useCalendarWorkspaceMeta>;

function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function setWorkspaceState(state?: Partial<UseWorkspaceResult>) {
  vi.mocked(useCalendarWorkspace).mockReturnValue({
    data: {
      data: {
        entries: [
          {
            entry_id: "entry-1",
            view: "user",
            source_type: "provider_event",
            source_key: "google:primary",
            title: "Morning planning",
            start_at: "2026-03-01T09:00:00Z",
            end_at: "2026-03-01T09:30:00Z",
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
          },
        ],
        source_freshness: [],
        lanes: [],
      },
      meta: {},
    },
    isLoading: false,
    isError: false,
    error: null,
    ...state,
  } as UseWorkspaceResult);
}

function setWorkspaceMetaState(state?: Partial<UseWorkspaceMetaResult>) {
  vi.mocked(useCalendarWorkspaceMeta).mockReturnValue({
    data: {
      data: {
        capabilities: {
          views: ["user", "butler"],
          filters: { butlers: true, sources: true, timezone: true },
          sync: { global: true, by_source: true },
        },
        connected_sources: [
          {
            source_id: "source-1",
            source_key: "google:primary",
            source_kind: "provider_event",
            lane: "user",
            provider: "google",
            calendar_id: "primary",
            butler_name: null,
            display_name: "Primary",
            writable: true,
            metadata: {},
            cursor_name: "provider_sync",
            last_synced_at: "2026-03-01T10:00:00Z",
            last_success_at: "2026-03-01T10:00:00Z",
            last_error_at: null,
            last_error: null,
            full_sync_required: false,
            sync_state: "fresh",
            staleness_ms: 1000,
          },
        ],
        writable_calendars: [],
        lane_definitions: [
          { lane_id: "health", butler_name: "health", title: "Health", source_keys: [] },
        ],
        default_timezone: "UTC",
      },
      meta: {},
    },
    isLoading: false,
    isError: false,
    error: null,
    ...state,
  } as UseWorkspaceMetaResult);
}

function SearchEcho() {
  const location = useLocation();
  return <output data-testid="search">{location.search}</output>;
}

describe("CalendarWorkspacePage", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    setWorkspaceState();
    setWorkspaceMetaState();

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  function renderPage(initialEntry: string) {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route
              path="/butlers/calendar"
              element={(
                <>
                  <CalendarWorkspacePage />
                  <SearchEcho />
                </>
              )}
            />
          </Routes>
        </MemoryRouter>,
      );
    });
  }

  function getSearchText() {
    return container.querySelector('[data-testid="search"]')?.textContent ?? "";
  }

  function findButton(label: string): HTMLButtonElement | undefined {
    return Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.trim() === label,
    );
  }

  function latestWorkspaceParams() {
    const calls = vi.mocked(useCalendarWorkspace).mock.calls;
    const params = calls.at(-1)?.[0];
    return params;
  }

  it("restores view/range from deep-link query state", () => {
    renderPage("/butlers/calendar?view=butler&range=list&anchor=2026-03-01");

    expect(findButton("Butler")?.getAttribute("aria-pressed")).toBe("true");
    expect(findButton("List")?.getAttribute("aria-pressed")).toBe("true");
    expect(latestWorkspaceParams()?.view).toBe("butler");
    expect(getSearchText()).toContain("view=butler");
    expect(getSearchText()).toContain("range=list");
  });

  it("persists view toggle changes into URL query state", async () => {
    renderPage("/butlers/calendar?view=butler&range=week&anchor=2026-03-01");

    const userButton = findButton("User");
    expect(userButton).toBeDefined();

    await act(async () => {
      userButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(getSearchText()).toContain("view=user");
    expect(latestWorkspaceParams()?.view).toBe("user");
  });

  it("updates workspace read range when switching range controls", async () => {
    renderPage("/butlers/calendar?view=user&range=day&anchor=2026-03-15");

    const before = latestWorkspaceParams();
    const monthButton = findButton("Month");
    expect(monthButton).toBeDefined();

    await act(async () => {
      monthButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const after = latestWorkspaceParams();
    expect(getSearchText()).toContain("range=month");
    expect(after?.start).not.toBe(before?.start);
    expect(after?.end).not.toBe(before?.end);
  });
});
