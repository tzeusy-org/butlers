/**
 * TanStack Query hooks for calendar workspace APIs.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getCalendarWorkspace,
  getCalendarWorkspaceAudit,
  getCalendarWorkspaceEntry,
  getCalendarWorkspaceMeta,
  mutateCalendarWorkspaceButlerEvent,
  mutateCalendarWorkspaceUserEvent,
  setPrimaryCalendar,
  syncCalendarWorkspace,
} from "@/api/index.ts";
import type {
  CalendarAuditParams,
  CalendarWorkspaceButlerMutationRequest,
  CalendarWorkspaceParams,
  CalendarWorkspaceSyncRequest,
  CalendarWorkspaceUserMutationRequest,
  SetPrimaryCalendarRequest,
} from "@/api/types.ts";

interface CalendarWorkspaceQueryOptions {
  refetchInterval?: number | false;
  enabled?: boolean;
}

/** Fetch normalized calendar workspace entries for the requested view/time range. */
export function useCalendarWorkspace(
  params: CalendarWorkspaceParams,
  options?: CalendarWorkspaceQueryOptions,
) {
  return useQuery({
    queryKey: ["calendar-workspace", params],
    queryFn: () => getCalendarWorkspace(params),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? 30_000,
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
