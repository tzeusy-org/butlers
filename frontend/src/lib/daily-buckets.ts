// ---------------------------------------------------------------------------
// Daily-bucket histogram helpers [bu-5fwbh]
//
// Backend "daily activity" rows are keyed by a `YYYY-MM-DD` UTC day string
// (emitted from a UTC `date_trunc`). Placing them onto a fixed-width day
// histogram means measuring how many whole days ago each key is. That math
// must be done entirely in UTC — evaluating a UTC-anchored day key with local
// Date getters re-interprets the instant in the viewer's host zone and shifts
// every bar, a bug the TZ=UTC test pin hides (see the sibling test).
// ---------------------------------------------------------------------------

/**
 * Whole days between `now`'s UTC calendar date and a backend daily-bucket day
 * key (`YYYY-MM-DD`). Positive = in the past, `0` = today (UTC). Returns
 * `null` for an unparseable key.
 *
 * Both sides are evaluated in UTC, so the result never depends on the viewer's
 * host timezone. This replaces an earlier implementation that parsed the key as
 * `…T00:00:00Z` but then read it back with local `getFullYear/getMonth/getDate`
 * — for any viewer west of UTC that re-interpretation rolled the bucket onto
 * the previous calendar day, shifting the whole histogram by one slot. vitest
 * pins `TZ=UTC` (vite.config.ts), which made that regression invisible.
 */
export function bucketDaysAgo(dayKey: string, now: Date): number | null {
  const bucketMs = Date.parse(`${dayKey}T00:00:00Z`);
  if (Number.isNaN(bucketMs)) return null;
  const todayMs = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  return Math.round((todayMs - bucketMs) / 86_400_000);
}
