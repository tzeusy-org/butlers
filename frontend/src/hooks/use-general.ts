/**
 * TanStack Query hooks for the General butler and Switchboard APIs.
 */

import { useQuery } from "@tanstack/react-query";

import {
  getGeneralCollections,
  getGeneralEntities,
  getGeneralStats,
  getRegistry,
  getRoutingLog,
  setButlerEligibility,
} from "@/api/index.ts";
import type {
  ApiResponse,
  GeneralCollectionsParams,
  GeneralEntitiesParams,
  RegistryEntry,
  RoutingLogParams,
  SetEligibilityResponse,
} from "@/api/index.ts";
import {
  rollbackLists,
  snapshotAndUpdateQueries,
  type ListSnapshot,
  useOptimisticMutation,
} from "@/hooks/use-optimistic-mutation";

/** Fetch the switchboard routing log. */
export function useRoutingLog(params?: RoutingLogParams) {
  return useQuery({
    queryKey: ["switchboard-routing-log", params],
    queryFn: () => getRoutingLog(params),
    refetchInterval: 30_000,
  });
}

/** Fetch the switchboard butler registry. */
export function useRegistry() {
  return useQuery({
    queryKey: ["switchboard-registry"],
    queryFn: () => getRegistry(),
    refetchInterval: 30_000,
  });
}

/** Mutation to set a butler's eligibility state. */
export function useSetEligibility() {
  return useOptimisticMutation<
    ApiResponse<SetEligibilityResponse>,
    { name: string; state: string },
    ListSnapshot
  >({
    mutationFn: ({ name, state }: { name: string; state: string }) =>
      setButlerEligibility(name, state),
    cancelQueryKeys: [["switchboard-registry"]],
    applyOptimisticUpdate: ({ name, state }, queryClient) =>
      snapshotAndUpdateQueries<ApiResponse<RegistryEntry[]>>(
        queryClient,
        ["switchboard-registry"],
        (current) =>
          current
            ? {
                ...current,
                data: current.data.map((entry) =>
                  entry.name === name ? { ...entry, eligibility_state: state } : entry,
                ),
              }
            : current,
      ),
    rollback: (snapshot, queryClient) => rollbackLists(queryClient, snapshot),
    invalidateQueryKeys: [["switchboard-registry"]],
  });
}

// ---------------------------------------------------------------------------
// General butler — collections (bu-iuol4.30)
// ---------------------------------------------------------------------------

/** GET /api/general/stats — aggregated KPIs and size histogram. */
export function useGeneralStats() {
  return useQuery({
    queryKey: ["general-stats"],
    queryFn: () => getGeneralStats(),
    refetchInterval: 60_000,
  });
}

/** GET /api/general/collections — paginated collection list with entity counts. */
export function useGeneralCollections(params?: GeneralCollectionsParams) {
  return useQuery({
    queryKey: ["general-collections", params],
    queryFn: () => getGeneralCollections(params),
    refetchInterval: 60_000,
  });
}

/** GET /api/general/entities — search or list all entities. */
export function useGeneralEntities(params?: GeneralEntitiesParams) {
  return useQuery({
    queryKey: ["general-entities", params],
    queryFn: () => getGeneralEntities(params),
    refetchInterval: 60_000,
  });
}
