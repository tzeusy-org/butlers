import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  confirmRulePromotionSuggestion,
  dismissRulePromotionSuggestion,
  getRulePromotionStats,
  getRulePromotionSuggestions,
  setRulePromotionRuleEnabled,
} from "@/api/index.ts";
import type { RulePromotionDismissRequest } from "@/api/index.ts";

// Query keys
export const rulePromotionKeys = {
  all: ["rule-promotions"] as const,
  surface: () => ["rule-promotions", "surface"] as const,
  stats: () => ["rule-promotions", "stats"] as const,
};

/** The rule-promotion approvals surface: pending owner-confirm cards + auto-applied info. */
export function useRulePromotions() {
  return useQuery({
    queryKey: rulePromotionKeys.surface(),
    queryFn: () => getRulePromotionSuggestions(),
  });
}

/** Aggregate rule-promotion metrics for the approvals stats tile (bead 6). */
export function useRulePromotionStats() {
  return useQuery({
    queryKey: rulePromotionKeys.stats(),
    queryFn: () => getRulePromotionStats(),
  });
}

export function useConfirmRulePromotion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (suggestionId: string) => confirmRulePromotionSuggestion(suggestionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: rulePromotionKeys.all });
      // A minted rule changes the ingestion-rules list.
      queryClient.invalidateQueries({ queryKey: ["ingestion-rules"] });
    },
  });
}

export function useDismissRulePromotion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      suggestionId,
      request,
    }: {
      suggestionId: string;
      request?: RulePromotionDismissRequest;
    }) => dismissRulePromotionSuggestion(suggestionId, request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: rulePromotionKeys.all });
    },
  });
}

/** Reversibly enable/disable the ingestion rule an auto-applied promotion minted. */
export function useSetRulePromotionRuleEnabled() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ suggestionId, enabled }: { suggestionId: string; enabled: boolean }) =>
      setRulePromotionRuleEnabled(suggestionId, enabled),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: rulePromotionKeys.all });
      queryClient.invalidateQueries({ queryKey: ["ingestion-rules"] });
    },
  });
}
