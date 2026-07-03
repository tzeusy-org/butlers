/**
 * TanStack Query hook for the unified timeline API.
 */

import { useQuery } from "@tanstack/react-query";

import { getTimeline } from "@/api/index.ts";
import type { TimelineParams } from "@/api/types.ts";

interface TimelineQueryOptions {
  refetchInterval?: number | false;
}

/** Fetch the unified timeline with cursor pagination and auto-refresh. */
export function useTimeline(params?: TimelineParams, options?: TimelineQueryOptions) {
  return useQuery({
    queryKey: ["timeline", params],
    queryFn: () => getTimeline(params),
    refetchInterval: options?.refetchInterval ?? 30_000,
    // Never-blank list (JARVIS audit move 10): keep the previous cursor/filter
    // combination's rows visible while the new one fetches.
    placeholderData: (prev) => prev,
  });
}
