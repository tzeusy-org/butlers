/**
 * TanStack Query hooks for butler analytics endpoints.
 * (bu-iuol4.16)
 *
 * Hooks:
 *   useButlerHourlyActivity  — GET /api/butlers/{name}/analytics/hourly-activity
 *   useButlerDailyActivity   — GET /api/butlers/{name}/analytics/daily-activity
 *   useButlerSessionKinds    — GET /api/butlers/{name}/analytics/session-kinds
 *   useButlerLatencyStats    — GET /api/butlers/{name}/analytics/latency-stats
 */

import { useQuery } from "@tanstack/react-query";

import {
  getButlerHourlyActivity,
  getButlerDailyActivity,
  getButlerSessionKinds,
  getButlerLatencyStats,
} from "@/api/index.ts";

// ---------------------------------------------------------------------------
// useButlerHourlyActivity
// ---------------------------------------------------------------------------

/**
 * Fetch hourly activity for a butler over a rolling window.
 *
 * @param butlerName  - Butler identifier
 * @param windowHours - Rolling window in hours (default 24, max 24)
 */
export function useButlerHourlyActivity(butlerName: string, windowHours: number = 24) {
  return useQuery({
    queryKey: ["butlers", butlerName, "analytics", "hourly-activity", windowHours],
    queryFn: () => getButlerHourlyActivity(butlerName, { window_hours: windowHours }),
    enabled: !!butlerName,
    staleTime: 60_000,
  });
}

// ---------------------------------------------------------------------------
// useButlerDailyActivity
// ---------------------------------------------------------------------------

/**
 * Fetch daily activity for a butler over a rolling window.
 *
 * @param butlerName - Butler identifier
 * @param windowDays - Rolling window in days; must be 7 or 30
 */
export function useButlerDailyActivity(butlerName: string, windowDays: 7 | 30 = 7) {
  return useQuery({
    queryKey: ["butlers", butlerName, "analytics", "daily-activity", windowDays],
    queryFn: () => getButlerDailyActivity(butlerName, { window_days: windowDays }),
    enabled: !!butlerName,
    staleTime: 60_000,
  });
}

// ---------------------------------------------------------------------------
// useButlerSessionKinds
// ---------------------------------------------------------------------------

/**
 * Fetch session count breakdown by trigger_source.
 *
 * @param butlerName - Butler identifier
 * @param windowDays - Rolling window in days (default 7)
 */
export function useButlerSessionKinds(butlerName: string, windowDays: number = 7) {
  return useQuery({
    queryKey: ["butlers", butlerName, "analytics", "session-kinds", windowDays],
    queryFn: () => getButlerSessionKinds(butlerName, { window_days: windowDays }),
    enabled: !!butlerName,
    staleTime: 60_000,
  });
}

// ---------------------------------------------------------------------------
// useButlerLatencyStats
// ---------------------------------------------------------------------------

export { type LatencyStats } from "@/api/index.ts";

/**
 * Fetch latency percentile statistics for a butler over a rolling window.
 *
 * @param butlerName - Butler identifier
 * @param windowDays - Rolling window in days (default 7)
 */
export function useButlerLatencyStats(butlerName: string, windowDays: number = 7) {
  const query = useQuery({
    queryKey: ["butlers", butlerName, "analytics", "latency-stats", windowDays],
    queryFn: () => getButlerLatencyStats(butlerName, { window_days: windowDays }),
    enabled: !!butlerName,
    staleTime: 60_000,
    select: (response) => response.data,
  });

  return query;
}
