import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  getEducationCrossTopicAnalytics,
  getEducationFlows,
  getEducationMasterySummary,
  getEducationMindMap,
  getEducationMindMapAnalytics,
  getEducationMindMapAnalyticsTrend,
  getEducationMindMapFrontier,
  getEducationMindMapStrugglingNodes,
  getEducationMindMaps,
  getEducationPendingReviews,
  getEducationQuizResponses,
  requestEducationCurriculum,
  updateEducationMindMapStatus,
} from "@/api/index.ts";
import type {
  CurriculumRequestBody,
  MindMapListParams,
  QuizResponseParams,
} from "@/api/index.ts";

/** List mind maps with optional status filter and pagination. */
export function useMindMaps(params?: MindMapListParams) {
  return useQuery({
    queryKey: ["education", "mind-maps", params],
    queryFn: () => getEducationMindMaps(params),
    refetchInterval: 30_000,
  });
}

/** Get a single mind map with full DAG (nodes + edges). */
export function useMindMap(mindMapId: string | null) {
  return useQuery({
    queryKey: ["education", "mind-map", mindMapId],
    queryFn: () => getEducationMindMap(mindMapId!),
    enabled: !!mindMapId,
    refetchInterval: 30_000,
  });
}

/** Get frontier nodes for a mind map. */
export function useFrontierNodes(mindMapId: string | null) {
  return useQuery({
    queryKey: ["education", "frontier", mindMapId],
    queryFn: () => getEducationMindMapFrontier(mindMapId!),
    enabled: !!mindMapId,
    refetchInterval: 30_000,
  });
}

/** Get analytics snapshot with optional trend for a mind map. */
export function useMindMapAnalytics(mindMapId: string | null, trendDays?: number) {
  return useQuery({
    queryKey: ["education", "analytics", mindMapId, trendDays],
    queryFn: () => getEducationMindMapAnalytics(mindMapId!, trendDays),
    enabled: !!mindMapId,
    refetchInterval: 30_000,
  });
}

/** Get nodes pending (and upcoming) spaced-repetition review.
 *
 * Requests a 14-day horizon so the dashboard timeline can group entries into
 * Overdue / Today / This Week / Later buckets. The backend endpoint filters
 * by next_review_at <= now() + 14 days.
 */
export function usePendingReviews(mindMapId: string | null) {
  return useQuery({
    queryKey: ["education", "pending-reviews", mindMapId],
    queryFn: () => getEducationPendingReviews(mindMapId!, 14),
    enabled: !!mindMapId,
    refetchInterval: 15_000,
  });
}

/** Get aggregate mastery summary for a mind map. */
export function useMasterySummary(mindMapId: string | null) {
  return useQuery({
    queryKey: ["education", "mastery-summary", mindMapId],
    queryFn: () => getEducationMasterySummary(mindMapId!),
    enabled: !!mindMapId,
    refetchInterval: 30_000,
  });
}

/**
 * Compute a polling interval that backs off as the active map count grows.
 *
 * Active-tab polling cost on the Reviews tab is O(N) where N = active maps and
 * each map contributes 3 polled queries (pending reviews on a 15s base, mastery
 * + frontier on a 30s base). For small N (the common case) we keep the base
 * interval; once N grows past the inflection point we lengthen the interval so
 * total per-second request volume stays bounded.
 *
 * Formula: `max(baseMs, perMapMs * mapCount)`.
 *  - baseMs is the original interval and is preserved for low map counts.
 *  - perMapMs sets the inflection point: floor(baseMs / perMapMs) maps pass
 *    through unchanged; beyond that the interval scales linearly with N.
 *
 * Examples (15s base / 5s perMap):
 *  - 1-3 maps -> 15s
 *  - 5 maps   -> 25s
 *  - 10 maps  -> 50s
 *
 * Examples (30s base / 10s perMap):
 *  - 1-3 maps -> 30s
 *  - 5 maps   -> 50s
 *  - 10 maps  -> 100s
 *
 * Exported for unit testing.
 */
export function scaledPollInterval(
  baseMs: number,
  perMapMs: number,
  mapCount: number,
): number {
  if (mapCount <= 0) return baseMs;
  return Math.max(baseMs, perMapMs * mapCount);
}

/**
 * Fetch pending reviews for all provided map IDs in parallel.
 *
 * Uses `useQueries` so the number of queries tracks the live map list without
 * violating React's rules of hooks. Returns an array of query results in the
 * same order as `mapIds`.
 *
 * Polling interval scales with `mapIds.length` so per-map polling stays bounded
 * for residents with many active maps. See `scaledPollInterval`.
 */
export function useAllPendingReviews(mapIds: string[]) {
  const interval = scaledPollInterval(15_000, 5_000, mapIds.length);
  return useQueries({
    queries: mapIds.map((id) => ({
      queryKey: ["education", "pending-reviews", id],
      queryFn: () => getEducationPendingReviews(id, 14),
      refetchInterval: interval,
      refetchIntervalInBackground: false,
      // Match staleTime to the scaled polling cadence. Without this, the
      // global default staleTime (30s) is shorter than the scaled interval
      // for high map counts, so refetchOnWindowFocus / refetchOnMount would
      // fire extra requests between scheduled polls and partially undo the
      // backoff. Pinning staleTime=interval keeps the backoff effective
      // under focus/mount as well.
      staleTime: interval,
    })),
  });
}

/**
 * Fetch mastery summaries for all provided map IDs in parallel.
 *
 * Uses `useQueries` so the number of queries tracks the live map list without
 * violating React's rules of hooks. Returns an array of query results in the
 * same order as `mapIds`.
 *
 * Polling interval scales with `mapIds.length` so per-map polling stays bounded
 * for residents with many active maps. See `scaledPollInterval`.
 */
export function useAllMasterySummaries(mapIds: string[]) {
  const interval = scaledPollInterval(30_000, 10_000, mapIds.length);
  return useQueries({
    queries: mapIds.map((id) => ({
      queryKey: ["education", "mastery-summary", id],
      queryFn: () => getEducationMasterySummary(id),
      refetchInterval: interval,
      refetchIntervalInBackground: false,
      // See useAllPendingReviews: keep staleTime aligned with the scaled
      // refetchInterval so focus/mount refetches don't punch through the
      // backoff for residents with many active maps.
      staleTime: interval,
    })),
  });
}

/**
 * Fetch frontier nodes for all provided map IDs in parallel.
 *
 * Uses `useQueries` so the number of queries tracks the live map list without
 * violating React's rules of hooks. Returns an array of query results in the
 * same order as `mapIds`.
 *
 * Polling interval scales with `mapIds.length` so per-map polling stays bounded
 * for residents with many active maps. See `scaledPollInterval`.
 */
export function useAllFrontierNodes(mapIds: string[]) {
  const interval = scaledPollInterval(30_000, 10_000, mapIds.length);
  return useQueries({
    queries: mapIds.map((id) => ({
      queryKey: ["education", "frontier", id],
      queryFn: () => getEducationMindMapFrontier(id),
      refetchInterval: interval,
      refetchIntervalInBackground: false,
      // See useAllPendingReviews: keep staleTime aligned with the scaled
      // refetchInterval so focus/mount refetches don't punch through the
      // backoff for residents with many active maps.
      staleTime: interval,
    })),
  });
}

/** List quiz responses with optional filters and pagination. */
export function useQuizResponses(params?: QuizResponseParams) {
  return useQuery({
    queryKey: ["education", "quiz-responses", params],
    queryFn: () => getEducationQuizResponses(params),
    enabled: !!(params?.mind_map_id || params?.node_id),
    refetchInterval: 30_000,
  });
}

/** List teaching flows with optional status filter. */
export function useTeachingFlows(status?: string) {
  return useQuery({
    queryKey: ["education", "flows", status],
    queryFn: () => getEducationFlows(status),
    refetchInterval: 30_000,
  });
}

/** Get cross-topic comparative analytics. */
export function useCrossTopicAnalytics() {
  return useQuery({
    queryKey: ["education", "cross-topic"],
    queryFn: () => getEducationCrossTopicAnalytics(),
    refetchInterval: 30_000,
  });
}

/** Mutation: update a mind map's status. Invalidates mind-maps cache on success. */
export function useUpdateMindMapStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ mindMapId, status }: { mindMapId: string; status: string }) =>
      updateEducationMindMapStatus(mindMapId, status),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["education", "mind-maps"] });
      qc.invalidateQueries({ queryKey: ["education", "mind-map"] });
    },
    onError: (err) => {
      // Surface PUT failures instead of silently closing the dialog as if the
      // status change succeeded.
      toast.error(
        `Failed to update curriculum status: ${
          err instanceof Error ? err.message : "Unknown error"
        }`,
      );
    },
  });
}

/**
 * Fetch the analytics trend time-series for a single mind map.
 *
 * Wraps GET /api/education/mind-maps/{id}/analytics/trend?days={days}.
 * Snapshots are ordered oldest-first, suitable for a sparkline chart.
 *
 * The query is disabled when mindMapId is null or empty.
 */
export function useMindMapAnalyticsTrend(mindMapId: string | null, days: number = 7) {
  return useQuery({
    queryKey: ["education", "analytics-trend", mindMapId, days],
    queryFn: () => getEducationMindMapAnalyticsTrend(mindMapId!, days),
    enabled: !!mindMapId,
    refetchInterval: 60_000,
    // Align staleTime with the polling interval so window-focus/mount refetches
    // don't fire extra requests between poll cycles (same rationale as
    // useAllPendingReviews / useAllMasterySummaries).
    staleTime: 60_000,
  });
}

/**
 * Fetch struggling nodes for a single mind map.
 *
 * Wraps GET /api/education/mind-maps/{id}/struggling-nodes.
 * Returns nodes with declining or consistently low mastery scores.
 *
 * The query is disabled when mindMapId is null or empty.
 */
export function useMindMapStrugglingNodes(mindMapId: string | null) {
  return useQuery({
    queryKey: ["education", "struggling-nodes", mindMapId],
    queryFn: () => getEducationMindMapStrugglingNodes(mindMapId!),
    enabled: !!mindMapId,
    refetchInterval: 60_000,
    // Align staleTime with the polling interval (see useMindMapAnalyticsTrend).
    staleTime: 60_000,
  });
}

/** Mutation: request a new curriculum. Shows toast on success/conflict. */
export function useRequestCurriculum() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CurriculumRequestBody) => requestEducationCurriculum(body),
    onSuccess: () => {
      toast.success(
        "Curriculum requested. The butler will set it up within a few minutes and message you to begin",
      );
      qc.invalidateQueries({ queryKey: ["education", "mind-maps"] });
    },
    onError: (error: Error & { status?: number }) => {
      if (error.status === 409) {
        toast.error(
          "A curriculum request is already pending. Please wait for the butler to process it",
        );
      } else {
        toast.error("Failed to submit curriculum request");
      }
    },
  });
}
