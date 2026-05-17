/**
 * TanStack Query hooks for the spend API.
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
 * Format a Date as YYYY-MM-DD for spend API query params.
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

/** Fetch aggregate spend summary with auto-refresh.
 *
 * When `from` and `to` are provided they override `period` and the server
 * computes the summary over the custom [from, to] date range. Dates are
 * formatted in the owner timezone via `formatCostDate` so day boundaries
 * are consistent with the time window anchor.
 *
 * When `butler` is provided, the query is scoped to that butler only.
 * Supported by the backend since bu-iuol4.12.
 */
export function useSpendSummary(period?: string, from?: Date, to?: Date, butler?: string) {
  const fromStr = from ? formatCostDate(from) : undefined;
  const toStr = to ? formatCostDate(to) : undefined;

  return useQuery({
    queryKey: ["cost-summary", period, fromStr, toStr, butler],
    queryFn: () => getCostSummary(period, fromStr, toStr, butler),
    refetchInterval: 60_000,
  });
}

/**
 * Fetch daily spend breakdown, optionally scoped to a date range and/or a butler.
 * Accepts Date objects; converts to YYYY-MM-DD for the API.
 * Falls back to the API default (last 7 days) when from/to are omitted.
 *
 * @param [from] - Start of the date range (inclusive). Omit to fall back to the API default (last 7 days).
 * @param [to]   - End of the date range (inclusive). Omit to fall back to the API default (last 7 days).
 * @param [options.butler]          - Butler name to scope the query (cache is partitioned per butler).
 * @param [options.refetchInterval] - Override the default 60s polling interval. Pass `false` to disable.
 */
export function useDailySpend(
  from?: Date,
  to?: Date,
  options?: { refetchInterval?: number | false; butler?: string },
) {
  const fromStr = from ? formatCostDate(from) : undefined;
  const toStr = to ? formatCostDate(to) : undefined;
  const { refetchInterval, butler } = options ?? {};

  return useQuery({
    queryKey: ["daily-costs", fromStr, toStr, butler],
    queryFn: () => getDailyCosts(fromStr, toStr, butler),
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
