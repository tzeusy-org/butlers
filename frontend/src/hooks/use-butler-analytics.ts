/**
 * TanStack Query hooks for butler analytics endpoints.
 * (bu-iuol4.16)
 *
 * Hooks:
 *   useButlerHourlyActivity  — GET /api/butlers/{name}/analytics/hourly-activity
 *   useButlerDailyActivity   — GET /api/butlers/{name}/analytics/daily-activity
 *   useButlerSessionKinds    — GET /api/butlers/{name}/analytics/session-kinds
 *   useButlerLatencyStats    — graceful no-op; endpoint not yet on main (bu-iuol4.6)
 */

import { useQuery } from "@tanstack/react-query";

import {
  getButlerHourlyActivity,
  getButlerDailyActivity,
  getButlerSessionKinds,
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
//
// NOTE: The latency-stats endpoint (bu-iuol4.6 / PR #1539) is not yet merged
// into main. This hook is a graceful no-op stub that always returns
// isAvailable: false and null values, so callers can render "—" safely.
// Wire it to the real endpoint once bu-iuol4.6 lands.
// ---------------------------------------------------------------------------

export interface LatencyStats {
  /** Median session duration in ms, or null when unavailable. */
  p50_ms: number | null;
  /** 95th-percentile session duration in ms, or null when unavailable. */
  p95_ms: number | null;
  /** Total sessions in the window. */
  sessions_count: number;
  /** Error count in the window. */
  errors_count: number;
}

/**
 * Graceful stub for the latency-stats endpoint (bu-iuol4.6).
 *
 * Always returns isAvailable: false with null p50/p95 until the endpoint
 * exists on main and this hook is wired to it. Parameters are accepted for
 * forward-compatibility only.
 *
 * @param butlerName - Reserved for future wiring
 * @param windowDays - Reserved for future wiring
 */
export function useButlerLatencyStats(
  butlerName: string,
  windowDays?: number,
): { data: LatencyStats | null; isLoading: false; isError: false; isAvailable: false } {
  // Params are intentionally unused — this is a forward-compat stub.
  // Wire to the real endpoint when bu-iuol4.6 / PR #1539 is merged.
  void butlerName;
  void windowDays;
  return {
    data: null,
    isLoading: false,
    isError: false,
    isAvailable: false,
  };
}
