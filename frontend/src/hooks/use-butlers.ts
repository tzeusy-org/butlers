/**
 * TanStack Query hooks for the butlers API.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  forceButlerTick,
  getButler,
  getButlerConfig,
  getButlerModules,
  getButlerSkills,
  getButlers,
  getRuntimeConfig,
  patchRuntimeConfig,
} from "@/api/index.ts";
import type { RuntimeConfigPatch } from "@/api/index.ts";

/** Fetch all butlers with live status. */
export function useButlers() {
  return useQuery({
    queryKey: ["butlers"],
    queryFn: () => getButlers(),
    refetchInterval: 30_000,
    staleTime: 30_000,
  });
}

/** Fetch a single butler by name. */
export function useButler(name: string) {
  return useQuery({
    queryKey: ["butlers", name],
    queryFn: () => getButler(name),
    enabled: !!name,
  });
}

/** Fetch configuration files for a specific butler. */
export function useButlerConfig(name: string) {
  return useQuery({
    queryKey: ["butlers", name, "config"],
    queryFn: () => getButlerConfig(name),
    enabled: !!name,
  });
}

/** Fetch per-module health status for a specific butler. */
export function useButlerModules(name: string) {
  return useQuery({
    queryKey: ["butlers", name, "modules"],
    queryFn: () => getButlerModules(name),
    enabled: !!name,
    refetchInterval: 30_000,
  });
}

/** Fetch skills available to a specific butler. */
export function useButlerSkills(name: string) {
  return useQuery({
    queryKey: ["butlers", name, "skills"],
    queryFn: () => getButlerSkills(name),
    enabled: !!name,
  });
}

/** Fetch runtime config for a butler from the DB-backed runtime_config table. */
export function useRuntimeConfig(name: string) {
  return useQuery({
    queryKey: ["butlers", name, "runtime-config"],
    queryFn: () => getRuntimeConfig(name),
    enabled: !!name,
  });
}

/** Mutation hook for patching runtime config. */
export function usePatchRuntimeConfig(name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (patch: RuntimeConfigPatch) => patchRuntimeConfig(name, patch),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["butlers", name, "runtime-config"],
      });
    },
  });
}

/**
 * Ping a butler to recheck reachability right now (JARVIS audit move 6,
 * bu-86c4c.15 — "ping butler" on the Issues feed).
 *
 * HONEST-PENDING, not optimistic: this is a genuine live MCP ping against
 * `GET /api/butlers/{name}` (the same live-status check the butler detail
 * page and board rely on), so the owner must wait for the real round trip
 * rather than see a faked-instant result. On settle it invalidates the
 * issues feed so a newly-reachable butler's "unreachable" issue clears (or a
 * still-unreachable one keeps showing) on the next read.
 */
export function usePingButler() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => getButler(name),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["issues"] });
    },
  });
}

/**
 * Force an immediate scheduler tick on a butler (JARVIS audit move 6,
 * bu-86c4c.15 — "run schedule now" on Issues, "trigger tick" on /system).
 *
 * HONEST-PENDING: dispatches a real MCP `tick` call that runs any due
 * schedules right now — not reversible, so no optimistic apply. Invalidates
 * the board (activity/session counts may change) and the issues feed
 * (a successful tick can resolve a "stale/overdue" signal) on settle.
 */
export function useForceButlerTick() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => forceButlerTick(name),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["butlers"] });
      void queryClient.invalidateQueries({ queryKey: ["issues"] });
    },
  });
}
