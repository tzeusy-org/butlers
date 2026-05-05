/**
 * TanStack Query hooks for the costs API.
 */

import { useQuery } from "@tanstack/react-query";

import { getCostSummary, getDailyCosts, getTopSessions } from "@/api/index.ts";
import { formatInTimeZone } from "date-fns-tz";
import { OWNER_TZ_DEFAULT } from "@/hooks/use-time-window";

// ---------------------------------------------------------------------------
// Format helper
// ---------------------------------------------------------------------------

const DATE_FMT = "yyyy-MM-dd";

/**
 * Format a Date as YYYY-MM-DD for cost API query params.
 * Uses the owner timezone so that day boundaries match the window anchor —
 * dates from useTimeWindow are UTC instants representing owner-tz midnight,
 * and formatting them in local browser time would give the wrong date string.
 */
export function formatCostDate(d: Date, tz: string = OWNER_TZ_DEFAULT): string {
  return formatInTimeZone(d, tz, DATE_FMT);
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

/** Fetch aggregate cost summary with auto-refresh.
 *
 * When `from` and `to` are provided they override `period` and the server
 * computes the summary over the custom [from, to] date range. Dates are
 * formatted in the owner timezone via `formatCostDate` so day boundaries
 * are consistent with the time window anchor.
 */
export function useCostSummary(period?: string, from?: Date, to?: Date) {
  const fromStr = from ? formatCostDate(from) : undefined;
  const toStr = to ? formatCostDate(to) : undefined;

  return useQuery({
    queryKey: ["cost-summary", period, fromStr, toStr],
    queryFn: () => getCostSummary(period, fromStr, toStr),
    refetchInterval: 60_000,
  });
}

/**
 * Fetch daily cost breakdown, optionally scoped to a date range.
 * Accepts Date objects; converts to YYYY-MM-DD for the API.
 * Falls back to the API default (last 7 days) when from/to are omitted.
 */
export function useDailyCosts(from?: Date, to?: Date, refetchInterval?: number | false) {
  const fromStr = from ? formatCostDate(from) : undefined;
  const toStr = to ? formatCostDate(to) : undefined;

  return useQuery({
    queryKey: ["daily-costs", fromStr, toStr],
    queryFn: () => getDailyCosts(fromStr, toStr),
    refetchInterval: refetchInterval ?? 60_000,
  });
}

/** Fetch most expensive sessions with auto-refresh. */
export function useTopSessions(limit?: number) {
  return useQuery({
    queryKey: ["top-sessions", limit],
    queryFn: () => getTopSessions(limit),
    refetchInterval: 60_000,
  });
}
