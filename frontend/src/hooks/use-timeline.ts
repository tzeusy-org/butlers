/**
 * TanStack Query hook for the unified timeline API.
 */

import { useQuery } from "@tanstack/react-query";

import { getTimeline } from "@/api/index.ts";
import type { TimelineParams } from "@/api/types.ts";
import { useBusAwarePollInterval } from "@/hooks/use-bus-aware-poll-interval";

interface TimelineQueryOptions {
  refetchInterval?: number | false;
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
    // Never-blank list (JARVIS audit move 10): keep the previous cursor/filter
    // combination's rows visible while the new one fetches.
    placeholderData: (prev) => prev,
  });
}
