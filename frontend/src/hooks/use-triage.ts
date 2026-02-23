/**
 * TanStack Query hooks for triage rule and thread affinity APIs.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createTriageRule,
  deleteThreadAffinityOverride,
  deleteTriageRule,
  getThreadAffinitySettings,
  listThreadAffinityOverrides,
  listTriageRules,
  testTriageRule,
  updateThreadAffinitySettings,
  updateTriageRule,
  upsertThreadAffinityOverride,
} from "@/api/index.ts";
import type {
  ThreadAffinitySettingsUpdate,
  ThreadOverrideUpsert,
  TriageRuleCreate,
  TriageRuleListParams,
  TriageRuleTestRequest,
  TriageRuleUpdate,
} from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Query key families
// ---------------------------------------------------------------------------

export const triageKeys = {
  rules: (params?: TriageRuleListParams) => ["triage-rules", params] as const,
  ruleTest: () => ["triage-rule-test"] as const,
  threadAffinitySettings: () => ["thread-affinity-settings"] as const,
  threadAffinityOverrides: () => ["thread-affinity-overrides"] as const,
} as const;

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/** Fetch list of triage rules. Staleness: 60s per spec. */
export function useTriageRules(params?: TriageRuleListParams) {
  return useQuery({
    queryKey: triageKeys.rules(params),
    queryFn: () => listTriageRules(params),
    staleTime: 60_000,
  });
}

/** Fetch global thread-affinity settings. Staleness: 60s per spec. */
export function useThreadAffinitySettings() {
  return useQuery({
    queryKey: triageKeys.threadAffinitySettings(),
    queryFn: () => getThreadAffinitySettings(),
    staleTime: 60_000,
  });
}

/** Fetch per-thread affinity overrides. */
export function useThreadAffinityOverrides() {
  return useQuery({
    queryKey: triageKeys.threadAffinityOverrides(),
    queryFn: () => listThreadAffinityOverrides(),
    staleTime: 60_000,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/** Create a new triage rule. */
export function useCreateTriageRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: TriageRuleCreate) => createTriageRule(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["triage-rules"] });
    },
  });
}

/** Partially update a triage rule. */
export function useUpdateTriageRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: TriageRuleUpdate }) =>
      updateTriageRule(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["triage-rules"] });
    },
  });
}

/** Soft-delete a triage rule. */
export function useDeleteTriageRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ruleId: string) => deleteTriageRule(ruleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["triage-rules"] });
    },
  });
}

/** Dry-run test a triage rule against a sample envelope. */
export function useTestTriageRule() {
  return useMutation({
    mutationFn: (body: TriageRuleTestRequest) => testTriageRule(body),
  });
}

/** Update global thread-affinity settings. */
export function useUpdateThreadAffinitySettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ThreadAffinitySettingsUpdate) => updateThreadAffinitySettings(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: triageKeys.threadAffinitySettings() });
    },
  });
}

/** Upsert a per-thread affinity override. */
export function useUpsertThreadAffinityOverride() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ threadId, body }: { threadId: string; body: ThreadOverrideUpsert }) =>
      upsertThreadAffinityOverride(threadId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: triageKeys.threadAffinitySettings() });
      queryClient.invalidateQueries({ queryKey: triageKeys.threadAffinityOverrides() });
    },
  });
}

/** Delete a per-thread affinity override. */
export function useDeleteThreadAffinityOverride() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (threadId: string) => deleteThreadAffinityOverride(threadId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: triageKeys.threadAffinitySettings() });
      queryClient.invalidateQueries({ queryKey: triageKeys.threadAffinityOverrides() });
    },
  });
}
