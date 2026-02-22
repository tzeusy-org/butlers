/**
 * TanStack Query hooks for calendar workspace APIs.
 */

import { useQuery } from "@tanstack/react-query";

import { getCalendarWorkspace, getCalendarWorkspaceMeta } from "@/api/index.ts";
import type { CalendarWorkspaceParams } from "@/api/types.ts";

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
