// ---------------------------------------------------------------------------
// day-window — owner-timezone calendar-day helpers [bu-s0d8j]
//
// Pure functions for computing and bucketing YYYY-MM-DD "day keys" in the
// owner's configured IANA timezone (useTimezone / AppTimezoneProvider,
// DEFAULT_TZ = "Asia/Singapore") rather than the host/browser clock.
//
// Motivation (bu-5fwbh sweep, PR #3062): ad-hoc Date getters (getFullYear /
// getMonth / getDate) bucket by the *host* local day. On a UTC-clocked server
// (or any viewer whose clock differs from the owner), a meal logged just after
// owner-midnight lands in the wrong calendar day, and the query window
// (todayISO / daysAgoISO) drifts off the buckets it is supposed to frame.
//
// Convention mirrors ChroniclesPage.formatDateInTimeZone and PR #3065: derive
// the day key via Intl.DateTimeFormat with an explicit `timeZone`, and fall
// back to UTC — never host-local — when the timezone string is invalid.
// ---------------------------------------------------------------------------

/** Format a Date's calendar day (YYYY-MM-DD) in `timeZone` via Intl. */
function formatDayKey(date: Date, timeZone: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const lookup = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${lookup.year}-${lookup.month}-${lookup.day}`;
}

/**
 * The YYYY-MM-DD calendar day of `date` as observed in `timeZone`.
 *
 * A malformed IANA name falls back to UTC (never the host clock), so callers
 * degrade to a stable, viewer-independent key rather than silently reverting
 * to host-local bucketing.
 */
export function dayKeyInTimeZone(date: Date, timeZone: string): string {
  try {
    return formatDayKey(date, timeZone);
  } catch {
    return formatDayKey(date, "UTC");
  }
}

/** Shift a YYYY-MM-DD day key by `deltaDays` calendar days (UTC-anchored math). */
function shiftDayKey(dayKey: string, deltaDays: number): string {
  const [year, month, day] = dayKey.split("-").map(Number);
  const shifted = new Date(Date.UTC(year, month - 1, day + deltaDays));
  return shifted.toISOString().slice(0, 10);
}

/** Today's day key (YYYY-MM-DD) in the owner timezone. */
export function todayISO(timeZone: string): string {
  return dayKeyInTimeZone(new Date(), timeZone);
}

/** The day key `n` calendar days before today, in the owner timezone. */
export function daysAgoISO(n: number, timeZone: string): string {
  return shiftDayKey(todayISO(timeZone), -n);
}
