/**
 * TanStack Query hooks for the General butler and Switchboard APIs.
 */

import { useQuery } from "@tanstack/react-query";

import {
  getRegistry,
  getRoutingLog,
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
