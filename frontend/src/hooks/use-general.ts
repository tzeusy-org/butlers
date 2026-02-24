/**
 * TanStack Query hooks for the General butler and Switchboard APIs.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
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
