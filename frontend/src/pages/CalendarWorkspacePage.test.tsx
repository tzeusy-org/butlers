// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";

import CalendarWorkspacePage from "@/pages/CalendarWorkspacePage";
import {
  useCalendarWorkspace,
  useCalendarWorkspaceMeta,
  useMutateCalendarWorkspaceButlerEvent,
  useMutateCalendarWorkspaceUserEvent,
  useSyncCalendarWorkspace,
} from "@/hooks/use-calendar-workspace";

vi.mock("@/hooks/use-calendar-workspace", () => ({
  useCalendarWorkspace: vi.fn(),
  useCalendarWorkspaceMeta: vi.fn(),
  useMutateCalendarWorkspaceButlerEvent: vi.fn(),
  useSyncCalendarWorkspace: vi.fn(),
  useMutateCalendarWorkspaceUserEvent: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

type UseWorkspaceResult = ReturnType<typeof useCalendarWorkspace>;
type UseWorkspaceMetaResult = ReturnType<typeof useCalendarWorkspaceMeta>;
type UseButlerMutationResult = ReturnType<typeof useMutateCalendarWorkspaceButlerEvent>;
type UseSyncResult = ReturnType<typeof useSyncCalendarWorkspace>;
type UseUserMutationResult = ReturnType<typeof useMutateCalendarWorkspaceUserEvent>;

const mutateButlerEvent = vi.fn();

function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function setInputValue(input: HTMLInputElement, value: string) {
  const prototype = window.HTMLInputElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
  descriptor?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
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
            butler_name: "general",
            schedule_id: null,
            reminder_id: null,
            rrule: null,
            cron: null,
            until_at: null,
            status: "active",
            sync_state: "fresh",
            editable: true,
            metadata: {
              description: "Daily planning",
              location: "Desk",
            },
          },
        ],
        source_freshness: [
          {
            source_id: "source-1",
            source_key: "google:primary",
            source_kind: "provider_event",
            lane: "user",
            provider: "google",
            calendar_id: "primary",
            butler_name: "general",
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
            staleness_ms: 500,
          },
        ],
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
            butler_name: "general",
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
          {
            source_id: "source-2",
            source_key: "google:work",
            source_kind: "provider_event",
            lane: "user",
            provider: "google",
            calendar_id: "work",
            butler_name: "general",
            display_name: "Work",
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
        writable_calendars: [
          {
            source_key: "google:primary",
            provider: "google",
            calendar_id: "primary",
            display_name: "Primary",
            butler_name: "general",
          },
          {
            source_key: "google:work",
            provider: "google",
            calendar_id: "work",
            display_name: "Work",
            butler_name: "general",
          },
        ],
        lane_definitions: [],
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

function setButlerMutationState(state?: Partial<UseButlerMutationResult>) {
  vi.mocked(useMutateCalendarWorkspaceButlerEvent).mockReturnValue({
    mutate: mutateButlerEvent,
    isPending: false,
    ...state,
  } as UseButlerMutationResult);
}

function setSyncState(state?: Partial<UseSyncResult>) {
  vi.mocked(useSyncCalendarWorkspace).mockReturnValue({
    mutateAsync: vi.fn().mockResolvedValue({
      data: {
        scope: "all",
        requested_source_key: null,
        requested_source_id: null,
        targets: [],
        triggered_count: 1,
      },
      meta: {},
    }),
    isPending: false,
    ...state,
  } as unknown as UseSyncResult);
}

function setUserMutationState(state?: Partial<UseUserMutationResult>) {
  vi.mocked(useMutateCalendarWorkspaceUserEvent).mockReturnValue({
    mutateAsync: vi.fn().mockResolvedValue({
      data: {
        action: "create",
        tool_name: "calendar_create_event",
        request_id: "req-1",
        result: { status: "created" },
        projection_version: null,
        staleness_ms: null,
        projection_freshness: null,
      },
      meta: {},
    }),
    isPending: false,
    ...state,
  } as unknown as UseUserMutationResult);
}

function setButlerWorkspaceFixtures() {
  setWorkspaceState({
    data: {
      data: {
        entries: [
          {
            entry_id: "entry-butler-1",
            view: "butler",
            source_type: "scheduled_task",
            source_key: "internal_scheduler:general",
            title: "Daily prep",
            start_at: "2026-03-01T09:00:00Z",
            end_at: "2026-03-01T09:15:00Z",
            timezone: "UTC",
            all_day: false,
            calendar_id: null,
            provider_event_id: null,
            butler_name: "general",
            schedule_id: "sched-1",
            reminder_id: null,
            rrule: "RRULE:FREQ=DAILY",
            cron: "0 9 * * *",
            until_at: "2026-03-08T09:00:00Z",
            status: "active",
            sync_state: "fresh",
            editable: true,
            metadata: {
              origin_ref: "sched-1",
            },
          },
          {
            entry_id: "entry-butler-2",
            view: "butler",
            source_type: "butler_reminder",
            source_key: "internal_reminders:health",
            title: "Hydration check",
            start_at: "2026-03-01T11:00:00Z",
            end_at: "2026-03-01T11:05:00Z",
            timezone: "UTC",
            all_day: false,
            calendar_id: null,
            provider_event_id: null,
            butler_name: "health",
            schedule_id: null,
            reminder_id: "rem-1",
            rrule: null,
            cron: null,
            until_at: null,
            status: "paused",
            sync_state: "fresh",
            editable: true,
            metadata: {
              origin_ref: "rem-1",
            },
          },
        ],
        source_freshness: [
          {
            source_id: "source-butler-1",
            source_key: "internal_scheduler:general",
            source_kind: "internal_scheduler",
            lane: "butler",
            provider: "internal",
            calendar_id: null,
            butler_name: "general",
            display_name: "General scheduler",
            writable: true,
            metadata: {},
            cursor_name: "projection",
            last_synced_at: "2026-03-01T10:00:00Z",
            last_success_at: "2026-03-01T10:00:00Z",
            last_error_at: null,
            last_error: null,
            full_sync_required: false,
            sync_state: "fresh",
            staleness_ms: 900,
          },
          {
            source_id: "source-butler-2",
            source_key: "internal_reminders:health",
            source_kind: "internal_reminders",
            lane: "butler",
            provider: "internal",
            calendar_id: null,
            butler_name: "health",
            display_name: "Health reminders",
            writable: true,
            metadata: {},
            cursor_name: "projection",
            last_synced_at: "2026-03-01T10:00:00Z",
            last_success_at: "2026-03-01T10:00:00Z",
            last_error_at: null,
            last_error: null,
            full_sync_required: false,
            sync_state: "fresh",
            staleness_ms: 900,
          },
        ],
        lanes: [
          {
            lane_id: "general",
            butler_name: "general",
            title: "General lane",
            source_keys: ["internal_scheduler:general"],
          },
          {
            lane_id: "health",
            butler_name: "health",
            title: "Health lane",
            source_keys: ["internal_reminders:health"],
          },
        ],
      },
      meta: {},
    },
  });

  setWorkspaceMetaState({
    data: {
      data: {
        capabilities: {
          views: ["user", "butler"],
          filters: { butlers: true, sources: true, timezone: true },
          sync: { global: true, by_source: true },
        },
        connected_sources: [
          {
            source_id: "source-butler-1",
            source_key: "internal_scheduler:general",
            source_kind: "internal_scheduler",
            lane: "butler",
            provider: "internal",
            calendar_id: null,
            butler_name: "general",
            display_name: "General scheduler",
            writable: true,
            metadata: {},
            cursor_name: "projection",
            last_synced_at: "2026-03-01T10:00:00Z",
            last_success_at: "2026-03-01T10:00:00Z",
            last_error_at: null,
            last_error: null,
            full_sync_required: false,
            sync_state: "fresh",
            staleness_ms: 900,
          },
          {
            source_id: "source-butler-2",
            source_key: "internal_reminders:health",
            source_kind: "internal_reminders",
            lane: "butler",
            provider: "internal",
            calendar_id: null,
            butler_name: "health",
            display_name: "Health reminders",
            writable: true,
            metadata: {},
            cursor_name: "projection",
            last_synced_at: "2026-03-01T10:00:00Z",
            last_success_at: "2026-03-01T10:00:00Z",
            last_error_at: null,
            last_error: null,
            full_sync_required: false,
            sync_state: "fresh",
            staleness_ms: 900,
          },
        ],
        writable_calendars: [],
        lane_definitions: [
          {
            lane_id: "general",
            butler_name: "general",
            title: "General lane",
            source_keys: ["internal_scheduler:general"],
          },
          {
            lane_id: "health",
            butler_name: "health",
            title: "Health lane",
            source_keys: ["internal_reminders:health"],
          },
        ],
        default_timezone: "UTC",
      },
      meta: {},
    },
  });
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
    mutateButlerEvent.mockReset();
    setWorkspaceState();
    setWorkspaceMetaState();
    setButlerMutationState();
    setSyncState();
    setUserMutationState();
    vi.stubGlobal("confirm", vi.fn(() => true));

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.unstubAllGlobals();
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
    return Array.from(document.querySelectorAll("button")).find(
      (button) => button.textContent?.trim() === label,
    );
  }

  function latestWorkspaceParams() {
    const calls = vi.mocked(useCalendarWorkspace).mock.calls;
    return calls.at(-1)?.[0];
  }

  function findDialogByTitle(title: string): Element | undefined {
    return Array.from(document.querySelectorAll('[data-slot="dialog-content"]')).find((dialog) =>
      dialog.textContent?.includes(title),
    );
  }

  it("restores view/range from deep-link query state", () => {
    renderPage("/butlers/calendar?view=butler&range=list&anchor=2026-03-01");

    expect(findButton("Butler")?.getAttribute("aria-pressed")).toBe("true");
    expect(findButton("List")?.getAttribute("aria-pressed")).toBe("true");
    expect(latestWorkspaceParams()?.view).toBe("butler");
    expect(getSearchText()).toContain("view=butler");
    expect(getSearchText()).toContain("range=list");
  });

  it("updates query state when toggling to butler view", async () => {
    renderPage("/butlers/calendar?view=user&range=week&anchor=2026-03-01");
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
    renderPage("/butlers/calendar?view=user&range=week&anchor=2026-03-01");

    const calendarSelect = container.querySelector("#calendar-filter") as HTMLSelectElement;
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
        targets: [],
        triggered_count: 2,
      },
      meta: {},
    });
    setSyncState({ mutateAsync: syncMutateAsync });

    renderPage("/butlers/calendar?view=user&range=week&anchor=2026-03-01");

    const syncButton = document.querySelector(
      'button[aria-label="Sync all sources now"]',
    ) as HTMLButtonElement;
    expect(syncButton).toBeDefined();

    await act(async () => {
      syncButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(syncMutateAsync).toHaveBeenCalledWith({ all: true });
  });

  it("creates user event through workspace mutation endpoint", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({
      data: {
        action: "create",
        tool_name: "calendar_create_event",
        request_id: "req-create",
        result: { status: "created" },
        projection_version: null,
        staleness_ms: null,
        projection_freshness: null,
      },
      meta: {},
    });
    setUserMutationState({ mutateAsync });

    renderPage("/butlers/calendar?view=user&range=week&anchor=2026-03-01");

    const openCreateButton = document.querySelector(
      'button[aria-label="Create user event"]',
    ) as HTMLButtonElement;
    await act(async () => {
      openCreateButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const dialog = findDialogByTitle("Create User Event");
    const titleInput = dialog?.querySelector("#event-title") as HTMLInputElement;
    expect(titleInput).toBeDefined();

    await act(async () => {
      setInputValue(titleInput, "Team review");
      await flush();
    });

    const form = titleInput.closest("form") as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await flush();
    });

    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        butler_name: "general",
        action: "create",
        payload: expect.objectContaining({
          title: "Team review",
          calendar_id: "primary",
        }),
      }),
    );
  });

  it("updates user event through workspace mutation endpoint", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({
      data: {
        action: "update",
        tool_name: "calendar_update_event",
        request_id: "req-update",
        result: { status: "updated" },
        projection_version: null,
        staleness_ms: null,
        projection_freshness: null,
      },
      meta: {},
    });
    setUserMutationState({ mutateAsync });

    renderPage("/butlers/calendar?view=user&range=week&anchor=2026-03-01");

    const editButton = findButton("Edit");
    expect(editButton).toBeDefined();

    await act(async () => {
      editButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const dialog = findDialogByTitle("Edit User Event");
    const titleInput = dialog?.querySelector("#event-title") as HTMLInputElement;
    await act(async () => {
      setInputValue(titleInput, "Morning review");
      await flush();
    });

    const form = titleInput.closest("form") as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await flush();
    });

    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        butler_name: "general",
        action: "update",
        payload: expect.objectContaining({
          event_id: "evt-1",
          title: "Morning review",
        }),
      }),
    );
  });

  it("deletes user event through workspace mutation endpoint", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({
      data: {
        action: "delete",
        tool_name: "calendar_delete_event",
        request_id: "req-delete",
        result: { status: "deleted" },
        projection_version: null,
        staleness_ms: null,
        projection_freshness: null,
      },
      meta: {},
    });
    setUserMutationState({ mutateAsync });

    renderPage("/butlers/calendar?view=user&range=week&anchor=2026-03-01");

    const rowDeleteButton = findButton("Delete");
    expect(rowDeleteButton).toBeDefined();

    await act(async () => {
      rowDeleteButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const deleteDialog = findDialogByTitle("Delete Event");
    const confirmDeleteButton = Array.from(deleteDialog?.querySelectorAll("button") ?? []).find(
      (button) => button.textContent?.trim() === "Delete",
    ) as HTMLButtonElement;

    await act(async () => {
      confirmDeleteButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        butler_name: "general",
        action: "delete",
        payload: expect.objectContaining({
          event_id: "evt-1",
          calendar_id: "primary",
        }),
      }),
    );
  });

  it("renders butler lanes grouped with lane metadata", () => {
    setButlerWorkspaceFixtures();
    renderPage("/butlers/calendar?view=butler&range=week&anchor=2026-03-01");

    expect(container.textContent).toContain("General lane");
    expect(container.textContent).toContain("Health lane");
    expect(container.textContent).toContain("Daily prep");
    expect(container.textContent).toContain("Hydration check");
  });

  it("creates butler event through workspace mutation endpoint", async () => {
    setButlerWorkspaceFixtures();
    renderPage("/butlers/calendar?view=butler&range=week&anchor=2026-03-01");

    const createButton = findButton("Create Butler Event");
    expect(createButton).toBeDefined();
    await act(async () => {
      createButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const dialog = findDialogByTitle("Create Butler Event");
    const titleInput = dialog?.querySelector("#calendar-event-title") as HTMLInputElement;
    expect(titleInput).toBeDefined();

    await act(async () => {
      setInputValue(titleInput, "Stretch break");
      await flush();
    });

    const saveButton = Array.from(dialog?.querySelectorAll("button") ?? []).find(
      (button) => button.textContent?.trim() === "Create Event",
    ) as HTMLButtonElement;
    await act(async () => {
      saveButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(mutateButlerEvent).toHaveBeenCalled();
    const [payload] = mutateButlerEvent.mock.calls.at(-1) ?? [];
    expect(payload).toEqual(
      expect.objectContaining({
        butler_name: "general",
        action: "create",
        request_id: expect.stringMatching(/^calendar-create-/),
        payload: expect.objectContaining({
          title: "Stretch break",
          source_hint: "butler_reminder",
        }),
      }),
    );
  });

  it("updates butler event through workspace mutation endpoint", async () => {
    setButlerWorkspaceFixtures();
    renderPage("/butlers/calendar?view=butler&range=week&anchor=2026-03-01");

    const editButton = findButton("Edit");
    expect(editButton).toBeDefined();
    await act(async () => {
      editButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const dialog = findDialogByTitle("Edit Butler Event");
    const titleInput = dialog?.querySelector("#calendar-event-title") as HTMLInputElement;
    expect(titleInput).toBeDefined();
    await act(async () => {
      setInputValue(titleInput, "Updated daily prep");
      await flush();
    });

    const saveButton = Array.from(dialog?.querySelectorAll("button") ?? []).find(
      (button) => button.textContent?.trim() === "Save Changes",
    ) as HTMLButtonElement;
    await act(async () => {
      saveButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(mutateButlerEvent).toHaveBeenCalled();
    const [payload] = mutateButlerEvent.mock.calls.at(-1) ?? [];
    expect(payload).toEqual(
      expect.objectContaining({
        butler_name: "general",
        action: "update",
        request_id: expect.stringMatching(/^calendar-update-/),
        payload: expect.objectContaining({
          event_id: "sched-1",
          source_hint: "scheduled_task",
          title: "Updated daily prep",
        }),
      }),
    );
  });
});
