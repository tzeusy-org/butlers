/**
 * Health Voice briefing hook for the Health Overview landing page.
 *
 * Wraps GET /api/health/briefing — the health-butler equivalent of the
 * dashboard briefing. Returns {greet, headline, elaboration, source,
 * state_class, generated_at}. source is exactly "llm" or "fallback".
 *
 * IMPORTANT — NO refetchInterval.
 *
 * LLM endpoints are excluded from the universal 30-second health-data
 * auto-refresh (spec: dashboard-domain-pages/spec.md §"Auto-refresh carve-out").
 * This hook deliberately omits refetchInterval; a fresh briefing is obtained
 * only on:
 *   1. Manual refresh via the BriefingStatus pill (onRefetch callback).
 *   2. Window focus after the 5-minute staleTime elapses.
 *
 * Auto-refresh would multiply LLM spawn cost on every Overview load.
 *
 * bu-sqjc7.4  -- Backend: GET /api/health/briefing
 * bu-w7b18.1  -- Frontend: Health Overview landing page
 */

import { useQuery } from "@tanstack/react-query";

import { getHealthBriefing } from "@/api/index.ts";
import type { Briefing } from "@/api/types.ts";

const FIVE_MINUTES_MS = 5 * 60 * 1000;

export const healthBriefingKeys = {
  all: ["health", "briefing"] as const,
};

/**
 * Fetch the health Voice briefing.
 *
 * Returns the full TanStack Query result so callers can access isFetching and
 * refetch for the BriefingStatus pill.
 *
 * No refetchInterval — manual refresh only (see module docstring).
 */
export function useHealthBriefing() {
  return useQuery<Briefing>({
    queryKey: healthBriefingKeys.all,
    queryFn: getHealthBriefing,
    staleTime: FIVE_MINUTES_MS,
    // refetchInterval intentionally omitted — LLM endpoint cost guard.
    refetchOnWindowFocus: true,
  });
}
