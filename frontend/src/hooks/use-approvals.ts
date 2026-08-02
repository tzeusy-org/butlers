import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  confirmAutonomySuggestion,
  createApprovalRule,
  createApprovalRuleFromAction,
  dismissAutonomySuggestion,
  getApprovalActions,
  getApprovalMetrics,
  getApprovalsFlat,
  getAutonomySuggestions,
  revokeApprovalRule,
} from "@/api/index.ts";
import type {
  ApprovalActionParams,
  ApprovalMetricsResponse,
  ApprovalRuleCreateRequest,
  ApprovalRuleFromActionRequest,
  ApprovalRuleParams,
  AutonomySuggestionDismissRequest,
  AutonomySuggestionParams,
} from "@/api/index.ts";
import { useBusAwarePollInterval } from "@/hooks/use-bus-aware-poll-interval";

const NO_DEGRADED_SOURCES: string[] = [];
const APPROVAL_METRICS_SOURCE_FIELDS = [
  "pending_actions_sources_degraded",
  "approval_rules_sources_degraded",
  "sources_degraded",
] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * The metrics client is a compile-time cast around an HTTP payload. Only a
 * complete pending-actions envelope may authorize a zero-derived all-clear.
 */
export function isCompleteApprovalMetricsResponse(response: unknown): boolean {
  if (!isRecord(response)) return false;

  const data = response.data;
  const meta = response.meta;
  if (!isRecord(data) || !isRecord(meta)) return false;

  const totalPending = data.total_pending;
  if (typeof totalPending !== "number" || !Number.isSafeInteger(totalPending) || totalPending < 0) {
    return false;
  }

  return APPROVAL_METRICS_SOURCE_FIELDS.every((field) => {
    const value = meta[field];
    return value === undefined || (Array.isArray(value) && value.every((item) => typeof item === "string"));
  });
}

/**
 * Sources omitted from the pending-actions aggregate. A missing key means the
 * aggregate is complete; callers must still distinguish an absent response
 * (loading/error) from a truthful empty metric.
 */
export function pendingApprovalMetricSourcesDegraded(
  response: ApprovalMetricsResponse | undefined,
): string[] {
  return response?.meta?.pending_actions_sources_degraded ?? NO_DEGRADED_SOURCES;
}

/** Sources omitted from the independent active-rules aggregate. */
export function approvalRuleMetricSourcesDegraded(
  response: ApprovalMetricsResponse | undefined,
): string[] {
  return response?.meta?.approval_rules_sources_degraded ?? NO_DEGRADED_SOURCES;
}

// Query keys
export const approvalKeys = {
  all: ["approvals"] as const,
  actions: (params?: ApprovalActionParams) => ["approvals", "actions", params] as const,
  rules: (params?: ApprovalRuleParams) => ["approvals", "rules", params] as const,
  gatedTools: () => ["approvals", "gated-tools"] as const,
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
  // Live path: the fleet event bus (bu-86c4c.8) invalidates this key on
  // every approval state-transition event. Polling is a 5-minute
  // reconciliation sweep while the bus is connected, tightening to a fast
  // fallback while it's down (bu-01r64.3) — never the primary update path
  // either way.
  const refetchInterval = useBusAwarePollInterval();
  return useQuery({
    queryKey: approvalKeys.metrics(),
    queryFn: async () => {
      const response = await getApprovalMetrics();
      if (!isCompleteApprovalMetricsResponse(response)) {
        throw new Error("Approval metrics response is incomplete");
      }
      return response;
    },
    refetchInterval,
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
      queryClient.invalidateQueries({ queryKey: approvalKeys.gatedTools() });
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
      queryClient.invalidateQueries({ queryKey: approvalKeys.gatedTools() });
      queryClient.invalidateQueries({ queryKey: approvalKeys.metrics() });
    },
  });
}

/** Create the backend-derived rule for one already-approved action. */
export function useCreateRuleFromAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: ApprovalRuleFromActionRequest) =>
      createApprovalRuleFromAction(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: approvalKeys.rules() });
      queryClient.invalidateQueries({ queryKey: approvalKeys.gatedTools() });
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
      queryClient.invalidateQueries({ queryKey: approvalKeys.gatedTools() });
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
