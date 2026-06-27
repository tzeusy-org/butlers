/**
 * TanStack Query hooks for the QA Staffer dashboard API.
 *
 * All hooks use a 30s staleTime so the dashboard stays reasonably current
 * without hammering the API on every render.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  addQaAllowedRepo,
  deleteQaAllowedRepo,
  dismissQaKnownIssue,
  forceQaPatrol,
  getHealingAttempt,
  getQaAllowedRepos,
  getQaCase,
  getQaCaseJournal,
  getQaCases,
  getQaCircuitBreaker,
  getQaFindingByAttempt,
  getQaInvestigations,
  getQaRepoConfig,
  getQaKnownIssues,
  getQaPatrol,
  getQaPatrolFindings,
  getQaPatrols,
  getQaSummary,
  getQaTrends,
  listHealingAttempts,
  patchQaAllowedRepo,
  removeQaDismissal,
  resetQaCircuitBreaker,
  retryHealingAttempt,
  syncQaRepo,
  undismissQaKnownIssue,
  updateQaGitAuthor,
  updateQaRepoConfig,
} from "@/api/index.ts";
import type {
  HealingAttemptsParams,
  QaAllowedRepoCreate,
  QaCaseJournalParams,
  QaCasesParams,
  QaDismissRequest,
  QaGitAuthorUpdate,
  QaInvestigationsParams,
  QaKnownIssuesParams,
  QaPatrolsParams,
  QaRepoConfigUpdate,
} from "@/api/index.ts";

const STALE_TIME = 30_000;

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

/** Fetch QA staffer summary (last patrol, 24h stats, all-time stats). */
export function useQaSummary(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["qa-summary"],
    queryFn: () => getQaSummary(),
    staleTime: STALE_TIME,
    refetchInterval: STALE_TIME,
    enabled: options?.enabled ?? true,
  });
}

// ---------------------------------------------------------------------------
// Cases
// ---------------------------------------------------------------------------

/** Fetch paginated QA case summaries for the dossier dashboard. */
export function useQaCases(params?: QaCasesParams) {
  return useQuery({
    queryKey: ["qa-cases", params],
    queryFn: () => getQaCases(params),
    staleTime: STALE_TIME,
    refetchInterval: STALE_TIME,
  });
}

/** Fetch a single QA case dossier. */
export function useQaCase(caseId: string | undefined) {
  return useQuery({
    queryKey: ["qa-case", caseId],
    queryFn: () => getQaCase(caseId!),
    enabled: !!caseId,
    staleTime: STALE_TIME,
    refetchInterval: STALE_TIME,
  });
}

/** Fetch paginated journal events for a QA case. */
export function useQaCaseJournal(
  caseId: string | undefined,
  params?: Pick<QaCaseJournalParams, "cursor" | "limit">,
) {
  return useQuery({
    queryKey: ["qa-case-journal", caseId, params],
    queryFn: () => getQaCaseJournal(caseId!, params),
    enabled: !!caseId,
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

/** Remove an active dismissal from a case dossier. */
export function useRemoveDismissal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (fingerprint: string) => removeQaDismissal(fingerprint),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["qa-case"] });
      queryClient.invalidateQueries({ queryKey: ["qa-cases"] });
      queryClient.invalidateQueries({ queryKey: ["qa-known-issues"] });
      queryClient.invalidateQueries({ queryKey: ["qa-summary"] });
    },
  });
}

/**
 * Re-dispatch an investigation for a QA case (healing attempt).
 * Creates a new healing attempt for the same fingerprint.
 * Only valid when the case is in a terminal state (landed/escalated).
 */
export function useRetryHealingAttempt() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (attemptId: string) => retryHealingAttempt(attemptId),
    onSuccess: (_data, attemptId) => {
      queryClient.invalidateQueries({ queryKey: ["qa-case", attemptId] });
      queryClient.invalidateQueries({ queryKey: ["qa-cases"] });
      queryClient.invalidateQueries({ queryKey: ["qa-investigations"] });
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

/** Fetch the QA finding that dispatched a given healing attempt (404 → no finding). */
export function useQaFindingByAttempt(attemptId: string | undefined) {
  return useQuery({
    queryKey: ["qa-finding-by-attempt", attemptId],
    queryFn: () => getQaFindingByAttempt(attemptId!),
    enabled: !!attemptId,
    staleTime: STALE_TIME,
    retry: false,
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

// ---------------------------------------------------------------------------
// Investigation pipeline
// ---------------------------------------------------------------------------

/** Fetch paginated QA investigations (healing attempts by pipeline status). */
export function useQaInvestigations(params?: QaInvestigationsParams) {
  return useQuery({
    queryKey: ["qa-investigations", params],
    queryFn: () => getQaInvestigations(params),
    staleTime: STALE_TIME,
    refetchInterval: STALE_TIME,
  });
}

// ---------------------------------------------------------------------------
// Trends
// ---------------------------------------------------------------------------

/** Fetch 7-day QA trend data (success rate + source breakdown). */
export function useQaTrends(days = 7) {
  return useQuery({
    queryKey: ["qa-trends", days],
    queryFn: () => getQaTrends(days),
    staleTime: STALE_TIME,
    refetchInterval: STALE_TIME,
  });
}

// ---------------------------------------------------------------------------
// Circuit breaker
// ---------------------------------------------------------------------------

/** Fetch QA circuit breaker status. */
export function useQaCircuitBreaker() {
  return useQuery({
    queryKey: ["qa-circuit-breaker"],
    queryFn: () => getQaCircuitBreaker(),
    staleTime: STALE_TIME,
    refetchInterval: STALE_TIME,
  });
}

/** Reset the QA circuit breaker. Invalidates circuit-breaker + investigation caches. */
export function useResetQaCircuitBreaker() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => resetQaCircuitBreaker(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["qa-circuit-breaker"] });
      queryClient.invalidateQueries({ queryKey: ["qa-investigations"] });
      queryClient.invalidateQueries({ queryKey: ["qa-summary"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Repo config
// ---------------------------------------------------------------------------

/** Fetch QA repo configuration. */
export function useQaRepoConfig() {
  return useQuery({
    queryKey: ["qa-repo-config"],
    queryFn: () => getQaRepoConfig(),
    staleTime: STALE_TIME,
    refetchInterval: STALE_TIME,
  });
}

/** Update QA repo URL. Invalidates repo-config cache on success. */
export function useUpdateQaRepoConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: QaRepoConfigUpdate) => updateQaRepoConfig(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["qa-repo-config"] });
    },
  });
}

/** Trigger immediate repo sync. Invalidates repo-config cache on success. */
export function useSyncQaRepo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => syncQaRepo(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["qa-repo-config"] });
    },
  });
}

/**
 * Store the QA git author identity (name + email). Invalidates the QA summary
 * cache on success so the card's credentials status badges refresh.
 */
export function useUpdateQaGitAuthor() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: QaGitAuthorUpdate) => updateQaGitAuthor(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["qa-summary"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Force patrol mutation
// ---------------------------------------------------------------------------

/** Trigger an immediate patrol cycle. Invalidates relevant QA caches on success. */
export function useForceQaPatrol() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => forceQaPatrol(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["qa-summary"] });
      queryClient.invalidateQueries({ queryKey: ["qa-patrols"] });
      queryClient.invalidateQueries({ queryKey: ["qa-investigations"] });
      queryClient.invalidateQueries({ queryKey: ["qa-trends"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Allowed repositories
// ---------------------------------------------------------------------------

/** Fetch the QA allowed-repos whitelist. */
export function useQaAllowedRepos() {
  return useQuery({
    queryKey: ["qa-allowed-repos"],
    queryFn: () => getQaAllowedRepos(),
    staleTime: STALE_TIME,
  });
}

/** Add a repository to the QA whitelist. */
export function useAddQaAllowedRepo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: QaAllowedRepoCreate) => addQaAllowedRepo(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["qa-allowed-repos"] });
    },
  });
}

/** Toggle enabled on a whitelisted repository. */
export function usePatchQaAllowedRepo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ owner, repo, enabled }: { owner: string; repo: string; enabled: boolean }) =>
      patchQaAllowedRepo(owner, repo, { enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["qa-allowed-repos"] });
    },
  });
}

/** Remove a repository from the QA whitelist. */
export function useDeleteQaAllowedRepo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ owner, repo }: { owner: string; repo: string }) =>
      deleteQaAllowedRepo(owner, repo),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["qa-allowed-repos"] });
    },
  });
}
