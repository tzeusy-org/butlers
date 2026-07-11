/**
 * TanStack Query hook for the dashboard Decisions lane (bu-ckkpz.2).
 *
 * GET /api/decisions returns the full envelope (not just `.data`) so callers
 * can read `meta.decisions_available` -- see api/client.ts::getDecisions and
 * the fleet-wide degraded-envelope convention (CLAUDE.md "API Conventions").
 */

import { useQuery } from "@tanstack/react-query";

import { getDecisions } from "@/api/index.ts";

export function useDecisions() {
  return useQuery({
    queryKey: ["decisions"],
    queryFn: () => getDecisions(),
  });
}
