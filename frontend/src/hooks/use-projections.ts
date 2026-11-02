/** TanStack Query hooks for ambient projection grants (/api/projections). */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { listProjections, revokeProjection, rotateProjection } from "@/api/projections.ts";

const KEY = ["projections"] as const;
const PROJECTIONS_POLL_MS = 30_000;

/** List grants; the last-fetch column goes stale quickly, so poll gently. */
export function useProjections() {
  return useQuery({
    queryKey: KEY,
    queryFn: listProjections,
    staleTime: 15_000,
    refetchInterval: PROJECTIONS_POLL_MS,
  });
}

// Revoke and rotate are HONEST-PENDING: they cut a live surface off, so the UI
// waits for the server rather than guessing (use-optimistic-mutation.ts).
export function useRevokeProjection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => revokeProjection(id),
    onSettled: () => queryClient.invalidateQueries({ queryKey: KEY }),
  });
}

export function useRotateProjection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => rotateProjection(id),
    // The response carries the one-time token; keep it out of the MutationCache.
    gcTime: 0,
    onSettled: () => queryClient.invalidateQueries({ queryKey: KEY }),
  });
}
