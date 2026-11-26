/**
 * `vi.mock` factories shared by the CalendarWorkspacePage test files. `vi.mock`
 * calls are hoisted per file, so each file keeps its own thin `vi.mock(...)`
 * that delegates here through a dynamic import.
 */

import { vi } from "vitest";

export function meetingDebriefsMock() {
  return {
    PENDING_DEBRIEFS_KEY: ["relationship", "meeting-debriefs", "pending"],
    usePendingMeetingDebriefs: vi.fn(() => ({
      isError: false,
      data: { debriefs: [], debriefs_available: true },
    })),
  };
}

const idleQuery = { isLoading: false, isError: false, error: null };

export function calendarWorkspaceHookMock() {
  return {
    useCalendarWorkspace: vi.fn(),
    useCalendarInvitations: vi.fn(),
    useCalendarOverlays: vi.fn(() => ({
      ...idleQuery,
      isFetched: false,
      data: { data: { entries: [], has_domain_context: false } },
    })),
    useCalendarDayBriefing: vi.fn(() => ({
      ...idleQuery,
      isFetched: false,
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
    useDayVerdict: vi.fn(() => ({
      ...idleQuery,
      isFetched: false,
      data: {
        data: {
          available: true,
          date: "2026-02-23",
          zone: "Asia/Singapore",
          relative: "tomorrow",
          freshness: { state: "current", as_of: null, sources_unreadable: [], sources_total: 1 },
          current: null,
          next: null,
          committed_minutes: 0,
          committed_count: 0,
          tentative_count: 0,
          free_window: null,
          empty: true,
        },
      },
    })),
    useCalendarMeetingPrep: vi.fn(() => ({
      ...idleQuery,
      isFetched: false,
      data: {
        data: {
          event_id: "00000000-0000-0000-0000-000000000000",
          has_prep_context: false,
          attendees: [],
          source_butlers: [],
        },
      },
    })),
    useCalendarWorkspaceSearch: vi.fn(() => ({
      ...idleQuery,
      isFetching: false,
      data: { data: { available: true, entries: [] } },
    })),
    useCalendarWorkspaceMeta: vi.fn(),
    useCalendarWorkspaceAudit: vi.fn(() => ({
      ...idleQuery,
      data: { data: { entries: [], total: 0, offset: 0, limit: 50 } },
    })),
    useCalendarWorkspaceEntry: vi.fn(() => ({ ...idleQuery, data: null })),
    useMutateCalendarWorkspaceButlerEvent: vi.fn(),
    useSyncCalendarWorkspace: vi.fn(),
    useMutateCalendarWorkspaceUserEvent: vi.fn(),
    useParseCalendarQuickAdd: vi.fn(() => ({
      mutateAsync: vi.fn(),
      isPending: false,
      reset: vi.fn(),
    })),
    usePreviewCalendarWorkspaceButlerEvent: vi.fn(),
    useSetPrimaryCalendar: vi.fn(),
    useSetCalendarSourceRole: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
    useCalendarAccounts: vi.fn(() => ({
      ...idleQuery,
      data: { data: { accounts: [], health_available: true } },
    })),
    useToggleCalendarSource: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
    useCalendarProposals: vi.fn(() => ({
      ...idleQuery,
      data: { data: { entries: [] } },
    })),
    useAcceptCalendarProposal: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
    useDismissCalendarProposal: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
    useCalendarChanges: vi.fn(() => ({
      ...idleQuery,
      data: {
        data: { changes: [], changes_available: true, truncated: false, since: "2026-07-01T00:00:00Z" },
      },
    })),
    useCalendarConflicts: vi.fn(() => ({
      ...idleQuery,
      data: {
        data: {
          issues: [],
          scan_window: { start: "2026-07-01T00:00:00Z", end: "2026-07-08T00:00:00Z" },
          issues_available: true,
        },
      },
    })),
  };
}
