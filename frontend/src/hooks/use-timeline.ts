/**
 * TanStack Query hook for the unified timeline API.
 */

import { useQuery } from "@tanstack/react-query";

import { getTimeline, getTimelineAttention, getTimelineHistogram } from "@/api/index.ts";
import type {
  TimelineAttentionParams,
  TimelineHistogramParams,
  TimelineParams,
} from "@/api/types.ts";
import { useBusAwarePollInterval } from "@/hooks/use-bus-aware-poll-interval";

interface TimelineQueryOptions {
  refetchInterval?: number | false;
  enabled?: boolean;
}

/**
 * Fetch the unified timeline with cursor pagination and auto-refresh.
 *
 * Bus-covered (bu-qvnce.14 slice 3): session, notification, and ingestion
 * events all invalidate ["timeline"] (see event-cache-registry.ts) -- the
 * default interval below is a bus-aware reconciliation sweep (bu-01r64.3),
 * not the primary update path. Callers that pass their own refetchInterval
 * (e.g. an explicit head-poll for a live-tail view) are unaffected.
 */
export function useTimeline(params?: TimelineParams, options?: TimelineQueryOptions) {
  const busAwareInterval = useBusAwarePollInterval();
  return useQuery({
    queryKey: ["timeline", params],
    queryFn: () => getTimeline(params),
    refetchInterval: options?.refetchInterval ?? busAwareInterval,
    enabled: options?.enabled,
    // Never-blank list (JARVIS audit move 10): keep the previous cursor/filter
    // combination's rows visible while the new one fetches.
    placeholderData: params?.since ? undefined : (prev) => prev,
  });
}

export function useTimelineHistogram(params: TimelineHistogramParams, enabled = true) {
  const busAwareInterval = useBusAwarePollInterval();
  return useQuery({
    queryKey: ["timeline", "histogram", params],
    queryFn: () => getTimelineHistogram(params),
    refetchInterval: busAwareInterval,
    enabled,
  });
}

export function useTimelineAttention(params?: TimelineAttentionParams, enabled = true) {
  const busAwareInterval = useBusAwarePollInterval();
  return useQuery({
    queryKey: ["timeline", "attention", params],
    queryFn: () => getTimelineAttention(params),
    refetchInterval: busAwareInterval,
    enabled,
  });
}

/** Resolve a selected event independently of the ordinary 50-row Timeline head. */
export function useTimelineEvent(
  event: string | null,
  params?: Pick<TimelineParams, "butler" | "trace">,
  enabled = true,
) {
  const lookupParams = { ...params, event: event ?? undefined, limit: 1 };
  return useQuery({
    queryKey: ["timeline", "event", lookupParams],
    queryFn: () => getTimeline(lookupParams),
    enabled: enabled && event !== null,
  });
}
