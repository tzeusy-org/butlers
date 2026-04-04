/**
 * TanStack Query hooks for the QA Staffer dashboard API.
 *
 * All hooks use a 30s staleTime so the dashboard stays reasonably current
 * without hammering the API on every render.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  dismissQaKnownIssue,
  getHealingAttempt,
  getQaKnownIssues,
  getQaPatrol,
  getQaPatrolFindings,
  getQaPatrols,
  getQaSummary,
  listHealingAttempts,
  undismissQaKnownIssue,
} from "@/api/index.ts";
import type {
  HealingAttemptsParams,
  QaDismissRequest,
  QaKnownIssuesParams,
  QaPatrolsParams,
} from "@/api/index.ts";

const STALE_TIME = 30_000;

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

/** Fetch QA staffer summary (last patrol, 24h stats, all-time stats). */
export function useQaSummary() {
  return useQuery({
    queryKey: ["qa-summary"],
    queryFn: () => getQaSummary(),
    staleTime: STALE_TIME,
    refetchInterval: STALE_TIME,
  });
}

// ---------------------------------------------------------------------------
// Patrols
// ---------------------------------------------------------------------------

/** Fetch paginated patrol list with optional status filter. */
export function useQaPatrols(params?: QaPatrolsParams) {
  return useQuery({
    queryKey: ["qa-patrols", params],
    queryFn: () => getQaPatrols(params),
    staleTime: STALE_TIME,
    refetchInterval: STALE_TIME,
  });
}

/** Fetch a single patrol with nested findings. */
export function useQaPatrol(patrolId: string | undefined) {
  return useQuery({
    queryKey: ["qa-patrol", patrolId],
    queryFn: () => getQaPatrol(patrolId!),
    enabled: !!patrolId,
    staleTime: STALE_TIME,
  });
}

/** Fetch paginated findings for a specific patrol. */
export function useQaPatrolFindings(
  patrolId: string | undefined,
  params?: { source_type?: string; novel_only?: boolean; offset?: number; limit?: number },
) {
  return useQuery({
    queryKey: ["qa-patrol-findings", patrolId, params],
    queryFn: () => getQaPatrolFindings(patrolId!, params),
    enabled: !!patrolId,
    staleTime: STALE_TIME,
  });
}

// ---------------------------------------------------------------------------
// Known Issues
// ---------------------------------------------------------------------------

/** Fetch known issues grouped by fingerprint. */
export function useQaKnownIssues(
  params?: QaKnownIssuesParams,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ["qa-known-issues", params],
    queryFn: () => getQaKnownIssues(params),
    staleTime: STALE_TIME,
    refetchInterval: STALE_TIME,
    enabled: options?.enabled ?? true,
  });
}

// ---------------------------------------------------------------------------
// Dismiss / Undismiss mutations
// ---------------------------------------------------------------------------

/** Dismiss a known issue fingerprint. Invalidates known-issues cache on success. */
export function useDismissQaIssue() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      fingerprint,
      body,
    }: {
      fingerprint: string;
      body?: QaDismissRequest;
    }) => dismissQaKnownIssue(fingerprint, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["qa-known-issues"] });
      queryClient.invalidateQueries({ queryKey: ["qa-summary"] });
    },
  });
}

/** Un-dismiss a known issue fingerprint. Invalidates known-issues cache on success. */
export function useUndismissQaIssue() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (fingerprint: string) => undismissQaKnownIssue(fingerprint),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["qa-known-issues"] });
      queryClient.invalidateQueries({ queryKey: ["qa-summary"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Healing attempts (used for QA investigation detail)
// ---------------------------------------------------------------------------

/** Fetch a single healing attempt (QA investigation or self-healing). */
export function useHealingAttempt(attemptId: string | undefined) {
  return useQuery({
    queryKey: ["healing-attempt", attemptId],
    queryFn: () => getHealingAttempt(attemptId!),
    enabled: !!attemptId,
    staleTime: STALE_TIME,
  });
}

/** Fetch paginated healing attempts. */
export function useHealingAttempts(params?: HealingAttemptsParams) {
  return useQuery({
    queryKey: ["healing-attempts", params],
    queryFn: () => listHealingAttempts(params),
    staleTime: STALE_TIME,
    refetchInterval: STALE_TIME,
  });
}
