/**
 * TanStack Query hooks for the memory re-embedding API.
 *
 * Endpoints:
 *   GET  /api/memory/reembed/pending  — count stale embeddings per tier
 *   POST /api/memory/reembed          — trigger a synchronous re-embed run
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getReembedPending, runReembed } from "@/api/index.ts";
import type { ReembedRunRequest } from "@/api/types.ts";

/**
 * Primary poll interval for memory re-embed pending count queries (bu-ep4ks.15).
 * No fleet-bus event type covers this domain (see
 * event-cache-registry.ts's EVENT_CACHE_REGISTRY) -- this cadence IS
 * the update path, not a reconciliation sweep.
 */
const MEMORY_REEMBED_POLL_MS = 30_000;

/**
 * Fetch stale-embedding counts per tier.
 *
 * Polling-friendly: refetches every 30 s so the counts stay current without
 * manual refresh.  Pass `butler` to scope to a specific butler schema;
 * omit to let the backend pick the first available pool.
 */
export function useReembedPending(butler?: string) {
  return useQuery({
    queryKey: ["memory-reembed-pending", butler ?? null],
    queryFn: () => getReembedPending(butler),
    refetchInterval: MEMORY_REEMBED_POLL_MS,
  });
}

/**
 * Mutation hook for triggering a re-embed run (dry or live).
 *
 * Invalidates the pending-counts cache on success so the UI reflects the
 * updated row counts after a live run.
 */
export function useReembedRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ReembedRunRequest) => runReembed(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["memory-reembed-pending"] });
    },
  });
}
