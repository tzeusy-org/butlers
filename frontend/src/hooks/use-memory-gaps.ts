/** TanStack Query hooks for the owner's open knowledge gaps (bu-q7vx1q.73). */

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { dismissMemoryGap, getMemoryGapDemand, getMemoryGaps } from "@/api/memory-gaps.ts";
import type { MemoryGapStatus } from "@/api/memory-gaps.ts";

export const MEMORY_GAPS_PAGE_SIZE = 50;

/** Shared prefix so one invalidation refreshes the list and the demand summary together. */
export const memoryGapsKey = ["memory", "gaps"] as const;

/**
 * Keyset-paged gaps, newest first. No status means open plus answerable. An `entityId`
 * narrows the list server-side to that entity and is part of the cache key.
 */
export function useMemoryGaps(params?: {
  status?: MemoryGapStatus;
  limit?: number;
  entityId?: string;
}) {
  const limit = params?.limit ?? MEMORY_GAPS_PAGE_SIZE;
  const status = params?.status;
  const entityId = params?.entityId;
  return useInfiniteQuery({
    queryKey: [...memoryGapsKey, "list", status ?? "default", limit, entityId ?? "all"],
    queryFn: ({ pageParam, signal }) =>
      getMemoryGaps({ status, limit, entityId, cursor: pageParam, signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) =>
      lastPage.meta.has_more ? (lastPage.meta.next_cursor ?? undefined) : undefined,
  });
}

/** Open gaps and asks per predicate, aggregated server-side across every page. */
export function useMemoryGapDemand() {
  return useQuery({
    queryKey: [...memoryGapsKey, "demand"],
    queryFn: ({ signal }) => getMemoryGapDemand(signal),
  });
}

/**
 * Dismiss one gap. The list and demand are refetched on success AND on failure: a
 * 404/409 means the gap already left the open states, so the register must show its
 * current status rather than an optimistic ghost.
 */
export function useDismissMemoryGap() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (gapId: string) => dismissMemoryGap(gapId),
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: memoryGapsKey });
    },
  });
}
