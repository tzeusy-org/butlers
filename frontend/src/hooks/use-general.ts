/**
 * TanStack Query hooks for the General butler and Switchboard APIs.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getEligibilityHistory,
  getRegistry,
  getRoutingLog,
  setButlerEligibility,
} from "@/api/index.ts";
import type { RoutingLogParams } from "@/api/index.ts";

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
