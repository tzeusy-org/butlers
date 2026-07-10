// ---------------------------------------------------------------------------
// Hourly-bucket histogram helpers [bu-8ogli]
//
// Backend "hourly activity" rows (`GET /api/butlers/{name}/analytics/
// hourly-activity`, sessions.py::_HOURLY_ACTIVITY_SQL) key each bucket by a
// `hour_start` timestamptz produced from `DATE_TRUNC('hour', NOW())` over a
// `generate_series`. The alignment happens in the DB session timezone, which
// is UTC (postgres:17-alpine sets no TZ, and the asyncpg pool sets no
// `TimeZone` server setting — see api/db.py / db.py), so the boundaries land on
// UTC `:00` instants. asyncpg returns each as a tz-aware datetime and it is
// serialized to an ISO-8601 instant with offset.
//
// The instant itself is unambiguous, so placing a bucket onto an hour-of-day
// histogram is purely a display choice: it must be done in the OWNER timezone
// (useTimezone / AppTimezoneProvider, DEFAULT_TZ = "Asia/Singapore"), never the
// viewer's host zone. The previous inline `new Date(hour_start).getHours()` read
// the instant back in the viewer's host zone, so the same bucket slotted into a
// different hour depending on where the dashboard was opened. vitest pins
// TZ=UTC (vite.config.ts), which made that skew invisible to the suite — the
// sibling test varies TZ explicitly to close the gap.
// ---------------------------------------------------------------------------

/**
 * Hour-of-day (0-23) that a backend `hour_start` instant falls on, evaluated in
 * `timeZone`. Returns `null` for an unparseable timestamp.
 *
 * Uses `Intl.DateTimeFormat` with an explicit `timeZone` and `hourCycle: "h23"`
 * so the result depends only on the instant and the owner timezone, never on
 * the viewer's host zone. `h23` renders midnight as `0` (not the `24` that
 * `h24` produces); the explicit `24 → 0` fold below is a belt-and-braces guard
 * for that edge in case an engine surfaces `"24"` anyway.
 */
export function bucketHourInZone(hourStart: string, timeZone: string): number | null {
  const ms = Date.parse(hourStart);
  if (Number.isNaN(ms)) return null;

  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hour: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(ms));

  const hourPart = parts.find((p) => p.type === "hour");
  if (hourPart === undefined) return null;

  let hour = Number(hourPart.value);
  if (!Number.isFinite(hour)) return null;
  if (hour === 24) hour = 0; // midnight edge: fold h24-style "24" back to 0
  if (hour < 0 || hour > 23) return null;
  return hour;
}
