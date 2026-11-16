/** TanStack Query hooks for the owner's break-through clauses. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { listReachClauses, revokeReachClause } from "@/api/reach.ts";

const KEY = ["reach-clauses"] as const;

export function useReachClauses() {
  return useQuery({ queryKey: KEY, queryFn: listReachClauses, staleTime: 15_000 });
}

// Revoke is HONEST-PENDING: it withdraws a standing interrupt right, so the UI waits for the
// server rather than guessing (use-optimistic-mutation.ts).
export function useRevokeReachClause() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => revokeReachClause(id),
    onSettled: () => queryClient.invalidateQueries({ queryKey: KEY }),
  });
}
