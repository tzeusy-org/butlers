/**
 * Proactive insight candidates hook for the Health Overview landing page.
 *
 * Wraps GET /api/switchboard/insights?butler=health&status=pending — the
 * Switchboard read-only reader for public.insight_candidates.
 *
 * IMPORTANT — NO refetchInterval.
 *
 * The insight feed is excluded from the universal 30-second health-data
 * auto-refresh (spec: dashboard-domain-pages/spec.md §"Auto-refresh carve-out").
 * Candidates are produced by the scheduled insight-scan job; there is no
 * per-pageview cost, but auto-polling on every Overview load would be wasteful.
 * Refresh is triggered manually via the BriefingStatus pill.
 *
 * bu-sqjc7.3  -- Backend: GET /api/switchboard/insights reader
 * bu-w7b18.1  -- Frontend: Health Overview landing page
 */

import { useQuery } from "@tanstack/react-query";

import { getInsightCandidates } from "@/api/index.ts";
import type { InsightCandidate, InsightCandidatesParams } from "@/api/index.ts";

export const insightKeys = {
  all: (params?: InsightCandidatesParams) =>
    ["insights", params] as const,
};

/**
 * Fetch proactive insight candidates from the Switchboard.
 *
 * No refetchInterval — candidates are not real-time; manual refresh only
 * (see module docstring).
 *
 * @param params - Filter params (butler, status, limit). Defaults to
 *   status=pending with no butler filter if omitted.
 */
export function useInsights(params?: InsightCandidatesParams) {
  return useQuery<InsightCandidate[]>({
    queryKey: insightKeys.all(params),
    queryFn: () => getInsightCandidates(params),
    staleTime: 5 * 60 * 1000,
    // refetchInterval intentionally omitted — manual refresh via pill only.
  });
}
