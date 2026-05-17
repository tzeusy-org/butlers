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
    refetchInterval: 30_000,
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
