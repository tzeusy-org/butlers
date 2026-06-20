// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { toast } from "sonner";

import CalendarWorkspacePage from "@/pages/CalendarWorkspacePage";
import {
  useCalendarWorkspace,
  useCalendarWorkspaceMeta,
  useMutateCalendarWorkspaceButlerEvent,
  useMutateCalendarWorkspaceUserEvent,
  useSetPrimaryCalendar,
  useSyncCalendarWorkspace,
} from "@/hooks/use-calendar-workspace";

vi.mock("@/hooks/use-calendar-workspace", () => ({
  useCalendarWorkspace: vi.fn(),
  useCalendarWorkspaceMeta: vi.fn(),
  useMutateCalendarWorkspaceButlerEvent: vi.fn(),
  useSyncCalendarWorkspace: vi.fn(),
  useMutateCalendarWorkspaceUserEvent: vi.fn(),
  useSetPrimaryCalendar: vi.fn(),
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
        primary_calendar_id: null,
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
        conflicts: [],
        suggested_slots: [],
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

type UsePrimaryMutationResult = ReturnType<typeof useSetPrimaryCalendar>;

function setPrimaryCalendarState(state?: Partial<UsePrimaryMutationResult>) {
  vi.mocked(useSetPrimaryCalendar).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
    ...state,
  } as unknown as UsePrimaryMutationResult);
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
        primary_calendar_id: null,
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
    setPrimaryCalendarState();
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
              path="/calendar"
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
    renderPage("/calendar?view=butler&range=list&anchor=2026-03-01");

    expect(findButton("Butler")?.getAttribute("aria-pressed")).toBe("true");
    expect(findButton("List")?.getAttribute("aria-pressed")).toBe("true");
    expect(latestWorkspaceParams()?.view).toBe("butler");
    expect(getSearchText()).toContain("view=butler");
    expect(getSearchText()).toContain("range=list");
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

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    const openCreateButton = document.querySelector(
      'button[aria-label="Create user event"]',
    ) as HTMLButtonElement;
    await act(async () => {
      openCreateButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const dialog = findDialogByTitle("Create user event");
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

  it("excludes unsubmittable (null-butler) calendars from the create-event dropdown", async () => {
    // One submittable calendar (butler resolves) and one unsubmittable one
    // whose butler_name is null — selecting it would fail at submit with
    // "Could not resolve owning butler", so it must not appear in the dropdown.
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
              source_key: "google:orphan",
              provider: "google",
              calendar_id: "orphan",
              display_name: "Orphan",
              butler_name: null,
            },
          ],
          lane_definitions: [],
          default_timezone: "UTC",
          primary_calendar_id: null,
        },
        meta: {},
      },
    } as Partial<UseWorkspaceMetaResult>);

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    const openCreateButton = document.querySelector(
      'button[aria-label="Create user event"]',
    ) as HTMLButtonElement;
    await act(async () => {
      openCreateButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const dialog = findDialogByTitle("Create user event");
    const select = dialog?.querySelector("#event-source") as HTMLSelectElement;
    expect(select).toBeDefined();

    const optionValues = Array.from(select.querySelectorAll("option")).map(
      (option) => (option as HTMLOptionElement).value,
    );
    expect(optionValues).toContain("google:primary");
    expect(optionValues).not.toContain("google:orphan");
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

    renderPage("/calendar?view=user&range=list&anchor=2026-03-01");

    const editButton = findButton("Edit");
    expect(editButton).toBeDefined();

    await act(async () => {
      editButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const dialog = findDialogByTitle("Edit user event");
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

    renderPage("/calendar?view=user&range=list&anchor=2026-03-01");

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

  it("shows an error toast (not success) when create user event hard-fails (status=error)", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({
      data: {
        action: "create",
        tool_name: "calendar_create_event",
        request_id: "req-create",
        result: { status: "error", error: "calendar not accessible" },
        conflicts: [],
        suggested_slots: [],
        projection_version: null,
        staleness_ms: null,
        projection_freshness: null,
      },
      meta: {},
    });
    setUserMutationState({ mutateAsync });

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    const openCreateButton = document.querySelector(
      'button[aria-label="Create user event"]',
    ) as HTMLButtonElement;
    await act(async () => {
      openCreateButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const dialog = findDialogByTitle("Create user event");
    const titleInput = dialog?.querySelector("#event-title") as HTMLInputElement;
    await act(async () => {
      setInputValue(titleInput, "Team review");
      await flush();
    });

    const form = titleInput.closest("form") as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await flush();
    });

    expect(mutateAsync).toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining("calendar not accessible"),
    );
    expect(toast.success).not.toHaveBeenCalled();
    // Dialog stays open on soft failure so the user can retry.
    expect(findDialogByTitle("Create user event")).toBeDefined();
  });

  it("shows conflict card (not error toast) when create user event returns status=conflict", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({
      data: {
        action: "create",
        tool_name: "calendar_create_event",
        request_id: "req-conflict",
        result: { status: "conflict", policy: "suggest" },
        conflicts: [
          {
            event_id: "evt-existing",
            title: "Existing meeting",
            start_at: "2026-03-01T10:00:00Z",
            end_at: "2026-03-01T10:30:00Z",
            timezone: "UTC",
          },
        ],
        suggested_slots: [
          {
            start_at: "2026-03-01T10:30:00Z",
            end_at: "2026-03-01T11:00:00Z",
            timezone: "UTC",
          },
        ],
        projection_version: null,
        staleness_ms: null,
        projection_freshness: null,
      },
      meta: {},
    });
    setUserMutationState({ mutateAsync });

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    const openCreateButton = document.querySelector(
      'button[aria-label="Create user event"]',
    ) as HTMLButtonElement;
    await act(async () => {
      openCreateButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const dialog = findDialogByTitle("Create user event");
    const titleInput = dialog?.querySelector("#event-title") as HTMLInputElement;
    await act(async () => {
      setInputValue(titleInput, "Team review");
      await flush();
    });

    const form = titleInput.closest("form") as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await flush();
    });

    expect(mutateAsync).toHaveBeenCalled();
    // status='conflict' must NOT toast an error — it shows the conflict card instead.
    expect(toast.error).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
    // Dialog stays open.
    expect(findDialogByTitle("Create user event")).toBeDefined();
    // Conflict card is visible.
    const conflictCard = dialog?.querySelector('[data-testid="conflict-card"]');
    expect(conflictCard).toBeDefined();
    expect(conflictCard?.textContent).toContain("Overlaps 1 event");
    expect(conflictCard?.textContent).toContain("Existing meeting");
    // Suggested-slot pill is rendered.
    const pills = dialog?.querySelectorAll('[data-testid="conflict-slot-pill"]');
    expect(pills?.length).toBe(1);
    // Book anyway button is rendered.
    const bookAnyway = dialog?.querySelector('[data-testid="conflict-book-anyway"]');
    expect(bookAnyway).toBeDefined();
  });

  it("slot pill shows date prefix when suggested slot is on a different day", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({
      data: {
        action: "create",
        tool_name: "calendar_create_event",
        request_id: "req-cross-day",
        result: { status: "conflict", policy: "suggest" },
        conflicts: [],
        suggested_slots: [
          {
            // Same day as original request — no date prefix expected
            start_at: "2026-03-01T10:30:00Z",
            end_at: "2026-03-01T11:00:00Z",
            timezone: "UTC",
          },
          {
            // Different day — date prefix expected
            start_at: "2026-03-02T09:00:00Z",
            end_at: "2026-03-02T09:30:00Z",
            timezone: "UTC",
          },
        ],
        projection_version: null,
        staleness_ms: null,
        projection_freshness: null,
      },
      meta: {},
    });
    setUserMutationState({ mutateAsync });

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    const openCreateButton = document.querySelector(
      'button[aria-label="Create user event"]',
    ) as HTMLButtonElement;
    await act(async () => {
      openCreateButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const dialog = findDialogByTitle("Create user event");
    const titleInput = dialog?.querySelector("#event-title") as HTMLInputElement;
    await act(async () => {
      setInputValue(titleInput, "Cross-day test");
      await flush();
    });

    const form = titleInput.closest("form") as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await flush();
    });

    const pills = dialog?.querySelectorAll('[data-testid="conflict-slot-pill"]') as NodeListOf<HTMLButtonElement>;
    expect(pills?.length).toBe(2);
    // First pill: same day — time only, no date prefix
    expect(pills[0].textContent).toMatch(/^\d+:\d+ [AP]M/);
    expect(pills[0].textContent).not.toMatch(/Mar \d/);
    // Second pill: different day — must include the date prefix
    expect(pills[1].textContent).toContain("Mar 2");
  });

  it("slot pill re-submits with new start/end and same request_id", async () => {
    const requestId = "req-slot-retry";
    let callCount = 0;
    const mutateAsync = vi.fn().mockImplementation(() => {
      callCount++;
      if (callCount === 1) {
        // First call: conflict
        return Promise.resolve({
          data: {
            action: "create",
            tool_name: "calendar_create_event",
            request_id: requestId,
            result: { status: "conflict" },
            conflicts: [
              {
                event_id: "evt-x",
                title: "Blocker",
                start_at: "2026-03-01T10:00:00Z",
                end_at: "2026-03-01T10:30:00Z",
                timezone: "UTC",
              },
            ],
            suggested_slots: [
              {
                start_at: "2026-03-01T11:00:00Z",
                end_at: "2026-03-01T11:30:00Z",
                timezone: "UTC",
              },
            ],
            projection_version: null,
            staleness_ms: null,
            projection_freshness: null,
          },
          meta: {},
        });
      }
      // Second call (pill click): success
      return Promise.resolve({
        data: {
          action: "create",
          tool_name: "calendar_create_event",
          request_id: requestId,
          result: { status: "created" },
          conflicts: [],
          suggested_slots: [],
          projection_version: null,
          staleness_ms: null,
          projection_freshness: null,
        },
        meta: {},
      });
    });
    setUserMutationState({ mutateAsync });

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    const openCreateButton = document.querySelector(
      'button[aria-label="Create user event"]',
    ) as HTMLButtonElement;
    await act(async () => {
      openCreateButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const dialog = findDialogByTitle("Create user event");
    const titleInput = dialog?.querySelector("#event-title") as HTMLInputElement;
    await act(async () => {
      setInputValue(titleInput, "Team review");
      await flush();
    });

    // First submit → conflict
    const form = titleInput.closest("form") as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await flush();
    });

    expect(callCount).toBe(1);
    const pill = dialog?.querySelector('[data-testid="conflict-slot-pill"]') as HTMLButtonElement;
    expect(pill).toBeDefined();

    // Click the slot pill → re-submit
    await act(async () => {
      pill.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(callCount).toBe(2);
    // Second call passes the slot's times (new start_at/end_at)
    const secondCall = mutateAsync.mock.calls[1][0];
    expect(secondCall.payload.start_at).toBe("2026-03-01T11:00:00Z");
    expect(secondCall.payload.end_at).toBe("2026-03-01T11:30:00Z");
    // request_id must match the original (same as first call)
    expect(secondCall.request_id).toBe(mutateAsync.mock.calls[0][0].request_id);
    // Dialog closes on success
    expect(toast.success).toHaveBeenCalled();
  });

  it("Book anyway re-submits with conflict_policy=allow_overlap", async () => {
    let callCount = 0;
    const mutateAsync = vi.fn().mockImplementation(() => {
      callCount++;
      if (callCount === 1) {
        return Promise.resolve({
          data: {
            action: "create",
            tool_name: "calendar_create_event",
            request_id: "req-book",
            result: { status: "conflict" },
            conflicts: [
              {
                event_id: "evt-block",
                title: "Blocked slot",
                start_at: "2026-03-01T10:00:00Z",
                end_at: "2026-03-01T10:30:00Z",
                timezone: "UTC",
              },
            ],
            suggested_slots: [],
            projection_version: null,
            staleness_ms: null,
            projection_freshness: null,
          },
          meta: {},
        });
      }
      return Promise.resolve({
        data: {
          action: "create",
          tool_name: "calendar_create_event",
          request_id: "req-override",
          result: { status: "created" },
          conflicts: [],
          suggested_slots: [],
          projection_version: null,
          staleness_ms: null,
          projection_freshness: null,
        },
        meta: {},
      });
    });
    setUserMutationState({ mutateAsync });

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    const openCreateButton = document.querySelector(
      'button[aria-label="Create user event"]',
    ) as HTMLButtonElement;
    await act(async () => {
      openCreateButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const dialog = findDialogByTitle("Create user event");
    const titleInput = dialog?.querySelector("#event-title") as HTMLInputElement;
    await act(async () => {
      setInputValue(titleInput, "Override test");
      await flush();
    });

    const form = titleInput.closest("form") as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await flush();
    });

    expect(callCount).toBe(1);

    const bookAnyway = dialog?.querySelector('[data-testid="conflict-book-anyway"]') as HTMLButtonElement;
    expect(bookAnyway).toBeDefined();

    await act(async () => {
      bookAnyway.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(callCount).toBe(2);
    const overrideCall = mutateAsync.mock.calls[1][0];
    expect(overrideCall.payload.conflict_policy).toBe("allow_overlap");
    expect(toast.success).toHaveBeenCalled();
  });

  it("shows a success toast when create user event genuinely succeeds", async () => {
    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    const openCreateButton = document.querySelector(
      'button[aria-label="Create user event"]',
    ) as HTMLButtonElement;
    await act(async () => {
      openCreateButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const dialog = findDialogByTitle("Create user event");
    const titleInput = dialog?.querySelector("#event-title") as HTMLInputElement;
    await act(async () => {
      setInputValue(titleInput, "Team review");
      await flush();
    });

    const form = titleInput.closest("form") as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await flush();
    });

    // Default user-mutation fixture returns result.status === "created".
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining("created"));
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("renders butler lanes grouped with lane metadata", () => {
    setButlerWorkspaceFixtures();
    renderPage("/calendar?view=butler&range=week&anchor=2026-03-01");

    expect(container.textContent).toContain("General lane");
    expect(container.textContent).toContain("Health lane");
    expect(container.textContent).toContain("Daily prep");
    expect(container.textContent).toContain("Hydration check");
  });

  it("creates butler event through workspace mutation endpoint", async () => {
    setButlerWorkspaceFixtures();
    renderPage("/calendar?view=butler&range=week&anchor=2026-03-01");

    const createButton = findButton("Create butler event");
    expect(createButton).toBeDefined();
    await act(async () => {
      createButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const dialog = findDialogByTitle("Create butler event");
    const titleInput = dialog?.querySelector("#calendar-event-title") as HTMLInputElement;
    expect(titleInput).toBeDefined();

    await act(async () => {
      setInputValue(titleInput, "Stretch break");
      await flush();
    });

    const saveButton = Array.from(dialog?.querySelectorAll("button") ?? []).find(
      (button) => button.textContent?.trim() === "Create event",
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
    renderPage("/calendar?view=butler&range=week&anchor=2026-03-01");

    const editButton = findButton("Edit");
    expect(editButton).toBeDefined();
    await act(async () => {
      editButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const dialog = findDialogByTitle("Edit butler event");
    const titleInput = dialog?.querySelector("#calendar-event-title") as HTMLInputElement;
    expect(titleInput).toBeDefined();
    await act(async () => {
      setInputValue(titleInput, "Updated daily prep");
      await flush();
    });

    const saveButton = Array.from(dialog?.querySelectorAll("button") ?? []).find(
      (button) => button.textContent?.trim() === "Save changes",
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

  it("shows an error toast (not success) when a butler delete soft-fails", async () => {
    setButlerWorkspaceFixtures();
    // Invoke onSuccess with a soft-failed envelope, as react-query would on HTTP 200.
    mutateButlerEvent.mockImplementation((_payload, options) => {
      options?.onSuccess?.({
        data: {
          action: "delete",
          tool_name: "calendar_delete_butler_event",
          request_id: "req-del",
          result: { status: "not_found", error: "event no longer exists" },
          projection_version: null,
          staleness_ms: null,
          projection_freshness: null,
        },
        meta: {},
      });
    });

    renderPage("/calendar?view=butler&range=week&anchor=2026-03-01");

    const deleteButton = findButton("Delete");
    expect(deleteButton).toBeDefined();
    await act(async () => {
      deleteButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(mutateButlerEvent).toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("event no longer exists"));
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("shows a success toast when a butler delete genuinely succeeds", async () => {
    setButlerWorkspaceFixtures();
    mutateButlerEvent.mockImplementation((_payload, options) => {
      options?.onSuccess?.({
        data: {
          action: "delete",
          tool_name: "calendar_delete_butler_event",
          request_id: "req-del",
          result: { status: "deleted" },
          projection_version: null,
          staleness_ms: null,
          projection_freshness: null,
        },
        meta: {},
      });
    });

    renderPage("/calendar?view=butler&range=week&anchor=2026-03-01");

    const deleteButton = findButton("Delete");
    await act(async () => {
      deleteButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(toast.success).toHaveBeenCalledWith("Event deleted");
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("shows an error toast when set-primary returns persisted: false", async () => {
    setButlerWorkspaceFixtures();
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
              source_id: "source-g1",
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
              staleness_ms: 900,
            },
          ],
          writable_calendars: [],
          lane_definitions: [],
          default_timezone: "UTC",
          primary_calendar_id: null,
        },
        meta: {},
      },
    } as Partial<UseWorkspaceMetaResult>);

    const setPrimaryMutate = vi.fn((_body, options) => {
      options?.onSuccess?.({
        data: { old_calendar_id: null, new_calendar_id: "work", persisted: false },
        meta: {},
      });
    });
    setPrimaryCalendarState({ mutate: setPrimaryMutate });

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    await act(async () => {
      const configureButton = document.querySelector(
        'button[aria-label="Configure sources"]',
      ) as HTMLButtonElement;
      configureButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const setPrimaryButton = findButton("Set as primary");
    expect(setPrimaryButton).toBeDefined();
    await act(async () => {
      setPrimaryButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(setPrimaryMutate).toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("not persisted"));
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("shows a success toast when set-primary persists", async () => {
    setButlerWorkspaceFixtures();
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
              source_id: "source-g1",
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
              staleness_ms: 900,
            },
          ],
          writable_calendars: [],
          lane_definitions: [],
          default_timezone: "UTC",
          primary_calendar_id: null,
        },
        meta: {},
      },
    } as Partial<UseWorkspaceMetaResult>);

    const setPrimaryMutate = vi.fn((_body, options) => {
      options?.onSuccess?.({
        data: { old_calendar_id: null, new_calendar_id: "work", persisted: true },
        meta: {},
      });
    });
    setPrimaryCalendarState({ mutate: setPrimaryMutate });

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    await act(async () => {
      const configureButton = document.querySelector(
        'button[aria-label="Configure sources"]',
      ) as HTMLButtonElement;
      configureButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const setPrimaryButton = findButton("Set as primary");
    await act(async () => {
      setPrimaryButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(toast.success).toHaveBeenCalledWith("Primary calendar updated");
    expect(toast.error).not.toHaveBeenCalled();
  });

  describe("recurring instance capping", () => {
    function makeRecurringEntries(
      scheduleId: string,
      count: number,
      dayPrefix: string = "2026-03-01",
    ) {
      return Array.from({ length: count }, (_, i) => ({
        entry_id: `entry-${scheduleId}-${dayPrefix}-${i}`,
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
});
