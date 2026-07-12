/**
 * useFleetHaltStatus — derives the monthly spend-ceiling fleet-halt state
 * from GET /api/dispatch/attempts (bu-7o89u.3).
 *
 * The ceiling is enforced by check_monthly_ceiling / the spawner
 * (spawner.py:1179-1202): once month-to-date spend reaches the configured
 * ceiling, EVERY dispatch across EVERY butler is denied and a `quota_skip`
 * provenance row is written with a `failure_reason` starting "Monthly spend
 * ceiling reached". That's distinct from the routine same-tier token-quota
 * `quota_skip` rows written during normal failover (spawner.py:1082,1122) --
 * `reason_prefix` isolates the ceiling-specific denials from that noise.
 *
 * Three lightweight queries against the same endpoint, scoped by params:
 *   - onset:  since=start-of-UTC-month, order=asc, limit=1  -> total this
 *             month (accurate regardless of limit -- meta.total is a
 *             server-side COUNT) + the earliest row's ts ("denied since
 *             <ts>"). UTC-anchored to match the backend ceiling gate's own
 *             month window (price_mtd_from_ledger).
 *   - today:  since=start-of-owner-tz-day, limit=1           -> today's
 *             count, in the owner's configured timezone (day-window.ts).
 *   - recent: since=start-of-UTC-month, order=desc, limit=N  -> drawer rows.
 *
 * `isError` MUST gate the caller's render -- a failed fetch is NOT "no
 * halt" (fleet degraded-source convention: never fabricate calm).
 */

import { fromZonedTime } from "date-fns-tz";
import { useQuery } from "@tanstack/react-query";

import { getDispatchAttempts } from "@/api/client";
import type { DispatchAttemptEntry } from "@/api/types";
import { useBusAwarePollInterval } from "@/hooks/use-bus-aware-poll-interval";
import { useTimezone } from "@/components/ui/timezone-context";
import { todayISO } from "@/lib/day-window";

/** The quota_skip failure_reason prefix written by the monthly spend-ceiling
 * hard block -- see module doc above for why this must be reason-scoped, not
 * just outcome-scoped. */
export const CEILING_DENIAL_REASON_PREFIX = "Monthly spend ceiling reached";

const DEFAULT_DRAWER_LIMIT = 20;

// "Today" uses the owner's configured timezone (day-window.ts / useTimezone),
// matching this codebase's established owner-tz day-bucketing convention
// (bu-5fwbh/bu-s0d8j) and the spec's explicit "owner-tz day" wording. A raw
// host/browser-local boundary (the previous `setHours(0,0,0,0)` on a bare
// `new Date()`) drifts from the owner's actual calendar day whenever the
// viewer's clock differs from the owner's configured zone.
function startOfTodayIso(timeZone: string): string {
  return fromZonedTime(`${todayISO(timeZone)}T00:00:00`, timeZone).toISOString();
}

// "Month" is anchored in UTC, matching the backend ceiling gate itself
// (`date_trunc('month', now() AT TIME ZONE 'UTC')` in
// `price_mtd_from_ledger` / model_routing.py) -- the same window the
// sibling MTD figure on this page already reports. Unlike "today" this
// isn't a pure UX bucket: it must track the actual calendar month the
// ceiling resets on, or "N denied since <ts>" would misstate how long the
// current breach has existed.
function startOfMonthIso(): string {
  const d = new Date();
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1)).toISOString();
}

export interface FleetHaltStatus {
  /** True once loaded, not errored, and at least one ceiling denial exists this month. */
  active: boolean;
  /** Total ceiling denials since the start of the current calendar month. */
  deniedTotal: number;
  /** Ceiling denials since the start of the current day in the owner's configured timezone. */
  deniedToday: number;
  /** ISO timestamp of the earliest ceiling denial this month, or null if none/unknown. */
  since: string | null;
  /** Most recent denied attempts (desc by ts) for the attempts drawer. */
  recentAttempts: DispatchAttemptEntry[];
  isLoading: boolean;
  /** True when any of the underlying queries failed -- render a degraded note, not "no halt". */
  isError: boolean;
}

export function useFleetHaltStatus(drawerLimit: number = DEFAULT_DRAWER_LIMIT): FleetHaltStatus {
  const refetchInterval = useBusAwarePollInterval();
  const ownerTz = useTimezone();
  const sinceMonth = startOfMonthIso();
  const sinceToday = startOfTodayIso(ownerTz);

  const onset = useQuery({
    queryKey: ["dispatch-attempts", "ceiling-onset", sinceMonth],
    queryFn: () =>
      getDispatchAttempts({
        outcome: "quota_skip",
        reason_prefix: CEILING_DENIAL_REASON_PREFIX,
        since: sinceMonth,
        order: "asc",
        limit: 1,
      }),
    refetchInterval,
  });

  const today = useQuery({
    queryKey: ["dispatch-attempts", "ceiling-today", sinceToday],
    queryFn: () =>
      getDispatchAttempts({
        outcome: "quota_skip",
        reason_prefix: CEILING_DENIAL_REASON_PREFIX,
        since: sinceToday,
        limit: 1,
      }),
    refetchInterval,
  });

  const recent = useQuery({
    queryKey: ["dispatch-attempts", "ceiling-recent", sinceMonth, drawerLimit],
    queryFn: () =>
      getDispatchAttempts({
        outcome: "quota_skip",
        reason_prefix: CEILING_DENIAL_REASON_PREFIX,
        since: sinceMonth,
        order: "desc",
        limit: drawerLimit,
      }),
    refetchInterval,
  });

  const isLoading = onset.isLoading || today.isLoading || recent.isLoading;
  const isError = onset.isError || today.isError || recent.isError;
  const deniedTotal = onset.data?.meta.total ?? 0;

  return {
    active: !isError && !isLoading && deniedTotal > 0,
    deniedTotal,
    deniedToday: today.data?.meta.total ?? 0,
    since: onset.data?.data[0]?.ts ?? null,
    recentAttempts: recent.data?.data ?? [],
    isLoading,
    isError,
  };
}
