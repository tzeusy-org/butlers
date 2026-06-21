/**
 * TanStack Query hooks for calendar workspace APIs.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getCalendarAccounts,
  getCalendarWorkspace,
  getCalendarWorkspaceAudit,
  getCalendarWorkspaceEntry,
  getCalendarWorkspaceMeta,
  mutateCalendarWorkspaceButlerEvent,
  mutateCalendarWorkspaceUserEvent,
  searchCalendarWorkspace,
  setPrimaryCalendar,
  syncCalendarWorkspace,
  toggleCalendarSource,
} from "@/api/index.ts";
import type {
  ApiResponse,
  CalendarAuditParams,
  CalendarSourceToggleRequest,
  CalendarWorkspaceButlerMutationRequest,
  CalendarWorkspaceParams,
  CalendarWorkspaceReadResponse,
  CalendarWorkspaceSearchParams,
  CalendarWorkspaceSyncRequest,
  CalendarWorkspaceUserMutationRequest,
  SetPrimaryCalendarRequest,
} from "@/api/types.ts";

interface CalendarWorkspaceQueryOptions {
  refetchInterval?: number | false;
  enabled?: boolean;
}

/** Per-page size when walking the keyset cursor; bounded by the API (<=1000). */
const WORKSPACE_PAGE_SIZE = 500;
/** Safety cap on cursor follows so a runaway window can't loop forever. */
const WORKSPACE_MAX_PAGES = 20;

/**
 * Fetch the full workspace window by following the keyset `next_cursor` until
 * `has_more` is false, concatenating entries. The calendar grid renders a
 * bounded time window, so this keeps the view complete while consuming the
 * server's cursor-paginated contract.
 */
async function fetchAllWorkspacePages(
  params: CalendarWorkspaceParams,
): Promise<ApiResponse<CalendarWorkspaceReadResponse>> {
  const entries: CalendarWorkspaceReadResponse["entries"] = [];
  let cursor: string | undefined = params.cursor;
  let last: ApiResponse<CalendarWorkspaceReadResponse> | null = null;

  for (let page = 0; page < WORKSPACE_MAX_PAGES; page += 1) {
    const resp = await getCalendarWorkspace({
      ...params,
      limit: params.limit ?? WORKSPACE_PAGE_SIZE,
      cursor,
    });
    last = resp;
    entries.push(...resp.data.entries);
    if (!resp.data.has_more || !resp.data.next_cursor) break;
    cursor = resp.data.next_cursor;
  }

  const base = last as ApiResponse<CalendarWorkspaceReadResponse>;
  return {
    ...base,
    data: {
      ...base.data,
      entries,
      next_cursor: null,
      has_more: false,
    },
  };
}

/** Fetch normalized calendar workspace entries for the requested view/time range. */
export function useCalendarWorkspace(
  params: CalendarWorkspaceParams,
  options?: CalendarWorkspaceQueryOptions,
) {
  return useQuery({
    queryKey: ["calendar-workspace", params],
    queryFn: () => fetchAllWorkspacePages(params),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? 30_000,
  });
}

/** Full-text search calendar events; disabled until the query is non-blank. */
export function useCalendarWorkspaceSearch(
  params: CalendarWorkspaceSearchParams,
  options?: { enabled?: boolean },
) {
  const trimmed = params.q.trim();
  return useQuery({
    queryKey: ["calendar-workspace-search", { ...params, q: trimmed }],
    queryFn: () => searchCalendarWorkspace({ ...params, q: trimmed }),
    enabled: (options?.enabled ?? true) && trimmed.length > 0,
    staleTime: 10_000,
  });
}

/** Fetch calendar workspace metadata (sources, lanes, writable calendars). */
export function useCalendarWorkspaceMeta(options?: CalendarWorkspaceQueryOptions) {
  return useQuery({
    queryKey: ["calendar-workspace-meta"],
    queryFn: () => getCalendarWorkspaceMeta(),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? 60_000,
  });
}

/** Trigger projection/provider sync for all sources or a selected source. */
export function useSyncCalendarWorkspace() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (body: CalendarWorkspaceSyncRequest) => syncCalendarWorkspace(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar-workspace"] });
      queryClient.invalidateQueries({ queryKey: ["calendar-workspace-meta"] });
    },
  });
}

/** Mutate user-view provider events and refresh workspace data after success. */
export function useMutateCalendarWorkspaceUserEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CalendarWorkspaceUserMutationRequest) =>
      mutateCalendarWorkspaceUserEvent(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar-workspace"] });
      queryClient.invalidateQueries({ queryKey: ["calendar-workspace-meta"] });
    },
  });
}

/** Execute butler-lane workspace mutations and refresh query caches. */
export function useMutateCalendarWorkspaceButlerEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CalendarWorkspaceButlerMutationRequest) =>
      mutateCalendarWorkspaceButlerEvent(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar-workspace"] });
      queryClient.invalidateQueries({ queryKey: ["calendar-workspace-meta"] });
    },
  });
}

/** List connected Google accounts joined with calendar connector health. */
export function useCalendarAccounts(options?: CalendarWorkspaceQueryOptions) {
  return useQuery({
    queryKey: ["calendar-accounts"],
    queryFn: () => getCalendarAccounts(),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? 60_000,
  });
}

/** Enable/disable a calendar as a sync source and refresh workspace metadata. */
export function useToggleCalendarSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CalendarSourceToggleRequest) => toggleCalendarSource(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar-workspace"] });
      queryClient.invalidateQueries({ queryKey: ["calendar-workspace-meta"] });
      queryClient.invalidateQueries({ queryKey: ["calendar-accounts"] });
    },
  });
}

/** Set the primary calendar and refresh workspace metadata. */
export function useSetPrimaryCalendar() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SetPrimaryCalendarRequest) => setPrimaryCalendar(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar-workspace-meta"] });
    },
  });
}

interface CalendarAuditQueryOptions {
  refetchInterval?: number | false;
  enabled?: boolean;
}

/** Fetch a single calendar workspace entry by instance ID. */
export function useCalendarWorkspaceEntry(
  entryId: string | null,
  options?: { enabled?: boolean; timezone?: string },
) {
  return useQuery({
    queryKey: ["calendar-workspace-entry", entryId],
    queryFn: () => getCalendarWorkspaceEntry(entryId!, options?.timezone),
    enabled: options?.enabled ?? !!entryId,
  });
}

/** Fetch paginated calendar mutation audit log entries. */
export function useCalendarWorkspaceAudit(
  params?: CalendarAuditParams,
  options?: CalendarAuditQueryOptions,
) {
  return useQuery({
    queryKey: ["calendar-workspace-audit", params],
    queryFn: () => getCalendarWorkspaceAudit(params),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? 30_000,
  });
}
