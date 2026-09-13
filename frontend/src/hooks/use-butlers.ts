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
  getButlersBoard,
  getRuntimeConfig,
  patchRuntimeConfig,
} from "@/api/index.ts";
import type { RuntimeConfigPatch } from "@/api/index.ts";
import { useBusAwarePollInterval } from "@/hooks/use-bus-aware-poll-interval";

// Not bus-covered (bu-qvnce.14 slice 3): no fleet-bus event maps to the bare
// butlers list or a single butler's per-module health -- unlike
// ["butlers","board"] below, these stay a primary-path poll rather than a
// reconciliation sweep.
const BUTLERS_POLL_MS = 30_000;

/** Fetch all butlers with live status. */
export function useButlers() {
  return useQuery({
    queryKey: ["butlers"],
    queryFn: () => getButlers(),
    refetchInterval: BUTLERS_POLL_MS,
    staleTime: BUTLERS_POLL_MS,
  });
}

/**
 * Fetch GET /api/butlers/board -- the canonical, cadence-aware liveness
 * verdict (bu-qvnce.4). Deliberately thin: unlike useButlerStatusBoard (the
 * /butlers status board's own hook, use-butler-status-board.ts), this
 * returns the raw wire BoardRow[]/BoardAggregates shape rather than the
 * board page's camelCase presentation type -- components/overview/model.ts
 * (the Overview's pure derivation function) consumes BoardRow[] directly.
 *
 * Same queryKey the board page's hook uses, so react-query dedupes the
 * request across pages AND the event bus's session-patch
 * (event-cache-registry.ts, which already invalidates ["butlers","board"])
 * live-refreshes both consumers together.
 */
export function useButlersBoard() {
  // Bus-covered (bu-qvnce.14 slice 3): event-cache-registry.ts's
  // sessionPatch invalidates this exact key on every session started/ended
  // event -- this interval is a bus-aware reconciliation sweep (bu-01r64.3),
  // not the primary update path.
  const refetchInterval = useBusAwarePollInterval();
  return useQuery({
    queryKey: ["butlers", "board"],
    queryFn: () => getButlersBoard(),
    refetchInterval,
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
    refetchInterval: BUTLERS_POLL_MS,
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
