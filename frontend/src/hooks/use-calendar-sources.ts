/**
 * TanStack Query hooks for calendar sources: the connected-accounts list, the
 * sync-enabled toggle (optimistic) and the primary-calendar setter.
 * Re-exported from `use-calendar-workspace.ts`.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getCalendarAccounts, setPrimaryCalendar, toggleCalendarSource } from "@/api/index.ts";
import type {
  ApiResponse,
  CalendarSourceToggleRequest,
  CalendarSourceToggleResponse,
  CalendarWorkspaceMetaResponse,
  CalendarWorkspaceReadResponse,
  CalendarWorkspaceSourceFreshness,
  SetPrimaryCalendarRequest,
} from "@/api/types.ts";
import { useOptimisticMutation } from "@/hooks/use-optimistic-mutation";

interface CalendarAccountsQueryOptions {
  refetchInterval?: number | false;
  enabled?: boolean;
}

/**
 * Primary poll interval for the connected-accounts list (bu-ep4ks.15).
 * Unlike the workspace queries above, ["calendar-accounts"] is NOT one of
 * the keys calendarPatch invalidates (see event-cache-registry.ts) -- account
 * connect/disconnect is a settings action, not a projection update the bus
 * carries, so this stays on its own fixed poll rather than useBusAwarePollInterval.
 */
const CALENDAR_ACCOUNTS_POLL_MS = 60_000;

interface CalendarSourceSyncEnabledSnapshot {
  queryKey: readonly unknown[];
  sourceId: string;
  sourceKey: string;
  butlerName: string | null;
  syncEnabled: boolean;
}

interface CalendarSourceToggleSnapshot {
  metaSources: CalendarSourceSyncEnabledSnapshot[];
  workspaceSources: CalendarSourceSyncEnabledSnapshot[];
}

function calendarSourceMatchesToggle(
  source: CalendarWorkspaceSourceFreshness,
  body: CalendarSourceToggleRequest,
): boolean {
  return (
    source.butler_name === body.butler &&
    ((body.source_key != null && source.source_key === body.source_key) ||
      (body.source_id != null && source.source_id === body.source_id))
  );
}

function sourceSyncEnabledSnapshot(
  queryKey: readonly unknown[],
  source: CalendarWorkspaceSourceFreshness,
): CalendarSourceSyncEnabledSnapshot {
  return {
    queryKey,
    sourceId: source.source_id,
    sourceKey: source.source_key,
    butlerName: source.butler_name,
    syncEnabled: source.sync_enabled,
  };
}

function matchesSourceSnapshot(
  source: CalendarWorkspaceSourceFreshness,
  snapshot: CalendarSourceSyncEnabledSnapshot,
): boolean {
  return (
    source.source_id === snapshot.sourceId &&
    source.source_key === snapshot.sourceKey &&
    source.butler_name === snapshot.butlerName
  );
}

/** List connected Google accounts joined with calendar connector health. */
export function useCalendarAccounts(options?: CalendarAccountsQueryOptions) {
  return useQuery({
    queryKey: ["calendar-accounts"],
    queryFn: () => getCalendarAccounts(),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? CALENDAR_ACCOUNTS_POLL_MS,
  });
}

/** Enable/disable a calendar as a sync source and refresh workspace metadata. */
export function useToggleCalendarSource() {
  return useOptimisticMutation<
    ApiResponse<CalendarSourceToggleResponse>,
    CalendarSourceToggleRequest,
    CalendarSourceToggleSnapshot
  >({
    mutationFn: (body: CalendarSourceToggleRequest) => toggleCalendarSource(body),
    cancelQueryKeys: [
      ["calendar-workspace"],
      ["calendar-workspace-meta"],
      ["calendar-accounts"],
    ],
    applyOptimisticUpdate: (body, queryClient) => {
      const metaSources = queryClient
        .getQueriesData<ApiResponse<CalendarWorkspaceMetaResponse>>({
          queryKey: ["calendar-workspace-meta"],
        })
        .flatMap(([queryKey, current]) =>
          current
            ? current.data.connected_sources
                .filter((source) => calendarSourceMatchesToggle(source, body))
                .map((source) => sourceSyncEnabledSnapshot(queryKey, source))
            : [],
        );
      const workspaceSources = queryClient
        .getQueriesData<ApiResponse<CalendarWorkspaceReadResponse>>({
          queryKey: ["calendar-workspace"],
        })
        .flatMap(([queryKey, current]) =>
          current
            ? current.data.source_freshness
                .filter((source) => calendarSourceMatchesToggle(source, body))
                .map((source) => sourceSyncEnabledSnapshot(queryKey, source))
            : [],
        );

      queryClient.setQueriesData<ApiResponse<CalendarWorkspaceMetaResponse>>(
        { queryKey: ["calendar-workspace-meta"] },
        (current) =>
          current
            ? {
                ...current,
                data: {
                  ...current.data,
                  connected_sources: current.data.connected_sources.map((source) =>
                    calendarSourceMatchesToggle(source, body)
                      ? { ...source, sync_enabled: body.enabled }
                      : source,
                  ),
                },
              }
            : current,
      );
      queryClient.setQueriesData<ApiResponse<CalendarWorkspaceReadResponse>>(
        { queryKey: ["calendar-workspace"] },
        (current) =>
          current
            ? {
                ...current,
                data: {
                  ...current.data,
                  source_freshness: current.data.source_freshness.map((source) =>
                    calendarSourceMatchesToggle(source, body)
                      ? { ...source, sync_enabled: body.enabled }
                      : source,
                  ),
                },
              }
            : current,
      );
      return { metaSources, workspaceSources };
    },
    rollback: (snapshot, queryClient) => {
      snapshot.metaSources.forEach((sourceSnapshot) => {
        queryClient.setQueryData<ApiResponse<CalendarWorkspaceMetaResponse>>(
          sourceSnapshot.queryKey,
          (current) =>
            current
              ? {
                  ...current,
                  data: {
                    ...current.data,
                    connected_sources: current.data.connected_sources.map((source) =>
                      matchesSourceSnapshot(source, sourceSnapshot)
                        ? { ...source, sync_enabled: sourceSnapshot.syncEnabled }
                        : source,
                    ),
                  },
                }
              : current,
        );
      });
      snapshot.workspaceSources.forEach((sourceSnapshot) => {
        queryClient.setQueryData<ApiResponse<CalendarWorkspaceReadResponse>>(
          sourceSnapshot.queryKey,
          (current) =>
            current
              ? {
                  ...current,
                  data: {
                    ...current.data,
                    source_freshness: current.data.source_freshness.map((source) =>
                      matchesSourceSnapshot(source, sourceSnapshot)
                        ? { ...source, sync_enabled: sourceSnapshot.syncEnabled }
                        : source,
                    ),
                  },
                }
              : current,
        );
      });
    },
    invalidateQueryKeys: [
      ["calendar-workspace"],
      ["calendar-workspace-meta"],
      ["calendar-accounts"],
    ],
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
