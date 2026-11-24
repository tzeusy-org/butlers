/**
 * Default hook return values for the CalendarWorkspacePage test files. Every
 * setter re-seeds one mocked hook from `@/hooks/use-calendar-workspace`; the
 * harness calls them all in `beforeEach` because `vi.resetAllMocks()` wipes the
 * `vi.mock` factory defaults.
 */

import { vi } from "vitest";

import {
  useCalendarOverlays,
  useCalendarWorkspace,
  useCalendarWorkspaceEntry,
  useCalendarWorkspaceMeta,
  useCalendarWorkspaceSearch,
  useMutateCalendarWorkspaceButlerEvent,
  useMutateCalendarWorkspaceUserEvent,
  usePreviewCalendarWorkspaceButlerEvent,
  useSetPrimaryCalendar,
  useSyncCalendarWorkspace,
} from "@/hooks/use-calendar-workspace";

export type UseWorkspaceResult = ReturnType<typeof useCalendarWorkspace>;
export type UseWorkspaceMetaResult = ReturnType<typeof useCalendarWorkspaceMeta>;
export type UseOverlaysResult = ReturnType<typeof useCalendarOverlays>;
export type UseWorkspaceSearchResult = ReturnType<typeof useCalendarWorkspaceSearch>;
export type UseButlerMutationResult = ReturnType<
  typeof useMutateCalendarWorkspaceButlerEvent
>;
export type UseSyncResult = ReturnType<typeof useSyncCalendarWorkspace>;
export type UseUserMutationResult = ReturnType<
  typeof useMutateCalendarWorkspaceUserEvent
>;

export const mutateButlerEvent = vi.fn();
export const mutateUserEvent = vi.fn();

export function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

export function setInputValue(input: HTMLInputElement, value: string) {
  const prototype = window.HTMLInputElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
  descriptor?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

export function setWorkspaceState(state?: Partial<UseWorkspaceResult>) {
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
            sync_applicable: true,
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

export function setWorkspaceMetaState(
  state?: Partial<UseWorkspaceMetaResult>,
  opts?: { defaultTimezone?: string },
) {
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
            sync_applicable: true,
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
            sync_applicable: true,
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
        default_timezone: opts?.defaultTimezone ?? "UTC",
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

export function setButlerMutationState(state?: Partial<UseButlerMutationResult>) {
  vi.mocked(useMutateCalendarWorkspaceButlerEvent).mockReturnValue({
    mutate: mutateButlerEvent,
    isPending: false,
    ...state,
  } as UseButlerMutationResult);
}

export type UsePreviewResult = ReturnType<
  typeof usePreviewCalendarWorkspaceButlerEvent
>;
export const previewMutate = vi.fn();

export function setRecurrencePreviewState(
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

export function setSyncState(state?: Partial<UseSyncResult>) {
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

export function setUserMutationState(state?: Partial<UseUserMutationResult>) {
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

export type UseEntryDetailResult = ReturnType<typeof useCalendarWorkspaceEntry>;

// Default: no fresh copy yet (data: null) → the panel falls back to the grid row.
// vi.resetAllMocks() in beforeEach wipes the vi.mock factory default, and the
// detail panel now always calls this hook, so it must be re-seeded every test.
export function setEntryDetailState(state?: Partial<UseEntryDetailResult>) {
  vi.mocked(useCalendarWorkspaceEntry).mockReturnValue({
    isLoading: false,
    isError: false,
    error: null,
    data: null,
    refetch: vi.fn(),
    ...state,
  } as unknown as UseEntryDetailResult);
}

export type UsePrimaryMutationResult = ReturnType<typeof useSetPrimaryCalendar>;

export function setPrimaryCalendarState(state?: Partial<UsePrimaryMutationResult>) {
  vi.mocked(useSetPrimaryCalendar).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
    ...state,
  } as unknown as UsePrimaryMutationResult);
}
