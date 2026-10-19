/**
 * TanStack Query hook for the Health personal baselines (usual ranges).
 */

import { useQuery } from "@tanstack/react-query";

import { getHealthBaselines } from "@/api/index.ts";

// Baselines are recomputed once a day; a five-minute poll is plenty.
const HEALTH_BASELINES_POLL_MS = 5 * 60_000;

/** Wraps GET /api/health/baselines. */
export function useHealthBaselines() {
  return useQuery({
    queryKey: ["health-baselines"],
    queryFn: getHealthBaselines,
    refetchInterval: HEALTH_BASELINES_POLL_MS,
  });
}
