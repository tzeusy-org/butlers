// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { toast } from "sonner";

import CalendarWorkspacePage from "@/pages/CalendarWorkspacePage";
import {
  useCalendarMeetingPrep,
  useCalendarWorkspace,
  useCalendarWorkspaceMeta,
  useFindCalendarWorkspaceTime,
  useMutateCalendarWorkspaceButlerEvent,
  useMutateCalendarWorkspaceUserEvent,
  usePreviewCalendarWorkspaceButlerEvent,
  useSetPrimaryCalendar,
  useSyncCalendarWorkspace,
  useToggleCalendarSource,
} from "@/hooks/use-calendar-workspace";

vi.mock("@/hooks/use-calendar-workspace", () => ({
  useCalendarWorkspace: vi.fn(),
  useCalendarOverlays: vi.fn(() => ({
    isLoading: false,
    isError: false,
    isFetched: false,
    error: null,
    data: { data: { entries: [], has_domain_context: false } },
  })),
  useCalendarDayBriefing: vi.fn(() => ({
    isLoading: false,
    isError: false,
    isFetched: false,
    error: null,
    data: {
      data: {
        date: "2026-02-23",
        timezone: "Asia/Singapore",
        has_domain_context: false,
        has_entries: false,
        groups: [],
        entries: [],
      },
    },
  })),
  useCalendarMeetingPrep: vi.fn(() => ({
    isLoading: false,
    isError: false,
    isFetched: false,
    error: null,
    data: {
      data: {
        event_id: "00000000-0000-0000-0000-000000000000",
        has_prep_context: false,
        attendees: [],
        source_butlers: [],
      },
    },
  })),
  useCalendarWorkspaceMeta: vi.fn(),
  useCalendarWorkspaceAudit: vi.fn(() => ({
    isLoading: false,
    isError: false,
    error: null,
    data: { data: { entries: [], total: 0, offset: 0, limit: 50 } },
  })),
  useCalendarWorkspaceEntry: vi.fn(() => ({
    isLoading: false,
    isError: false,
    error: null,
    data: null,
  })),
  useMutateCalendarWorkspaceButlerEvent: vi.fn(),
  useSyncCalendarWorkspace: vi.fn(),
  useFindCalendarWorkspaceTime: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
    isError: false,
  })),
  useMutateCalendarWorkspaceUserEvent: vi.fn(),
  useParseCalendarQuickAdd: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
    reset: vi.fn(),
  })),
  usePreviewCalendarWorkspaceButlerEvent: vi.fn(),
  useSetPrimaryCalendar: vi.fn(),
  useCalendarAccounts: vi.fn(() => ({
    isLoading: false,
    isError: false,
    error: null,
    data: { data: { accounts: [], health_available: true } },
  })),
  useToggleCalendarSource: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useCalendarProposals: vi.fn(() => ({
    isLoading: false,
    isError: false,
    error: null,
    data: { data: { entries: [] } },
  })),
  useAcceptCalendarProposal: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useDismissCalendarProposal: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

// Collapse the recurrence-preview debounce to identity so the dialog reacts
// synchronously to recurrence changes (no 400ms timer to advance in tests).
vi.mock("@/hooks/use-debounce", () => ({
  useDebounce: <T,>(value: T) => value,
}));

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

type UseWorkspaceResult = ReturnType<typeof useCalendarWorkspace>;
type UseWorkspaceMetaResult = ReturnType<typeof useCalendarWorkspaceMeta>;
type UseButlerMutationResult = ReturnType<
  typeof useMutateCalendarWorkspaceButlerEvent
>;
type UseSyncResult = ReturnType<typeof useSyncCalendarWorkspace>;
type UseUserMutationResult = ReturnType<
  typeof useMutateCalendarWorkspaceUserEvent
>;

const mutateButlerEvent = vi.fn();
const mutateUserEvent = vi.fn();

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
            event_id: "evt-entry-1",
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
            error_kind: "none",
            sync_enabled: true,
          },
        ],
        lanes: [],
        next_cursor: null,
        has_more: false,
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
            error_kind: "none",
            sync_enabled: true,
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
            error_kind: "none",
            sync_enabled: true,
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

type UsePreviewResult = ReturnType<
  typeof usePreviewCalendarWorkspaceButlerEvent
>;
const previewMutate = vi.fn();

function setRecurrencePreviewState(
  data: unknown = {
    data: {
      occurrences: ["2026-03-02T09:00:00+00:00", "2026-03-09T09:00:00+00:00"],
      total_in_window: 13,
      more_count: 7,
      window_start: "2026-03-01T09:00:00+00:00",
      window_end: "2026-05-30T09:00:00+00:00",
      effective_cron: "0 9 * * 1",
      notes: [
        "INTERVAL=2 is not supported by the butler scheduler — will fire every week.",
      ],
    },
  },
) {
  vi.mocked(usePreviewCalendarWorkspaceButlerEvent).mockReturnValue({
    mutate: previewMutate,
    reset: vi.fn(),
    data,
    isError: false,
    isPending: false,
  } as unknown as UsePreviewResult);
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
    mutate: mutateUserEvent,
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
            event_id: "evt-entry-butler-1",
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
            event_id: "evt-entry-butler-2",
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
            error_kind: "none",
            sync_enabled: true,
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
            error_kind: "none",
            sync_enabled: true,
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
        next_cursor: null,
        has_more: false,
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
            error_kind: "none",
            sync_enabled: true,
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
            error_kind: "none",
            sync_enabled: true,
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
    mutateUserEvent.mockReset();
    setWorkspaceState();
    setWorkspaceMetaState();
    setButlerMutationState();
    setSyncState();
    setUserMutationState();
    setPrimaryCalendarState();
    previewMutate.mockReset();
    setRecurrencePreviewState();
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );

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
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[initialEntry]}>
            <Routes>
              <Route
                path="/calendar"
                element={
                  <>
                    <CalendarWorkspacePage />
                    <SearchEcho />
                  </>
                }
              />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
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
    return Array.from(
      document.querySelectorAll('[data-slot="dialog-content"]'),
    ).find((dialog) => dialog.textContent?.includes(title));
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
      openCreateButton.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    const dialog = findDialogByTitle("Create user event");
    const titleInput = dialog?.querySelector(
      "#event-title",
    ) as HTMLInputElement;
    expect(titleInput).toBeDefined();

    await act(async () => {
      setInputValue(titleInput, "Team review");
      await flush();
    });

    const form = titleInput.closest("form") as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
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
              error_kind: "none",
              sync_enabled: true,
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
      openCreateButton.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
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

  it("updates user event title via detail panel inline edit", async () => {
    renderPage("/calendar?view=user&range=list&anchor=2026-03-01");

    // "Detail" button in list view opens the panel
    const detailButton = findButton("Detail");
    expect(detailButton).toBeDefined();

    await act(async () => {
      detailButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const panel = container.querySelector('[data-testid="entry-detail-panel"]');
    expect(panel).toBeDefined();

    const titleInput = panel?.querySelector(
      '[data-testid="detail-title-input"]',
    ) as HTMLInputElement;
    expect(titleInput).toBeDefined();

    // Update the title draft
    await act(async () => {
      setInputValue(titleInput, "Morning review");
      await flush();
    });
    // Blur triggers save-on-blur mutation
    await act(async () => {
      titleInput.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
      await flush();
    });

    expect(mutateUserEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        butler_name: "general",
        action: "update",
        payload: expect.objectContaining({
          event_id: "evt-1",
          calendar_id: "primary",
          title: "Morning review",
        }),
      }),
      // Blur-save attaches success/error callbacks so the auto-save outcome is
      // surfaced (toast + "Saved ✓"/"Save failed" indicator).
      expect.objectContaining({
        onSuccess: expect.any(Function),
        onError: expect.any(Function),
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
      rowDeleteButton?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    const deleteDialog = findDialogByTitle("Delete Event");
    const confirmDeleteButton = Array.from(
      deleteDialog?.querySelectorAll("button") ?? [],
    ).find(
      (button) => button.textContent?.trim() === "Delete",
    ) as HTMLButtonElement;

    await act(async () => {
      confirmDeleteButton.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
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

  it("deletes a recurring occurrence with the chosen recurrence scope", async () => {
    const recurringEntry = (suffix: string, start: string) => ({
      entry_id: `rec-${suffix}`,
      event_id: `evt-rec-${suffix}`,
      view: "user" as const,
      source_type: "provider_event" as const,
      source_key: "google:primary",
      title: "Weekly standup",
      start_at: start,
      end_at: start.replace("T09:00", "T09:30"),
      timezone: "UTC",
      all_day: false,
      calendar_id: "primary",
      provider_event_id: "rec-evt",
      butler_name: "general",
      schedule_id: null,
      reminder_id: null,
      rrule: "RRULE:FREQ=WEEKLY;BYDAY=TU",
      cron: null,
      until_at: null,
      status: "active",
      sync_state: "fresh" as const,
      editable: true,
      metadata: {},
    });
    setWorkspaceState({
      data: {
        data: {
          entries: [
            recurringEntry("1", "2026-03-03T09:00:00Z"),
            recurringEntry("2", "2026-03-10T09:00:00Z"),
          ],
          source_freshness: [],
          lanes: [],
          next_cursor: null,
          has_more: false,
        },
        meta: {},
      },
    } as unknown as Partial<UseWorkspaceResult>);
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
    await act(async () => {
      rowDeleteButton?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    // The three-option scope sheet renders for a recurring occurrence.
    const scopeSheet = document.querySelector(
      '[data-testid="delete-recurrence-scope"]',
    );
    expect(scopeSheet).not.toBeNull();
    const followingRadio = document.querySelector(
      '[data-testid="delete-scope-following"] input',
    ) as HTMLInputElement;
    expect(followingRadio).toBeDefined();

    await act(async () => {
      followingRadio.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const deleteDialog = findDialogByTitle("Delete Event");
    const confirmDeleteButton = Array.from(
      deleteDialog?.querySelectorAll("button") ?? [],
    ).find(
      (button) => button.textContent?.trim() === "Delete",
    ) as HTMLButtonElement;
    await act(async () => {
      confirmDeleteButton.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "delete",
        payload: expect.objectContaining({
          event_id: "rec-evt",
          recurrence_scope: "following",
          instance_start_at: "2026-03-03T09:00:00Z",
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
      openCreateButton.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    const dialog = findDialogByTitle("Create user event");
    const titleInput = dialog?.querySelector(
      "#event-title",
    ) as HTMLInputElement;
    await act(async () => {
      setInputValue(titleInput, "Team review");
      await flush();
    });

    const form = titleInput.closest("form") as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
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
      openCreateButton.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    const dialog = findDialogByTitle("Create user event");
    const titleInput = dialog?.querySelector(
      "#event-title",
    ) as HTMLInputElement;
    await act(async () => {
      setInputValue(titleInput, "Team review");
      await flush();
    });

    const form = titleInput.closest("form") as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
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
    const pills = dialog?.querySelectorAll(
      '[data-testid="conflict-slot-pill"]',
    );
    expect(pills?.length).toBe(1);
    // Book anyway button is rendered.
    const bookAnyway = dialog?.querySelector(
      '[data-testid="conflict-book-anyway"]',
    );
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
      openCreateButton.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    const dialog = findDialogByTitle("Create user event");
    const titleInput = dialog?.querySelector(
      "#event-title",
    ) as HTMLInputElement;
    await act(async () => {
      setInputValue(titleInput, "Cross-day test");
      await flush();
    });

    const form = titleInput.closest("form") as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
      await flush();
    });

    const pills = dialog?.querySelectorAll(
      '[data-testid="conflict-slot-pill"]',
    ) as NodeListOf<HTMLButtonElement>;
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
      openCreateButton.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    const dialog = findDialogByTitle("Create user event");
    const titleInput = dialog?.querySelector(
      "#event-title",
    ) as HTMLInputElement;
    await act(async () => {
      setInputValue(titleInput, "Team review");
      await flush();
    });

    // First submit → conflict
    const form = titleInput.closest("form") as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
      await flush();
    });

    expect(callCount).toBe(1);
    const pill = dialog?.querySelector(
      '[data-testid="conflict-slot-pill"]',
    ) as HTMLButtonElement;
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
      openCreateButton.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    const dialog = findDialogByTitle("Create user event");
    const titleInput = dialog?.querySelector(
      "#event-title",
    ) as HTMLInputElement;
    await act(async () => {
      setInputValue(titleInput, "Override test");
      await flush();
    });

    const form = titleInput.closest("form") as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
      await flush();
    });

    expect(callCount).toBe(1);

    const bookAnyway = dialog?.querySelector(
      '[data-testid="conflict-book-anyway"]',
    ) as HTMLButtonElement;
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
      openCreateButton.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    const dialog = findDialogByTitle("Create user event");
    const titleInput = dialog?.querySelector(
      "#event-title",
    ) as HTMLInputElement;
    await act(async () => {
      setInputValue(titleInput, "Team review");
      await flush();
    });

    const form = titleInput.closest("form") as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
      await flush();
    });

    // Default user-mutation fixture returns result.status === "created".
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringContaining("created"),
    );
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
    const titleInput = dialog?.querySelector(
      "#calendar-event-title",
    ) as HTMLInputElement;
    expect(titleInput).toBeDefined();

    await act(async () => {
      setInputValue(titleInput, "Stretch break");
      await flush();
    });

    const saveButton = Array.from(
      dialog?.querySelectorAll("button") ?? [],
    ).find(
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

  it("renders a live recurrence preview strip when a recurrence is set", async () => {
    setButlerWorkspaceFixtures();
    renderPage("/calendar?view=butler&range=week&anchor=2026-03-01");

    const createButton = findButton("Create butler event");
    await act(async () => {
      createButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const dialog = findDialogByTitle("Create butler event");
    expect(dialog).toBeDefined();

    // No recurrence yet -> no preview strip.
    expect(
      dialog?.querySelector('[data-testid="recurrence-preview"]'),
    ).toBeNull();

    const frequencySelect = dialog?.querySelector(
      "#calendar-frequency",
    ) as HTMLSelectElement;
    expect(frequencySelect).toBeDefined();
    await act(async () => {
      frequencySelect.value = "WEEKLY";
      frequencySelect.dispatchEvent(new Event("change", { bubbles: true }));
      await flush();
    });

    // Preview fired with an RRULE carrying the chosen frequency.
    expect(previewMutate).toHaveBeenCalled();
    const [previewBody] = previewMutate.mock.calls.at(-1) ?? [];
    expect(previewBody).toEqual(
      expect.objectContaining({
        rrule: expect.stringContaining("FREQ=WEEKLY"),
        limit: 6,
      }),
    );

    // Strip renders projected dates, the +N sentinel, and the lossy note.
    const strip = dialog?.querySelector('[data-testid="recurrence-preview"]');
    expect(strip).not.toBeNull();
    expect(
      strip?.querySelector('[data-testid="recurrence-preview-more"]')
        ?.textContent,
    ).toContain("+7 more in 90 days");
    expect(
      strip?.querySelector('[data-testid="recurrence-preview-note"]')
        ?.textContent,
    ).toContain("INTERVAL=2");
  });

  it("updates butler event title via detail panel inline edit", async () => {
    setButlerWorkspaceFixtures();
    renderPage("/calendar?view=butler&range=week&anchor=2026-03-01");

    // Butler lane "Edit" button opens the detail panel
    const editButton = findButton("Edit");
    expect(editButton).toBeDefined();
    await act(async () => {
      editButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const panel = container.querySelector('[data-testid="entry-detail-panel"]');
    expect(panel).toBeDefined();

    const titleInput = panel?.querySelector(
      '[data-testid="detail-title-input"]',
    ) as HTMLInputElement;
    expect(titleInput).toBeDefined();

    await act(async () => {
      setInputValue(titleInput, "Updated daily prep");
      await flush();
    });
    await act(async () => {
      titleInput.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
      await flush();
    });

    expect(mutateButlerEvent).toHaveBeenCalled();
    const [payload] = mutateButlerEvent.mock.calls.at(-1) ?? [];
    expect(payload).toEqual(
      expect.objectContaining({
        butler_name: "general",
        action: "update",
        request_id: expect.stringMatching(/^detail-update-/),
        payload: expect.objectContaining({
          event_id: "sched-1",
          source_hint: "scheduled_task",
          title: "Updated daily prep",
        }),
      }),
    );
  });

  function firePointer(
    el: Element,
    type: string,
    clientY: number,
    clientX = 20,
  ) {
    el.dispatchEvent(
      new PointerEvent(type, {
        bubbles: true,
        cancelable: true,
        pointerId: 1,
        button: 0,
        clientX,
        clientY,
      }),
    );
  }

  function findGridEvent(label: string): HTMLButtonElement | undefined {
    // Grid event blocks are absolutely positioned via an inline `top` offset.
    return Array.from(document.querySelectorAll("button")).find(
      (button) =>
        button.textContent?.includes(label) && button.style.top !== "",
    );
  }

  it("drag on the empty time grid opens the create dialog with a prefilled window", async () => {
    renderPage("/calendar?view=user&range=day&anchor=2026-03-01");

    const surface = document.querySelector(
      'button[aria-label^="Create event on"]',
    ) as HTMLButtonElement;
    expect(surface).toBeTruthy();

    await act(async () => {
      firePointer(surface, "pointerdown", 540); // 09:00
      firePointer(surface, "pointermove", 600); // drag to 10:00
      firePointer(surface, "pointerup", 600);
      await flush();
    });

    const dialog = findDialogByTitle("Create user event");
    expect(dialog).toBeDefined();
    const startInput = dialog?.querySelector(
      "#event-start",
    ) as HTMLInputElement;
    const endInput = dialog?.querySelector("#event-end") as HTMLInputElement;
    expect(startInput.value).toBe("2026-03-01T09:00");
    expect(endInput.value).toBe("2026-03-01T10:00");
  });

  it("drag-moving a user event dispatches an update with new start/end", async () => {
    const userMutateAsync = vi.fn().mockResolvedValue({
      data: {
        result: { status: "updated" },
        conflicts: [],
        suggested_slots: [],
      },
      meta: {},
    });
    setUserMutationState({
      mutateAsync: userMutateAsync,
    } as Partial<UseUserMutationResult>);
    renderPage("/calendar?view=user&range=day&anchor=2026-03-01");

    const eventButton = findGridEvent("Morning planning");
    expect(eventButton).toBeDefined();

    await act(async () => {
      firePointer(eventButton!, "pointerdown", 600);
      firePointer(eventButton!, "pointermove", 660); // +60px => +60min
      firePointer(eventButton!, "pointerup", 660);
      await flush();
    });

    expect(userMutateAsync).toHaveBeenCalledTimes(1);
    const payload = userMutateAsync.mock.calls[0][0];
    expect(payload).toEqual(
      expect.objectContaining({
        action: "update",
        payload: expect.objectContaining({
          event_id: "evt-1",
          start_at: expect.any(String),
          end_at: expect.any(String),
        }),
      }),
    );
  });

  it("snaps a moved user event back when the update soft-fails", async () => {
    const userMutateAsync = vi.fn().mockResolvedValue({
      data: {
        result: { status: "failed", error: "calendar rejected" },
        conflicts: [],
        suggested_slots: [],
      },
      meta: {},
    });
    setUserMutationState({
      mutateAsync: userMutateAsync,
    } as Partial<UseUserMutationResult>);
    renderPage("/calendar?view=user&range=day&anchor=2026-03-01");

    const eventButton = findGridEvent("Morning planning");
    expect(eventButton).toBeDefined();

    await act(async () => {
      firePointer(eventButton!, "pointerdown", 600);
      firePointer(eventButton!, "pointermove", 660);
      firePointer(eventButton!, "pointerup", 660);
      await flush();
    });

    expect(userMutateAsync).toHaveBeenCalledTimes(1);
    expect(toast.error).toHaveBeenCalled();
    // No undo ghost is left behind on a failed move.
    expect(
      document.querySelector('[data-testid="calendar-move-ghost"]'),
    ).toBeNull();
  });

  it("drag-moving a recurring occurrence opens the scope sheet and applies the chosen scope", async () => {
    const userMutateAsync = vi.fn().mockResolvedValue({
      data: {
        result: { status: "updated" },
        conflicts: [],
        suggested_slots: [],
      },
      meta: {},
    });
    setUserMutationState({
      mutateAsync: userMutateAsync,
    } as Partial<UseUserMutationResult>);
    setWorkspaceState({
      data: {
        data: {
          entries: [
            {
              entry_id: "rec-1",
              event_id: "evt-rec-1",
              view: "user",
              source_type: "provider_event",
              source_key: "google:primary",
              title: "Standup",
              start_at: "2026-03-01T09:00:00Z",
              end_at: "2026-03-01T09:30:00Z",
              timezone: "UTC",
              all_day: false,
              calendar_id: "primary",
              provider_event_id: "evt-rec",
              butler_name: "general",
              schedule_id: null,
              reminder_id: null,
              rrule: "RRULE:FREQ=DAILY",
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
          next_cursor: null,
          has_more: false,
        },
        meta: {},
      },
    } as Partial<UseWorkspaceResult>);
    renderPage("/calendar?view=user&range=day&anchor=2026-03-01");

    const eventButton = findGridEvent("Standup");
    expect(eventButton).toBeDefined();
    // Recurring occurrences are now draggable/resizable; the drop routes through the scope sheet.
    expect(
      eventButton?.querySelector('[data-testid="calendar-resize-handle"]'),
    ).not.toBeNull();

    await act(async () => {
      firePointer(eventButton!, "pointerdown", 600);
      firePointer(eventButton!, "pointermove", 660);
      firePointer(eventButton!, "pointerup", 660);
      await flush();
    });

    // The drag does not commit directly — it opens the recurrence scope sheet.
    expect(userMutateAsync).not.toHaveBeenCalled();
    const scopeSheet = document.querySelector(
      '[data-testid="edit-recurrence-scope"]',
    );
    expect(scopeSheet).not.toBeNull();

    const followingRadio = document.querySelector(
      '[data-testid="edit-scope-following"] input',
    ) as HTMLInputElement;
    await act(async () => {
      followingRadio.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const editDialog = findDialogByTitle("Edit recurring event");
    const saveButton = Array.from(
      editDialog?.querySelectorAll("button") ?? [],
    ).find(
      (button) => button.textContent?.trim() === "Save changes",
    ) as HTMLButtonElement;
    await act(async () => {
      saveButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(userMutateAsync).toHaveBeenCalledTimes(1);
    expect(userMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "update",
        payload: expect.objectContaining({
          event_id: "evt-rec",
          recurrence_scope: "following",
          instance_start_at: "2026-03-01T09:00:00Z",
          start_at: expect.any(String),
          end_at: expect.any(String),
        }),
      }),
    );
  });

  it("reports an error (not success) when a recurring edit returns status=conflict", async () => {
    const userMutateAsync = vi.fn().mockResolvedValue({
      data: {
        result: { status: "conflict" },
        conflicts: [],
        suggested_slots: [],
      },
      meta: {},
    });
    setUserMutationState({
      mutateAsync: userMutateAsync,
    } as Partial<UseUserMutationResult>);
    setWorkspaceState({
      data: {
        data: {
          entries: [
            {
              entry_id: "rec-1",
              event_id: "evt-rec-1",
              view: "user",
              source_type: "provider_event",
              source_key: "google:primary",
              title: "Standup",
              start_at: "2026-03-01T09:00:00Z",
              end_at: "2026-03-01T09:30:00Z",
              timezone: "UTC",
              all_day: false,
              calendar_id: "primary",
              provider_event_id: "evt-rec",
              butler_name: "general",
              schedule_id: null,
              reminder_id: null,
              rrule: "RRULE:FREQ=DAILY",
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
          next_cursor: null,
          has_more: false,
        },
        meta: {},
      },
    } as Partial<UseWorkspaceResult>);
    renderPage("/calendar?view=user&range=day&anchor=2026-03-01");

    const eventButton = findGridEvent("Standup");
    await act(async () => {
      firePointer(eventButton!, "pointerdown", 600);
      firePointer(eventButton!, "pointermove", 660);
      firePointer(eventButton!, "pointerup", 660);
      await flush();
    });

    const editDialog = findDialogByTitle("Edit recurring event");
    const saveButton = Array.from(
      editDialog?.querySelectorAll("button") ?? [],
    ).find(
      (button) => button.textContent?.trim() === "Save changes",
    ) as HTMLButtonElement;
    await act(async () => {
      saveButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    // A conflict is NOT a success: surface an error and keep the scope sheet open
    // (the edit never landed) instead of falsely reporting the change applied.
    expect(userMutateAsync).toHaveBeenCalledTimes(1);
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining("conflicts"),
    );
    expect(toast.success).not.toHaveBeenCalled();
    expect(
      document.querySelector('[data-testid="edit-recurrence-scope"]'),
    ).not.toBeNull();
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
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining("event no longer exists"),
    );
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

  it("snoozes a butler event to a preset time via the snooze menu", async () => {
    setButlerWorkspaceFixtures();
    mutateButlerEvent.mockImplementation((_payload, options) => {
      options?.onSuccess?.({
        data: {
          action: "snooze",
          tool_name: "calendar_update_butler_event",
          request_id: "req-snooze",
          result: { status: "updated" },
          projection_version: null,
          staleness_ms: null,
          projection_freshness: null,
        },
        meta: {},
      });
    });

    renderPage("/calendar?view=butler&range=list&anchor=2026-03-01");

    const snoozeButton = document.querySelector(
      '[data-testid="butler-snooze-button"]',
    ) as HTMLButtonElement;
    expect(snoozeButton).toBeTruthy();
    await act(async () => {
      snoozeButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const preset = document.querySelector(
      '[data-testid="butler-snooze-preset-1 hour"]',
    ) as HTMLButtonElement;
    expect(preset).toBeTruthy();
    await act(async () => {
      preset.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(mutateButlerEvent).toHaveBeenCalled();
    const [body] = mutateButlerEvent.mock.calls.at(-1) ?? [];
    expect(body.action).toBe("snooze");
    expect(typeof body.payload.due_at).toBe("string");
    expect(body.payload.event_id).toBeTruthy();
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringContaining("Snoozed to"),
    );
  });

  it("dismisses a due reminder from the grid", async () => {
    setButlerWorkspaceFixtures();
    mutateButlerEvent.mockImplementation((_payload, options) => {
      options?.onSuccess?.({
        data: {
          action: "dismiss",
          tool_name: "reminder_dismiss",
          request_id: "req-dismiss",
          result: { status: "dismissed" },
          projection_version: null,
          staleness_ms: null,
          projection_freshness: null,
        },
        meta: {},
      });
    });

    renderPage("/calendar?view=butler&range=list&anchor=2026-03-01");

    const dismissButton = document.querySelector(
      '[data-testid="butler-dismiss-button"]',
    ) as HTMLButtonElement;
    expect(dismissButton).toBeTruthy();
    await act(async () => {
      dismissButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(mutateButlerEvent).toHaveBeenCalled();
    const [body] = mutateButlerEvent.mock.calls.at(-1) ?? [];
    expect(body.action).toBe("dismiss");
    // reminder_dismiss targets the reminder id; no extra payload keys.
    expect(body.payload).toEqual({ event_id: "rem-1" });
    expect(toast.success).toHaveBeenCalledWith("Reminder dismissed");
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
              error_kind: "none",
              sync_enabled: true,
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
        data: {
          old_calendar_id: null,
          new_calendar_id: "work",
          persisted: false,
        },
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
      setPrimaryButton?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    expect(setPrimaryMutate).toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining("not persisted"),
    );
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
              error_kind: "none",
              sync_enabled: true,
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
        data: {
          old_calendar_id: null,
          new_calendar_id: "work",
          persisted: true,
        },
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
      setPrimaryButton?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    expect(toast.success).toHaveBeenCalledWith("Primary calendar updated");
    expect(toast.error).not.toHaveBeenCalled();
  });

  describe("detail panel", () => {
    it("renders provenance and sync state chip when entry has source_butler", async () => {
      setWorkspaceState({
        data: {
          data: {
            entries: [
              {
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
            entries: [
              {
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

    function setRecurringUserEntry(mutateAsync: ReturnType<typeof vi.fn>) {
      setUserMutationState({ mutateAsync } as Partial<UseUserMutationResult>);
      setWorkspaceState({
        data: {
          data: {
            entries: [
              {
                entry_id: "rec-detail-1",
                event_id: "evt-rec-detail-1",
                view: "user",
                source_type: "provider_event",
                source_key: "google:primary",
                title: "Weekly sync",
                start_at: "2026-03-03T09:00:00Z",
                end_at: "2026-03-03T09:30:00Z",
                timezone: "UTC",
                all_day: false,
                calendar_id: "primary",
                provider_event_id: "rec-evt",
                butler_name: "general",
                schedule_id: null,
                reminder_id: null,
                rrule: "RRULE:FREQ=WEEKLY;BYDAY=TU",
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
            next_cursor: null,
            has_more: false,
          },
          meta: {},
        },
      } as Partial<UseWorkspaceResult>);
    }

    async function openRecurringDetailEdit() {
      renderPage("/calendar?view=user&range=list&anchor=2026-03-01");
      const detailButton = findButton("Detail");
      await act(async () => {
        detailButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await flush();
      });
      const panel = container.querySelector(
        '[data-testid="entry-detail-panel"]',
      );
      const titleInput = panel?.querySelector(
        '[data-testid="detail-title-input"]',
      ) as HTMLInputElement;
      await act(async () => {
        setInputValue(titleInput, "Weekly sync (renamed)");
        await flush();
      });
      await act(async () => {
        titleInput.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
        await flush();
      });
    }

    it("editing a recurring event from the detail panel opens the scope sheet (this scope)", async () => {
      const mutateAsync = vi.fn().mockResolvedValue({
        data: {
          result: { status: "updated" },
          conflicts: [],
          suggested_slots: [],
        },
        meta: {},
      });
      setRecurringUserEntry(mutateAsync);
      await openRecurringDetailEdit();

      // The blur defers to the scope sheet instead of committing immediately.
      expect(mutateAsync).not.toHaveBeenCalled();
      const scopeSheet = document.querySelector(
        '[data-testid="edit-recurrence-scope"]',
      );
      expect(scopeSheet).not.toBeNull();

      // Default scope is "this"; confirm passes recurrence_scope + instance_start_at.
      const editDialog = findDialogByTitle("Edit recurring event");
      const saveButton = Array.from(
        editDialog?.querySelectorAll("button") ?? [],
      ).find(
        (button) => button.textContent?.trim() === "Save changes",
      ) as HTMLButtonElement;
      await act(async () => {
        saveButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await flush();
      });

      expect(mutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({
          action: "update",
          payload: expect.objectContaining({
            event_id: "rec-evt",
            title: "Weekly sync (renamed)",
            recurrence_scope: "this",
            instance_start_at: "2026-03-03T09:00:00Z",
          }),
        }),
      );
    });

    it("editing a recurring event with the series scope omits recurrence_scope", async () => {
      const mutateAsync = vi.fn().mockResolvedValue({
        data: {
          result: { status: "updated" },
          conflicts: [],
          suggested_slots: [],
        },
        meta: {},
      });
      setRecurringUserEntry(mutateAsync);
      await openRecurringDetailEdit();

      const seriesRadio = document.querySelector(
        '[data-testid="edit-scope-series"] input',
      ) as HTMLInputElement;
      await act(async () => {
        seriesRadio.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await flush();
      });

      const editDialog = findDialogByTitle("Edit recurring event");
      const saveButton = Array.from(
        editDialog?.querySelectorAll("button") ?? [],
      ).find(
        (button) => button.textContent?.trim() === "Save changes",
      ) as HTMLButtonElement;
      await act(async () => {
        saveButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await flush();
      });

      expect(mutateAsync).toHaveBeenCalledTimes(1);
      const payload = mutateAsync.mock.calls[0][0].payload;
      expect(payload).toMatchObject({
        event_id: "rec-evt",
        title: "Weekly sync (renamed)",
      });
      expect(payload).not.toHaveProperty("recurrence_scope");
      expect(payload).not.toHaveProperty("instance_start_at");
    });
  });

  describe("sync recovery cockpit", () => {
    function setSourceMeta(errorKind: string, lastError: string | null) {
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
                last_success_at: lastError ? null : "2026-03-01T10:00:00Z",
                last_error_at: lastError ? "2026-03-01T10:05:00Z" : null,
                last_error: lastError,
                full_sync_required: false,
                sync_state: lastError ? "failed" : "fresh",
                staleness_ms: 900,
                error_kind: errorKind,
                sync_enabled: true,
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
    }

    async function openSourcesDialog() {
      renderPage("/calendar?view=user&range=week&anchor=2026-03-01");
      await act(async () => {
        const configureButton = document.querySelector(
          'button[aria-label="Configure sources"]',
        ) as HTMLButtonElement;
        configureButton.dispatchEvent(
          new MouseEvent("click", { bubbles: true }),
        );
        await flush();
      });
    }

    it("Recover button triggers a full re-sync (full=true)", async () => {
      setButlerWorkspaceFixtures();
      setSourceMeta("none", null);
      const syncMutateAsync = vi.fn().mockResolvedValue({
        data: {
          scope: "source",
          requested_source_key: "google:work",
          requested_source_id: null,
          full: true,
          targets: [
            {
              butler_name: "general",
              source_key: "google:work",
              calendar_id: "work",
              status: "sync_completed",
              detail: null,
              error: null,
              recovery: true,
            },
          ],
          triggered_count: 1,
        },
        meta: {},
      });
      setSyncState({ mutateAsync: syncMutateAsync });

      await openSourcesDialog();

      const recoverButton = findButton("Recover");
      expect(recoverButton).toBeDefined();
      await act(async () => {
        recoverButton?.dispatchEvent(
          new MouseEvent("click", { bubbles: true }),
        );
        await flush();
      });

      expect(syncMutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({ source_key: "google:work", full: true }),
      );
    });

    it("shows a Reconnect CTA for a token-expired source", async () => {
      setButlerWorkspaceFixtures();
      setSourceMeta("token_expired", "sync token expired (410 Gone)");
      setSyncState();

      await openSourcesDialog();

      const reconnect = Array.from(document.querySelectorAll("a")).find(
        (anchor) => anchor.textContent?.trim() === "Reconnect",
      );
      expect(reconnect).toBeDefined();
    });

    it("hides the Reconnect CTA for a healthy source", async () => {
      setButlerWorkspaceFixtures();
      setSourceMeta("none", null);
      setSyncState();

      await openSourcesDialog();

      const reconnect = Array.from(document.querySelectorAll("a")).find(
        (anchor) => anchor.textContent?.trim() === "Reconnect",
      );
      expect(reconnect).toBeUndefined();
    });

    it("toggling a source persists the change via POST /api/calendar/sources", async () => {
      setButlerWorkspaceFixtures();
      setSourceMeta("none", null);
      setSyncState();
      const toggleMutate = vi.fn();
      vi.mocked(useToggleCalendarSource).mockReturnValue({
        mutate: toggleMutate,
        isPending: false,
      } as unknown as ReturnType<typeof useToggleCalendarSource>);

      await openSourcesDialog();

      const checkbox = document.querySelector(
        '[aria-label^="Toggle "]',
      ) as HTMLElement | null;
      expect(checkbox).not.toBeNull();
      await act(async () => {
        checkbox?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await flush();
      });

      expect(toggleMutate).toHaveBeenCalledWith(
        expect.objectContaining({
          butler: "general",
          source_key: "google:work",
          enabled: false,
        }),
        expect.anything(),
      );
    });

    it("Hide control removes a source from the read filter without toggling sync", async () => {
      setButlerWorkspaceFixtures();
      setSourceMeta("none", null);
      setSyncState();
      const toggleMutate = vi.fn();
      vi.mocked(useToggleCalendarSource).mockReturnValue({
        mutate: toggleMutate,
        isPending: false,
      } as unknown as ReturnType<typeof useToggleCalendarSource>);

      await openSourcesDialog();

      // Before hiding: no source filter is applied (all sources visible).
      expect(latestWorkspaceParams()?.sources).toBeUndefined();

      const hideButton = findButton("Hide");
      expect(hideButton).toBeDefined();
      await act(async () => {
        hideButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await flush();
      });

      // The read filter now excludes the hidden source...
      expect(latestWorkspaceParams()?.sources).toEqual([]);
      // ...without persisting any change to the sync toggle.
      expect(toggleMutate).not.toHaveBeenCalled();
      // The control flips to "Show" so it can be un-hidden.
      expect(findButton("Show")).toBeDefined();
      // The masthead reflects the view-only hidden count.
      expect(document.body.textContent).toContain("1 hidden");
    });

    it("disabling sync does not hide the source from the view", async () => {
      setButlerWorkspaceFixtures();
      setSourceMeta("none", null);
      setSyncState();
      vi.mocked(useToggleCalendarSource).mockReturnValue({
        mutate: vi.fn(),
        isPending: false,
      } as unknown as ReturnType<typeof useToggleCalendarSource>);

      await openSourcesDialog();

      const checkbox = document.querySelector(
        '[aria-label^="Toggle "]',
      ) as HTMLElement | null;
      expect(checkbox).not.toBeNull();
      await act(async () => {
        checkbox?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await flush();
      });

      // Toggling the persisted sync state must not affect the view read filter
      // nor the hidden count — the two concerns are decoupled.
      expect(latestWorkspaceParams()?.sources).toBeUndefined();
      expect(document.body.textContent).not.toContain("1 hidden");
    });
  });

  describe("recurring instance capping", () => {
    function makeRecurringEntries(
      scheduleId: string,
      count: number,
      dayPrefix: string = "2026-03-01",
    ) {
      return Array.from({ length: count }, (_, i) => ({
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
            next_cursor: null,
            has_more: false,
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
            next_cursor: null,
            has_more: false,
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
            next_cursor: null,
            has_more: false,
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

  describe("find-time degraded state", () => {
    it("renders an honest 'free/busy unavailable' state, not a fake empty result", async () => {
      const mutateAsync = vi.fn().mockResolvedValue({
        data: {
          slots: [],
          duration_minutes: 30,
          calendar_ids: [],
          available: false,
          reason: "Butler 'general' is unreachable",
        },
      });
      vi.mocked(useFindCalendarWorkspaceTime).mockReturnValue({
        mutateAsync,
        isPending: false,
        isError: false,
      } as unknown as ReturnType<typeof useFindCalendarWorkspaceTime>);

      renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

      // Open the Find-time panel (toggle button on the toolbar).
      await act(async () => {
        findButton("Find time")?.dispatchEvent(
          new MouseEvent("click", { bubbles: true }),
        );
        await flush();
      });

      // Submit the search form.
      const form = container
        .querySelector("#find-time-duration")
        ?.closest("form");
      expect(form).toBeTruthy();
      await act(async () => {
        form?.dispatchEvent(
          new Event("submit", { bubbles: true, cancelable: true }),
        );
        await flush();
      });

      expect(mutateAsync).toHaveBeenCalled();
      const unavailable = document.querySelector(
        '[data-testid="find-time-unavailable"]',
      );
      expect(unavailable).toBeTruthy();
      expect(unavailable?.textContent).toContain("Free/busy is unavailable");
      expect(unavailable?.textContent).toContain("unreachable");
      // The honest degraded state must NOT masquerade as "no open slots".
      expect(
        document.querySelector('[data-testid="find-time-slots"]'),
      ).toBeNull();
    });

    it("renders ranked slots when free/busy is available", async () => {
      const mutateAsync = vi.fn().mockResolvedValue({
        data: {
          slots: [
            {
              start_at: "2026-03-02T09:00:00Z",
              end_at: "2026-03-02T09:30:00Z",
              timezone: "UTC",
            },
          ],
          duration_minutes: 30,
          calendar_ids: ["primary"],
          available: true,
          reason: null,
        },
      });
      vi.mocked(useFindCalendarWorkspaceTime).mockReturnValue({
        mutateAsync,
        isPending: false,
        isError: false,
      } as unknown as ReturnType<typeof useFindCalendarWorkspaceTime>);

      renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

      await act(async () => {
        findButton("Find time")?.dispatchEvent(
          new MouseEvent("click", { bubbles: true }),
        );
        await flush();
      });

      const form = container
        .querySelector("#find-time-duration")
        ?.closest("form");
      await act(async () => {
        form?.dispatchEvent(
          new Event("submit", { bubbles: true, cancelable: true }),
        );
        await flush();
      });

      expect(
        document.querySelector('[data-testid="find-time-slots"]'),
      ).toBeTruthy();
      expect(
        document.querySelector('[data-testid="find-time-unavailable"]'),
      ).toBeNull();
    });
  });
});
