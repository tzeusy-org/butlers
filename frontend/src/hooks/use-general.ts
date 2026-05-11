/**
 * TanStack Query hooks for the General butler and Switchboard APIs.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getEligibilityHistory,
  getGeneralCollections,
  getGeneralEntities,
  getGeneralStats,
  getRegistry,
  getRoutingLog,
  setButlerEligibility,
} from "@/api/index.ts";
import type {
  GeneralCollectionsParams,
  GeneralEntitiesParams,
  RoutingLogParams,
} from "@/api/index.ts";

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

/** Fetch eligibility history for a butler. */
export function useEligibilityHistory(name: string, hours = 24) {
  return useQuery({
    queryKey: ["eligibility-history", name, hours],
    queryFn: () => getEligibilityHistory(name, hours),
    refetchInterval: 60_000,
    enabled: !!name,
  });
}

/** Mutation to set a butler's eligibility state. */
export function useSetEligibility() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ name, state }: { name: string; state: string }) =>
      setButlerEligibility(name, state),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["switchboard-registry"] });
    },
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
