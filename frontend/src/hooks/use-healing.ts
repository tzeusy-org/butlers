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

import { useQuery } from "@tanstack/react-query";

import { getHealingDispatchEvents, type HealingDispatchEventsParams } from "@/api/index.ts";
import { useBusAwarePollInterval } from "@/hooks/use-bus-aware-poll-interval";

export function useHealingDispatchEvents(params: HealingDispatchEventsParams = {}) {
  const refetchInterval = useBusAwarePollInterval();
  return useQuery({
    queryKey: ["healing-dispatch-events", params],
    queryFn: () => getHealingDispatchEvents(params),
    refetchInterval,
  });
}

const SUPPRESSIONS_SAMPLE_LIMIT = 100;

/**
 * Per-fingerprint count of QA dispatches suppressed by an infra condition
 * (decision="infra_condition_open"). One fetch of the most recent events
 * covers every condition row the Standing Conditions panel renders, rather
 * than one request per row. `isError` must gate the caller's render -- a
 * failed fetch means "unknown", not "zero suppressed".
 */
export function useInfraConditionSuppressionCounts() {
  const query = useHealingDispatchEvents({
    decision: "infra_condition_open",
    limit: SUPPRESSIONS_SAMPLE_LIMIT,
  });
  const counts = new Map<string, number>();
  for (const event of query.data?.data ?? []) {
    counts.set(event.fingerprint, (counts.get(event.fingerprint) ?? 0) + 1);
  }
  return {
    counts,
    isLoading: query.isLoading,
    isError: query.isError,
  };
}
