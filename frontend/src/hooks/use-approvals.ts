import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  confirmAutonomySuggestion,
  createApprovalRule,
  dismissAutonomySuggestion,
  getApprovalActions,
  getApprovalMetrics,
  getApprovalsFlat,
  getAutonomySuggestions,
  revokeApprovalRule,
} from "@/api/index.ts";
import type {
  ApprovalActionParams,
  ApprovalRuleCreateRequest,
  ApprovalRuleParams,
  AutonomySuggestionDismissRequest,
  AutonomySuggestionParams,
} from "@/api/index.ts";
import { POLL_BUS_RECONCILE_MS } from "@/lib/poll-policy";

// Query keys
export const approvalKeys = {
  all: ["approvals"] as const,
  actions: (params?: ApprovalActionParams) => ["approvals", "actions", params] as const,
  rules: (params?: ApprovalRuleParams) => ["approvals", "rules", params] as const,
  metrics: () => ["approvals", "metrics"] as const,
  autonomySuggestions: (params?: AutonomySuggestionParams) =>
    ["approvals", "autonomy-suggestions", params] as const,
};

// Queries
export function useApprovalActions(params?: ApprovalActionParams) {
  return useQuery({
    queryKey: approvalKeys.actions(params),
    queryFn: () => getApprovalActions(params),
  });
}

export function useApprovalMetrics() {
  return useQuery({
    queryKey: approvalKeys.metrics(),
    queryFn: () => getApprovalMetrics(),
    // Live path: the fleet event bus (bu-86c4c.8) invalidates this key on
    // every approval state-transition event. Polling is now a 5-minute
    // reconciliation sweep — a safety net, not the primary update path.
    refetchInterval: POLL_BUS_RECONCILE_MS,
    staleTime: 30_000,
  });
}

/**
 * Individual pending approvals ("waiting" state, Dispatch-language API),
 * newest-agnostic, capped at `limit`. Shares its `["approvals","flat",
 * "waiting", limit]` query key PREFIX with ApprovalsPage's own rail query
 * (any `limit`) -- useApprovalDecisionMutations' cache eviction matches by
 * prefix, so a decision made from either surface updates both (bu-86c4c.14).
 */
export function usePendingApprovalsFlat(limit: number) {
  return useQuery({
    queryKey: ["approvals", "flat", "waiting", limit] as const,
    queryFn: () => getApprovalsFlat("waiting", limit),
    staleTime: 30_000,
  });
}

// Mutations
export function useCreateRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: ApprovalRuleCreateRequest) => createApprovalRule(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: approvalKeys.rules() });
      queryClient.invalidateQueries({ queryKey: approvalKeys.metrics() });
    },
  });
}

export function useRevokeRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ruleId: string) => revokeApprovalRule(ruleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: approvalKeys.rules() });
      queryClient.invalidateQueries({ queryKey: approvalKeys.metrics() });
    },
  });
}

// Autonomy suggestions hooks

export function useAutonomySuggestions(params?: AutonomySuggestionParams) {
  return useQuery({
    queryKey: approvalKeys.autonomySuggestions(params),
    queryFn: () => getAutonomySuggestions(params),
  });
}

export function useConfirmAutonomySuggestion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (suggestionId: string) => confirmAutonomySuggestion(suggestionId),
    onSuccess: () => {
      // Invalidate by prefix to catch all autonomy-suggestions queries regardless of params.
      queryClient.invalidateQueries({ queryKey: ["approvals", "autonomy-suggestions"] });
      queryClient.invalidateQueries({ queryKey: approvalKeys.rules() });
      queryClient.invalidateQueries({ queryKey: approvalKeys.metrics() });
    },
  });
}

export function useDismissAutonomySuggestion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      suggestionId,
      request,
    }: {
      suggestionId: string;
      request?: AutonomySuggestionDismissRequest;
    }) => dismissAutonomySuggestion(suggestionId, request),
    onSuccess: () => {
      // Invalidate by prefix to catch all autonomy-suggestions queries regardless of params.
      queryClient.invalidateQueries({ queryKey: ["approvals", "autonomy-suggestions"] });
    },
  });
}
