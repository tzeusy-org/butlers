import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  approveAction,
  confirmAutonomySuggestion,
  createApprovalRule,
  createRuleFromAction,
  dismissAutonomySuggestion,
  expireStaleActions,
  getApprovalAction,
  getApprovalActions,
  getApprovalMetrics,
  getApprovalRule,
  getApprovalRules,
  getAutonomySuggestions,
  getExecutedActions,
  getRuleSuggestions,
  rejectAction,
  retryAction,
  revokeApprovalRule,
} from "@/api/index.ts";
import type {
  ApprovalActionApproveRequest,
  ApprovalActionParams,
  ApprovalActionRejectRequest,
  ApprovalRuleCreateRequest,
  ApprovalRuleFromActionRequest,
  ApprovalRuleParams,
  AutonomySuggestionDismissRequest,
  AutonomySuggestionParams,
} from "@/api/index.ts";

// Query keys
export const approvalKeys = {
  all: ["approvals"] as const,
  actions: (params?: ApprovalActionParams) => ["approvals", "actions", params] as const,
  action: (id: string) => ["approvals", "action", id] as const,
  executedActions: (params?: ApprovalActionParams) => ["approvals", "executed", params] as const,
  rules: (params?: ApprovalRuleParams) => ["approvals", "rules", params] as const,
  rule: (id: string) => ["approvals", "rule", id] as const,
  metrics: () => ["approvals", "metrics"] as const,
  suggestions: (actionId: string) => ["approvals", "suggestions", actionId] as const,
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

export function useApprovalAction(id: string) {
  return useQuery({
    queryKey: approvalKeys.action(id),
    queryFn: () => getApprovalAction(id),
    enabled: !!id,
  });
}

export function useExecutedActions(params?: ApprovalActionParams) {
  return useQuery({
    queryKey: approvalKeys.executedActions(params),
    queryFn: () => getExecutedActions(params),
  });
}

export function useApprovalRules(params?: ApprovalRuleParams) {
  return useQuery({
    queryKey: approvalKeys.rules(params),
    queryFn: () => getApprovalRules(params),
  });
}

export function useApprovalRule(id: string) {
  return useQuery({
    queryKey: approvalKeys.rule(id),
    queryFn: () => getApprovalRule(id),
    enabled: !!id,
  });
}

export function useApprovalMetrics() {
  return useQuery({
    queryKey: approvalKeys.metrics(),
    queryFn: () => getApprovalMetrics(),
  });
}

export function useRuleSuggestions(actionId: string) {
  return useQuery({
    queryKey: approvalKeys.suggestions(actionId),
    queryFn: () => getRuleSuggestions(actionId),
    enabled: !!actionId,
  });
}

// Mutations
export function useApproveAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ actionId, request }: { actionId: string; request: ApprovalActionApproveRequest }) =>
      approveAction(actionId, request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: approvalKeys.all });
    },
  });
}

export function useRejectAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ actionId, request }: { actionId: string; request: ApprovalActionRejectRequest }) =>
      rejectAction(actionId, request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: approvalKeys.all });
    },
  });
}

export function useRetryAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (actionId: string) => retryAction(actionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: approvalKeys.all });
    },
  });
}

export function useExpireStaleActions() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ butler, hours }: { butler?: string; hours?: number }) =>
      expireStaleActions(butler, hours),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: approvalKeys.all });
    },
  });
}

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

export function useCreateRuleFromAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: ApprovalRuleFromActionRequest) => createRuleFromAction(request),
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
