/**
 * TanStack Query hooks for the spend API.
 */

import { useQuery } from "@tanstack/react-query";

import { getCostSummary, getDailyCosts, getTopSessions, getCostsBySchedule } from "@/api/index.ts";
import { formatInTimeZone } from "date-fns-tz";
import { OWNER_TZ_DEFAULT } from "@/hooks/use-time-window";
import { useBusAwarePollInterval } from "@/hooks/use-bus-aware-poll-interval";

// ---------------------------------------------------------------------------
// Format helper
// ---------------------------------------------------------------------------

const DATE_FMT = "yyyy-MM-dd";

/** Date-key timezone shared by ledger-backed implicit Spend windows. */
export const SPEND_UTC_DATE_KEY_TIMEZONE = "UTC";

/**
 * Build an inclusive calendar-day range anchored to UTC rather than the
 * browser or owner timezone. Spend's implicit windows must share the ledger
 * and monthly-ceiling UTC day; explicit operator-selected ranges retain their
 * caller-selected date-key timezone.
 */
export function utcDateWindow(days: number, now: Date = new Date()): { from: Date; to: Date } {
  if (!Number.isInteger(days) || days < 1) {
    throw new RangeError("utcDateWindow requires at least one whole day");
  }

  const currentDayStart = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const dayMs = 24 * 60 * 60 * 1000;
  return {
    from: new Date(currentDayStart - (days - 1) * dayMs),
    to: new Date(currentDayStart + dayMs - 1),
  };
}

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
 * formatted in the owner timezone via `formatCostDate` by default. Callers
 * may opt into UTC date keys for implicit ledger-aligned Spend windows.
 *
 * When `butler` is provided, the query is scoped to that butler only.
 * Supported by the backend since bu-iuol4.12.
 */
export function useSpendSummary(
  period?: string,
  from?: Date,
  to?: Date,
  butler?: string,
  dateKeyTimezone: string = OWNER_TZ_DEFAULT,
) {
  const fromStr = from ? formatCostDate(from, dateKeyTimezone) : undefined;
  const toStr = to ? formatCostDate(to, dateKeyTimezone) : undefined;
  // Live path: /api/spend/stream + the fleet event bus (bu-86c4c.8) both
  // invalidate ["cost-summary"] on every spend call event. Polling is a
  // 5-minute reconciliation sweep while the bus is connected, tightening to a
  // fast fallback while it's down (bu-01r64.3) — a safety net either way, not
  // the primary path.
  const refetchInterval = useBusAwarePollInterval();

  return useQuery({
    queryKey: ["cost-summary", period, fromStr, toStr, butler],
    queryFn: () => getCostSummary(period, fromStr, toStr, butler),
    refetchInterval,
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
 * @param [options.refetchInterval] - Override the default 5-minute reconciliation-sweep polling interval
 *   (bu-86c4c.8: the fleet event bus is now the primary update path). Pass `false` to disable.
 * @param [options.dateKeyTimezone] - Timezone used only to serialize the optional date keys.
 */
export function useDailySpend(
  from?: Date,
  to?: Date,
  options?: { refetchInterval?: number | false; butler?: string; dateKeyTimezone?: string },
) {
  const { refetchInterval, butler, dateKeyTimezone = OWNER_TZ_DEFAULT } = options ?? {};
  const fromStr = from ? formatCostDate(from, dateKeyTimezone) : undefined;
  const toStr = to ? formatCostDate(to, dateKeyTimezone) : undefined;
  const busAwareInterval = useBusAwarePollInterval();

  return useQuery({
    queryKey: ["daily-costs", fromStr, toStr, butler],
    queryFn: () => getDailyCosts(fromStr, toStr, butler),
    refetchInterval: refetchInterval ?? busAwareInterval,
  });
}

/**
 * Fetch most expensive sessions with auto-refresh, optionally scoped to a date range.
 *
 * When `from`/`to` are provided, results are scoped to sessions started within that
 * inclusive range (backend support added bu-oaiiw). Omit both for all-time results.
 */
export function useTopSessions(
  limit?: number,
  from?: Date,
  to?: Date,
  dateKeyTimezone: string = OWNER_TZ_DEFAULT,
) {
  const fromStr = from ? formatCostDate(from, dateKeyTimezone) : undefined;
  const toStr = to ? formatCostDate(to, dateKeyTimezone) : undefined;
  // See useSpendSummary above: fleet-event-bus-driven, poll is a bus-aware safety net.
  const refetchInterval = useBusAwarePollInterval();

  return useQuery({
    queryKey: ["top-sessions", limit, fromStr, toStr],
    queryFn: () => getTopSessions(limit, fromStr, toStr),
    refetchInterval,
  });
}

/**
 * Fetch per-schedule cost analysis (projected monthly USD per cron job), optionally
 * scoped to a date range.
 *
 * When `from`/`to` are provided, run totals are scoped to that inclusive range
 * (backend support added bu-oaiiw). Omit both for all-time totals.
 */
export function useCostsBySchedule(
  from?: Date,
  to?: Date,
  dateKeyTimezone: string = OWNER_TZ_DEFAULT,
) {
  const fromStr = from ? formatCostDate(from, dateKeyTimezone) : undefined;
  const toStr = to ? formatCostDate(to, dateKeyTimezone) : undefined;
  const refetchInterval = useBusAwarePollInterval();

  return useQuery({
    queryKey: ["costs-by-schedule", fromStr, toStr],
    queryFn: () => getCostsBySchedule(fromStr, toStr),
    refetchInterval,
  });
}
