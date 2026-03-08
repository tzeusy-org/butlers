/**
 * TanStack Query hooks for unified ingestion rules CRUD (design.md D8).
 *
 * Separate from use-ingestion.ts which covers connector analytics.
 *
 * Query key strategy:
 * - ingestionRuleKeys.all                     -> broad invalidation anchor
 * - ingestionRuleKeys.list(params?)           -> list with optional filters
 * - ingestionRuleKeys.test()                  -> dry-run test results
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createIngestionRule,
  deleteIngestionRule,
  getIngestionRules,
  testIngestionRule,
  updateIngestionRule,
} from "@/api/index.ts";
import type {
  IngestionRuleCreate,
  IngestionRuleListParams,
  IngestionRuleTestRequest,
  IngestionRuleUpdate,
} from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Query key factory
// ---------------------------------------------------------------------------

export const ingestionRuleKeys = {
  all: ["ingestion-rules"] as const,
  list: (params?: IngestionRuleListParams) =>
    [...ingestionRuleKeys.all, "list", params] as const,
  test: () => [...ingestionRuleKeys.all, "test"] as const,
} as const;

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/** Fetch ingestion rules with optional scope/type/action/enabled filters. */
export function useIngestionRules(
  params?: IngestionRuleListParams,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionRuleKeys.list(params),
    queryFn: () => getIngestionRules(params),
    staleTime: 60_000,
    enabled: options?.enabled !== false,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/** Create a new ingestion rule. Invalidates the list cache on success. */
export function useCreateIngestionRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: IngestionRuleCreate) => createIngestionRule(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ingestionRuleKeys.all });
    },
  });
}

/** Partially update an ingestion rule. Invalidates the list cache on success. */
export function useUpdateIngestionRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: IngestionRuleUpdate }) =>
      updateIngestionRule(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ingestionRuleKeys.all });
    },
  });
}

/** Soft-delete an ingestion rule. Invalidates the list cache on success. */
export function useDeleteIngestionRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ruleId: string) => deleteIngestionRule(ruleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ingestionRuleKeys.all });
    },
  });
}

/** Dry-run test: evaluate a sample envelope against active ingestion rules. */
export function useTestIngestionRule() {
  return useMutation({
    mutationFn: (body: IngestionRuleTestRequest) => testIngestionRule(body),
  });
}
