// ---------------------------------------------------------------------------
// <Time> — semantic time primitive with absolute / relative / smart modes
// (bu-v1tt2.2, bu-fv4vy, bu-5j7p9)
//
// Renders a <time> element with a datetime attribute (a11y / machine-readable)
// and human-readable text according to the chosen mode.
//
// Mode behaviour:
//   - absolute: "May 3, 2026 at 4:42 PM SGT"
//   - relative:  "4 minutes ago"
//   - smart:     relative for < 24 h, absolute for older (default)
//
// compact flag:
//   When true, absolute output omits the year and timezone abbreviation,
//   producing shorter output suited for dense table cells.
//   Examples (compact=true):
//     precision=minute:  "May 3, 4:42 PM"
//     precision=second:  "May 3, 4:42:07 PM"
//     precision=hour:    "May 3, 4 PM"
//     precision=day:     "May 3"
//   compact has no effect on relative output.
//
// precision extensions (bu-5j7p9):
//   - weekday: full weekday + date, e.g. "Sunday, May 3, 2026"
//              Compact form omits year: "Sunday, May 3"
//              Timezone label intentionally suppressed (date headings need none).
//   - time:    time-only (24-hour clock), e.g. "08:30"
//              Used for dense time-column cells. compact flag ignored.
//              Timezone label intentionally suppressed (cell is too narrow).
//   Both precisions still use the user's timezone for TZ-correct rendering.
//
// Date-only strings (YYYY-MM-DD):
//   Anchored to UTC noon (T12:00:00.000Z) to prevent midnight-crossing
//   artifacts when rendering in the user's timezone. Without this, `new Date(
//   "YYYY-MM-DD")` parses as UTC midnight and can show the previous local day
//   for timezones west of UTC.
//
// Timezone is read from AppTimezoneContext via useTimezone(). The provider is
// mounted at App level so all pages share the owner timezone automatically.
// An explicit `timezone` prop overrides the context value — useful for
// isolated rendering outside an AppTimezoneProvider.
//
// Smart-mode threshold is 24 h. If it needs to be configurable in the future,
// thread a `smartThresholdMs` prop through; the logic is isolated in one place.
// ---------------------------------------------------------------------------

import { formatDistanceToNow } from "date-fns"
import { formatInTimeZone } from "date-fns-tz"
import { useTimezone } from "@/components/ui/timezone-context"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type TimeMode = "absolute" | "relative" | "smart"
export type TimePrecision = "second" | "minute" | "hour" | "day" | "weekday" | "time"

export interface TimeProps {
  /** The date value to render. Accepts an ISO 8601 string or a Date object. */
  value: string | Date
  /**
   * Display mode.
   *   - absolute: full date + time + tz abbreviation
   *   - relative: "N minutes/hours/days ago"
   *   - smart: relative when < 24 h old, absolute otherwise
   * @default "smart"
   */
  mode?: TimeMode
  /**
   * Display precision (affects absolute / smart-absolute output only).
   * Relative mode always uses date-fns natural language.
   *   - second:  "May 3, 2026 at 4:42:07 PM SGT"
   *   - minute:  "May 3, 2026 at 4:42 PM SGT"  (default)
   *   - hour:    "May 3, 2026 at 4 PM SGT"
   *   - day:     "May 3, 2026 SGT"
   *   - weekday: "Sunday, May 3, 2026"  (compact: "Sunday, May 3"; no tz label)
   *   - time:    "08:30"  (24-hour clock, time-only; compact has no effect; no tz label)
   * @default "minute"
   */
  precision?: TimePrecision
  /**
   * When true, absolute output omits the year and timezone abbreviation for
   * use in dense table cells where space is limited.
   *   - precision=minute:  "May 3, 4:42 PM"
   *   - precision=second:  "May 3, 4:42:07 PM"
   *   - precision=hour:    "May 3, 4 PM"
   *   - precision=day:     "May 3"
   * Has no effect on relative output.
   * @default false
   */
  compact?: boolean
  /**
   * IANA timezone name override.
   * Defaults to the owner timezone from AppTimezoneContext.
   */
  timezone?: string
  /**
   * When true, a full ISO 8601 timestamp is rendered in the native browser
   * tooltip via the HTML title attribute.
   * @default true
   */
  showTitle?: boolean
  /** Additional className forwarded to the <time> element. */
  className?: string
}

// ---------------------------------------------------------------------------
// Format strings per precision
// ---------------------------------------------------------------------------

// Note: `weekday` and `time` precisions intentionally omit the timezone
// abbreviation (zzz). `weekday` renders a calendar-date heading where the tz
// label adds no value; `time` renders a compact 24-hour column cell. Both still
// consume the user's timezone via formatInTimeZone() so output is TZ-correct.
const ABSOLUTE_FORMAT: Record<TimePrecision, string> = {
  second:  "MMM d, yyyy 'at' h:mm:ss a zzz",
  minute:  "MMM d, yyyy 'at' h:mm a zzz",
  hour:    "MMM d, yyyy 'at' h a zzz",
  day:     "MMM d, yyyy zzz",
  weekday: "EEEE, MMMM d, yyyy",
  time:    "HH:mm",
}

// Compact format omits year and timezone — used in dense table cells.
const COMPACT_FORMAT: Record<TimePrecision, string> = {
  second:  "MMM d, h:mm:ss a",
  minute:  "MMM d, h:mm a",
  hour:    "MMM d, h a",
  day:     "MMM d",
  weekday: "EEEE, MMMM d",
  time:    "HH:mm",  // time precision: compact has no effect, same format
}

// 24-hour threshold in ms — smart mode crossover point.
const SMART_THRESHOLD_MS = 24 * 60 * 60 * 1_000

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Regex for ISO date-only strings ("YYYY-MM-DD"). When JS parses these with
// `new Date("YYYY-MM-DD")` it treats them as UTC midnight, which shifts the
// displayed date by one day for timezones west of UTC. To avoid this, we
// anchor date-only values to UTC noon — far enough from midnight to survive
// any real-world UTC offset (UTC-12 through UTC+14).
const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/

function toDate(value: string | Date): Date {
  if (typeof value === "string" && DATE_ONLY_RE.test(value)) {
    return new Date(value + "T12:00:00.000Z")
  }
  return value instanceof Date ? value : new Date(value)
}

function formatAbsolute(date: Date, precision: TimePrecision, tz: string, compact = false): string {
  try {
    const fmt = compact ? COMPACT_FORMAT[precision] : ABSOLUTE_FORMAT[precision]
    // Compact format does not embed a timezone token, so tz is still passed to
    // formatInTimeZone to ensure the rendered local time is correct.
    return formatInTimeZone(date, tz, fmt)
  } catch {
    return date.toISOString()
  }
}

function formatRelative(date: Date): string {
  try {
    return formatDistanceToNow(date, { addSuffix: true })
  } catch {
    return date.toISOString()
  }
}

/**
 * Compute the display text for smart mode outside the React render path so
 * that the react-hooks/purity rule does not flag Date.now() usage.
 * Returns { useRelative: boolean } so the component can delegate to the right
 * formatter without calling Date.now() itself.
 */
function resolveSmartMode(date: Date): { useRelative: boolean } {
  // Use Math.abs so that future dates are also bounded by the threshold —
  // without this, negative ageMs would always pass the < check and render
  // far-future dates (e.g. "in 5 years") as relative.
  const ageMs = Math.abs(Date.now() - date.getTime())
  return { useRelative: ageMs < SMART_THRESHOLD_MS }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Semantic <time> primitive.
 *
 * @example
 * // Smart mode — relative if < 24 h, absolute otherwise
 * <Time value={entity.created_at} />
 *
 * @example
 * // Always show relative
 * <Time value={notification.sent_at} mode="relative" />
 *
 * @example
 * // Always show absolute, day precision
 * <Time value={fact.created_at} mode="absolute" precision="day" />
 *
 * @example
 * // Compact absolute — no year or timezone, for dense table cells
 * <Time value={row.created_at} mode="absolute" compact />
 *
 * @example
 * // Weekday section heading — "Sunday, May 3, 2026"
 * <Time value={entry.date} mode="absolute" precision="weekday" />
 *
 * @example
 * // Time-only cell (24-hour clock) — "08:30"
 * <Time value={entry.eaten_at} mode="absolute" precision="time" />
 */
export function Time({
  value,
  mode = "smart",
  precision = "minute",
  compact = false,
  timezone,
  showTitle = true,
  className,
}: TimeProps) {
  const contextTz = useTimezone()
  const tz = timezone ?? contextTz

  const date = toDate(value)

  // Guard: invalid date (e.g. malformed ISO string) — render a safe placeholder
  // rather than throwing a RangeError from toISOString().
  if (Number.isNaN(date.getTime())) {
    return (
      <time className={className}>
        {String(value)}
      </time>
    )
  }

  const isoString = date.toISOString()

  let text: string
  if (mode === "relative") {
    text = formatRelative(date)
  } else if (mode === "absolute") {
    text = formatAbsolute(date, precision, tz, compact)
  } else {
    // smart: relative for < 24 h, absolute for older.
    // Math.abs ensures future dates obey the threshold symmetrically.
    const { useRelative } = resolveSmartMode(date)
    text = useRelative
      ? formatRelative(date)
      : formatAbsolute(date, precision, tz, compact)
  }

  return (
    <time
      dateTime={isoString}
      title={showTitle ? isoString : undefined}
      className={className}
    >
      {text}
    </time>
  )
}
