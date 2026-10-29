/**
 * TanStack Query hooks for GET /api/healing/dispatch-events (bu-ep4ks.3).
 *
 * Dispatch events record gate evaluations -- why a healing/QA-remediation
 * workflow was or was not launched. `useInfraConditionSuppressions` is the
 * specific slice the Standing Conditions panel uses: how many QA dispatches
 * an active standing condition has suppressed, joined on the same
 * `fingerprint` identity `public.infra_conditions` uses (Gate 5.5,
 * bu-27dxl.6.4) -- previously invisible over the API.
 */

import { useQueries, useQuery } from "@tanstack/react-query";

import { getHealingDispatchEvents, type HealingDispatchEventsParams } from "@/api/index.ts";

// No fleet event type covers healing gate evaluations, so this local poll is the primary path.
const HEALING_DISPATCH_EVENTS_POLL_MS = 30_000;

export function useHealingDispatchEvents(params: HealingDispatchEventsParams = {}) {
  return useQuery({
    queryKey: ["healing-dispatch-events", params],
    queryFn: () => getHealingDispatchEvents(params),
    refetchInterval: HEALING_DISPATCH_EVENTS_POLL_MS,
  });
}

/**
 * Per-fingerprint count of QA dispatches suppressed by an infra condition
 * (decision="infra_condition_open"). Fetch only one event per distinct
 * rendered infrastructure fingerprint: the filtered API's meta.total counts
 * every match, including events outside the latest global page. A missing or
 * failed total remains unknown, even when a failed refetch has cached data.
 */
export function useInfraConditionSuppressionCounts(fingerprints: readonly string[]) {
  const identities = [...new Set(fingerprints)].sort();
  const queries = useQueries({
    queries: identities.map((fingerprint) => ({
      queryKey: ["infra-condition-suppression-total", fingerprint],
      queryFn: async () => {
        const response = await getHealingDispatchEvents({
          decision: "infra_condition_open",
          fingerprint,
          limit: 1,
        });
        const total = response.meta?.total;
        if (!Number.isSafeInteger(total) || total < 0) {
          throw new Error("QA dispatch suppression total is unavailable");
        }
        return total;
      },
      refetchInterval: HEALING_DISPATCH_EVENTS_POLL_MS,
    })),
  });
  const counts = new Map<string, number>();
  queries.forEach((query, index) => {
    if (query.isSuccess) counts.set(identities[index], query.data);
  });
  return {
    counts,
    isLoading: queries.some((query) => query.isPending),
    isError: queries.some((query) => query.isError),
  };
}
