/** TanStack Query hooks for one decision set. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { decideDecisionSet, getDecisionSet } from "@/api/decision-sets.ts";
import type { DecisionSetPick, DecisionSetRead, DecisionSetVerdict } from "@/api/decision-sets.ts";

export const decisionSetKey = (setId: string) => ["decision-sets", setId] as const;

export function useDecisionSet(setId: string) {
  return useQuery<DecisionSetRead>({
    queryKey: decisionSetKey(setId),
    queryFn: () => getDecisionSet(setId),
    enabled: setId !== "",
    retry: false,
  });
}

export function useDecideDecisionSet(setId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: {
      version: number;
      decision: DecisionSetVerdict;
      selected: DecisionSetPick[];
    }) => decideDecisionSet(setId, request),
    // The picks carry item digests; do not keep them once the page lets go.
    gcTime: 0,
    // Success or refusal, the set may have changed: read the server's view again.
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: decisionSetKey(setId) });
    },
  });
}
