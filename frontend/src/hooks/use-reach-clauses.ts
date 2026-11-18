/** TanStack Query hooks for the owner's break-through clauses. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  confirmReachProposal,
  dismissReachProposal,
  keepReachClause,
  listReachDecay,
  listReachClauses,
  listReachProposals,
  revokeReachClause,
} from "@/api/reach.ts";

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

const PROPOSALS_KEY = ["reach-proposals"] as const;

export function useReachProposals() {
  return useQuery({ queryKey: PROPOSALS_KEY, queryFn: listReachProposals, staleTime: 15_000 });
}

// Confirm creates a standing interrupt right, so it is HONEST-PENDING: no optimistic update. It
// refetches both lists on settle (success or failure), since a confirm can fail because the
// proposal expired or was already consumed.
export function useConfirmReachProposal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => confirmReachProposal(id),
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: PROPOSALS_KEY }),
        queryClient.invalidateQueries({ queryKey: KEY }),
      ]),
  });
}

export function useDismissReachProposal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => dismissReachProposal(id),
    onSettled: () => queryClient.invalidateQueries({ queryKey: PROPOSALS_KEY }),
  });
}

const DECAY_KEY = ["reach-decay"] as const;

export function useReachDecay() {
  return useQuery({ queryKey: DECAY_KEY, queryFn: listReachDecay, staleTime: 15_000 });
}

// Keep / revive re-arms a clause, so it is HONEST-PENDING (no optimistic update) and refetches
// the decay state and the clauses on settle.
export function useKeepReachClause() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => keepReachClause(id),
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: DECAY_KEY }),
        queryClient.invalidateQueries({ queryKey: KEY }),
      ]),
  });
}
