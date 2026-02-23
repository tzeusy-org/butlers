/**
 * TanStack Query hooks for backfill job APIs.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  cancelBackfillJob,
  createBackfillJob,
  getBackfillJob,
  getBackfillJobProgress,
  listBackfillJobs,
  listConnectors,
  pauseBackfillJob,
  resumeBackfillJob,
} from "@/api/index.ts";
import type { BackfillJobParams, CreateBackfillJobRequest } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Active statuses — used to drive polling intervals
// ---------------------------------------------------------------------------

const ACTIVE_STATUSES = new Set(["pending", "active"]);

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/** Fetch the paginated list of backfill jobs. */
export function useBackfillJobs(
  params?: BackfillJobParams,
  options?: { refetchInterval?: number | false },
) {
  return useQuery({
    queryKey: ["backfill-jobs", params],
    queryFn: () => listBackfillJobs(params),
    refetchInterval: options?.refetchInterval ?? 30_000,
  });
}

/** Fetch a single backfill job by id. */
export function useBackfillJob(jobId: string | null) {
  return useQuery({
    queryKey: ["backfill-job", jobId],
    queryFn: () => getBackfillJob(jobId!),
    enabled: !!jobId,
    refetchInterval: 30_000,
  });
}

/**
 * Poll a single backfill job's progress.
 *
 * Polls frequently when the job is active or pending, falls back to
 * a slow interval otherwise.
 */
export function useBackfillJobProgress(jobId: string | null, currentStatus?: string) {
  const isActive = currentStatus ? ACTIVE_STATUSES.has(currentStatus) : false;

  return useQuery({
    queryKey: ["backfill-job-progress", jobId],
    queryFn: () => getBackfillJobProgress(jobId!),
    enabled: !!jobId,
    // Poll every 5 s when active, every 30 s when idle
    refetchInterval: isActive ? 5_000 : 30_000,
  });
}

/** List connectors for the create-job form's connector selector. */
export function useConnectors() {
  return useQuery({
    queryKey: ["connectors"],
    queryFn: () => listConnectors(),
    refetchInterval: 60_000,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/** Create a new backfill job. */
export function useCreateBackfillJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateBackfillJobRequest) => createBackfillJob(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["backfill-jobs"] });
    },
  });
}

/** Pause a backfill job. */
export function usePauseBackfillJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => pauseBackfillJob(jobId),
    onSuccess: (_data, jobId) => {
      queryClient.invalidateQueries({ queryKey: ["backfill-jobs"] });
      queryClient.invalidateQueries({ queryKey: ["backfill-job", jobId] });
      queryClient.invalidateQueries({ queryKey: ["backfill-job-progress", jobId] });
    },
  });
}

/** Cancel a backfill job. */
export function useCancelBackfillJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => cancelBackfillJob(jobId),
    onSuccess: (_data, jobId) => {
      queryClient.invalidateQueries({ queryKey: ["backfill-jobs"] });
      queryClient.invalidateQueries({ queryKey: ["backfill-job", jobId] });
      queryClient.invalidateQueries({ queryKey: ["backfill-job-progress", jobId] });
    },
  });
}

/** Resume a paused backfill job. */
export function useResumeBackfillJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => resumeBackfillJob(jobId),
    onSuccess: (_data, jobId) => {
      queryClient.invalidateQueries({ queryKey: ["backfill-jobs"] });
      queryClient.invalidateQueries({ queryKey: ["backfill-job", jobId] });
      queryClient.invalidateQueries({ queryKey: ["backfill-job-progress", jobId] });
    },
  });
}
