// ---------------------------------------------------------------------------
// tz-format — timezone-aware date formatting helpers (bu-k18cm)
//
// All format functions here take an explicit `tz` (IANA timezone name) so
// they never fall back to the browser's local timezone.
//
// Relies on date-fns-tz for IANA-correct formatting.
// ---------------------------------------------------------------------------

import { formatInTimeZone, toZonedTime, fromZonedTime } from "date-fns-tz"
import { startOfDay, endOfDay } from "date-fns"

export { formatInTimeZone }

/**
 * Default IANA timezone for the owner's configured zone.
 * Matches the SGT constant in briefing.py.
 */
export const DEFAULT_TZ = "Asia/Singapore"

/**
 * Format an ISO string or ms timestamp as "HH:mm" in the given timezone.
 * Returns "?" if the input is falsy or unparseable.
 */
export function formatTimeInTz(
  iso: string | number | null | undefined,
  tz: string,
): string {
  if (!iso && iso !== 0) return "?"
  try {
    return formatInTimeZone(new Date(iso), tz, "HH:mm")
  } catch {
    return "?"
  }
}

/**
 * Format an ISO string as a medium-date + short-time string in the given timezone,
 * including the timezone abbreviation (e.g. "30 Apr 2026, 18:30 SGT").
 * Returns "—" if the input is falsy.
 */
export function formatDateTimeInTz(
  iso: string | null | undefined,
  tz: string,
): string {
  if (!iso) return "—"
  try {
    return formatInTimeZone(new Date(iso), tz, "d MMM yyyy, HH:mm zzz")
  } catch {
    return "—"
  }
}

/**
 * Format a timestamp (ms or ISO) as a short label for the scrubber.
 * Uses time-only format when the window is ≤ 2 days; otherwise adds date.
 * The tz abbreviation is appended to the label so the user always sees
 * which timezone is being shown.
 */
export function formatScrubberLabel(
  ms: number,
  windowDurationMs: number,
  tz: string,
): string {
  try {
    if (windowDurationMs <= 2 * 86_400_000) {
      return formatInTimeZone(new Date(ms), tz, "HH:mm zzz")
    }
    return formatInTimeZone(new Date(ms), tz, "d MMM HH:mm zzz")
  } catch {
    return "?"
  }
}

/**
 * Format a gantt axis tick label in the given timezone.
 * Short time when window ≤ 2 days; short date otherwise.
 */
export function formatGanttTickLabel(
  ms: number,
  windowDuration: number,
  tz: string,
): string {
  try {
    if (windowDuration <= 2 * 86_400_000) {
      return formatInTimeZone(new Date(ms), tz, "HH:mm")
    }
    return formatInTimeZone(new Date(ms), tz, "d MMM")
  } catch {
    return "?"
  }
}

/**
 * Return the start of the day for `date` anchored to `tz`-local midnight,
 * as a UTC Date. Equivalent to date-fns startOfDay but tz-aware.
 *
 * Algorithm:
 *   1. Convert UTC Date to "wall clock" representation in tz via toZonedTime.
 *   2. Apply startOfDay/endOfDay on that wall-clock Date.
 *   3. Re-interpret that wall-clock time as belonging to tz via fromZonedTime
 *      to get the correct UTC instant.
 */
export function startOfDayInTz(date: Date, tz: string): Date {
  const zoned = toZonedTime(date, tz)
  return fromZonedTime(startOfDay(zoned), tz)
}

/**
 * Return the end of the day for `date` anchored to `tz`-local midnight,
 * as a UTC Date. Equivalent to date-fns endOfDay but tz-aware.
 */
export function endOfDayInTz(date: Date, tz: string): Date {
  const zoned = toZonedTime(date, tz)
  return fromZonedTime(endOfDay(zoned), tz)
}

/**
 * Return the [from, to) UTC window for a calendar day given as "YYYY-MM-DD",
 * interpreted in `tz`. Mirrors the backend's `day_window_utc`
 * (datetime.combine(target, time.min, tzinfo=tz)) exactly for every zone,
 * including UTC+13/+14: the day string is treated as a naive local midnight in
 * `tz`, never reinterpreted through a UTC anchor, so the FE drilldown window
 * matches the day the backend reconstructed.
 */
export function dayWindowInTz(isoDate: string, tz: string): { from: Date; to: Date } {
  const [y, m, d] = isoDate.split("-").map(Number)
  const nextIso = new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10)
  return {
    from: fromZonedTime(`${isoDate}T00:00:00`, tz),
    to: fromZonedTime(`${nextIso}T00:00:00`, tz),
  }
}
