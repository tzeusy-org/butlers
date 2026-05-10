import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  getEducationCrossTopicAnalytics,
  getEducationFlows,
  getEducationMasterySummary,
  getEducationMindMap,
  getEducationMindMapAnalytics,
  getEducationMindMapFrontier,
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
 * Fetch pending reviews for all provided map IDs in parallel.
 *
 * Uses `useQueries` so the number of queries tracks the live map list without
 * violating React's rules of hooks. Returns an array of query results in the
 * same order as `mapIds`.
 */
export function useAllPendingReviews(mapIds: string[]) {
  return useQueries({
    queries: mapIds.map((id) => ({
      queryKey: ["education", "pending-reviews", id],
      queryFn: () => getEducationPendingReviews(id, 14),
      refetchInterval: 15_000,
      refetchIntervalInBackground: false,
    })),
  });
}

/**
 * Fetch mastery summaries for all provided map IDs in parallel.
 *
 * Uses `useQueries` so the number of queries tracks the live map list without
 * violating React's rules of hooks. Returns an array of query results in the
 * same order as `mapIds`.
 */
export function useAllMasterySummaries(mapIds: string[]) {
  return useQueries({
    queries: mapIds.map((id) => ({
      queryKey: ["education", "mastery-summary", id],
      queryFn: () => getEducationMasterySummary(id),
      refetchInterval: 30_000,
      refetchIntervalInBackground: false,
    })),
  });
}

/**
 * Fetch frontier nodes for all provided map IDs in parallel.
 *
 * Uses `useQueries` so the number of queries tracks the live map list without
 * violating React's rules of hooks. Returns an array of query results in the
 * same order as `mapIds`.
 */
export function useAllFrontierNodes(mapIds: string[]) {
  return useQueries({
    queries: mapIds.map((id) => ({
      queryKey: ["education", "frontier", id],
      queryFn: () => getEducationMindMapFrontier(id),
      refetchInterval: 30_000,
      refetchIntervalInBackground: false,
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
  });
}

/** Mutation: request a new curriculum. Shows toast on success/conflict. */
export function useRequestCurriculum() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CurriculumRequestBody) => requestEducationCurriculum(body),
    onSuccess: () => {
      toast.success("Curriculum requested — the butler will set it up shortly");
      qc.invalidateQueries({ queryKey: ["education", "mind-maps"] });
    },
    onError: (error: Error & { status?: number }) => {
      if (error.status === 409) {
        toast.error(
          "A curriculum request is already pending — please wait for the butler to process it",
        );
      } else {
        toast.error("Failed to submit curriculum request");
      }
    },
  });
}
