/**
 * Fetches the dashboard briefing for the editorial Overview surface.
 *
 * The briefing is composed server-side (templated greeting + classified
 * headline + LLM-elaborated paragraph with templated fallback) and cached
 * per-owner for 5 minutes. The hook mirrors that TTL and refetches on
 * window focus so the page stays honest after returning to the tab.
 *
 * Capability spec: openspec/changes/dashboard-overview-briefing/specs/
 *   dashboard-briefing/spec.md
 * Visual contract:  about/heart-and-soul/design-language.md (Editorial
 *   archetype, Voice surface, status pill).
 *
 * No consumer is wired in this change; the Overview page restructure that
 * consumes the hook is a follow-up change once the backend lands.
 */

import { useQuery } from "@tanstack/react-query";

import { getDashboardBriefing } from "@/api/client.ts";
import type { Briefing } from "@/api/types.ts";

const FIVE_MINUTES_MS = 5 * 60 * 1000;

export const briefingKeys = {
  all: ["dashboard", "briefing"] as const,
};

export function useBriefing() {
  return useQuery<Briefing>({
    queryKey: briefingKeys.all,
    queryFn: getDashboardBriefing,
    staleTime: FIVE_MINUTES_MS,
    refetchInterval: FIVE_MINUTES_MS,
    refetchOnWindowFocus: true,
  });
}
