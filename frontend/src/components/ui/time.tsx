// ---------------------------------------------------------------------------
// <Time> — semantic time primitive with absolute / relative / smart modes
// (bu-v1tt2.2, bu-fv4vy, bu-5j7p9, bu-hb7dh.4)
//
// Renders a <time> element with a datetime attribute (a11y / machine-readable)
// and human-readable text according to the chosen mode.
//
// Mode behaviour:
//   - absolute:         "May 3, 2026 at 4:42 PM SGT"
//   - relative:         "4 minutes ago"
//   - smart:            relative for < 24 h, absolute for older (default)
//   - clock-24h-mono:   "08:30" — live-ticking 24-hour clock, tabular-nums
//                       monospace. Renders as the owner timezone via context.
//                       Aligned to the next minute boundary then ticks every
//                       60 s (SSR: static snapshot).
//   - relative-compact: "6m ago", "2h ago", "3d ago"; "now" for < 60 s.
//                       Seconds-precision floor — never produces "5 seconds ago"
//                       verbosity. Future times render as "in 6m", "in 2h", "in 3d".
//                       Owner-timezone-agnostic (uses wall-clock diff).
//
// compact flag:
//   When true, absolute output omits the year and timezone abbreviation,
//   producing shorter output suited for dense table cells.
//   Examples (compact=true):
//     precision=minute:  "May 3, 4:42 PM"
//     precision=second:  "May 3, 4:42:07 PM"
//     precision=hour:    "May 3, 4 PM"
//     precision=day:     "May 3"
//   compact has no effect on relative or relative-compact output.
//
// precision extensions (bu-5j7p9, bu-hb7dh.4):
//   - weekday:    full weekday + date, e.g. "Sunday, May 3, 2026"
//                 Compact form omits year: "Sunday, May 3"
//                 Timezone label intentionally suppressed (date headings need none).
//   - time:       time-only (24-hour clock), e.g. "08:30"
//                 Used for dense time-column cells. compact flag ignored.
//                 Timezone label intentionally suppressed (cell is too narrow).
//   - short-date: 3-letter weekday + day + 3-letter month + year,
//                 e.g. "Sun 3 May 2026". Used in BoardHeader date cluster.
//                 Compact form omits year: "Sun 3 May".
//                 Timezone label intentionally suppressed (calendar dates need none).
//   All precisions still use the owner timezone via formatInTimeZone().
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

import { useEffect, useState } from "react"
import { formatInTimeZone } from "date-fns-tz"
import { useTimezone } from "@/components/ui/timezone-context"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type TimeMode = "absolute" | "relative" | "smart" | "clock-24h-mono" | "relative-compact"
export type TimePrecision = "second" | "minute" | "hour" | "day" | "weekday" | "time" | "short-date"

export interface TimeProps {
  /** The date value to render. Accepts an ISO 8601 string or a Date object. */
  value: string | Date
  /**
   * Display mode.
   *   - absolute:         full date + time + tz abbreviation
   *   - relative:         "N minutes/hours/days ago" (date-fns natural language)
   *   - smart:            relative when < 24 h old, absolute otherwise
   *   - clock-24h-mono:   "HH:MM" — live 24-hour clock in owner timezone.
   *                       Applies `tabular-nums` font-variant and `font-mono`
   *                       CSS classes for fixed-width digit rendering in headers.
   *                       Aligned to minute boundaries: an initial timeout fires
   *                       at the next whole minute, then a 60 s interval takes
   *                       over. This eliminates the up-to-59 s display lag that
   *                       a fixed interval incurs when mounted mid-minute.
   *                       Initialized immediately from `Date.now()` (the current
   *                       wall-clock time), so the first render is already accurate.
   *                       `value` is only used for the `datetime` a11y attribute.
   *                       The `precision` prop is ignored for this mode.
   *   - relative-compact: "6m ago", "2h ago", "3d ago"; "in 6m", "in 2h", "in 3d".
   *                       Seconds-precision floor: values within ±60 s render as "now".
   *                       Large past deltas: "m"/"h"/"d" suffixed with " ago".
   *                       Large future deltas: "m"/"h"/"d" prefixed with "in ".
   *                       Owner-timezone-agnostic — computed from wall-clock diff.
   *                       The `precision` and `compact` props are ignored.
   * @default "smart"
   */
  mode?: TimeMode
  /**
   * Display precision (affects absolute / smart-absolute output only).
   * Relative and relative-compact modes always use their own format logic.
   *   - second:     "May 3, 2026 at 4:42:07 PM SGT"
   *   - minute:     "May 3, 2026 at 4:42 PM SGT"  (default)
   *   - hour:       "May 3, 2026 at 4 PM SGT"
   *   - day:        "May 3, 2026 SGT"
   *   - weekday:    "Sunday, May 3, 2026"  (compact: "Sunday, May 3"; no tz label)
   *   - time:       "08:30"  (24-hour clock, time-only; compact has no effect; no tz label)
   *   - short-date: "Sun 3 May 2026"  (3-letter weekday + day + 3-letter month + year;
   *                 compact: "Sun 3 May"; no tz label). Used in BoardHeader date cluster.
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

// Note: `weekday`, `time`, and `short-date` precisions intentionally omit the
// timezone abbreviation (zzz). `weekday` renders a calendar-date heading where
// the tz label adds no value; `time` renders a compact 24-hour column cell;
// `short-date` renders a brief header date (e.g. "Sun 3 May 2026"). All three
// still consume the owner timezone via formatInTimeZone() so output is TZ-correct.
const ABSOLUTE_FORMAT: Record<TimePrecision, string> = {
  second:     "MMM d, yyyy 'at' h:mm:ss a zzz",
  minute:     "MMM d, yyyy 'at' h:mm a zzz",
  hour:       "MMM d, yyyy 'at' h a zzz",
  day:        "MMM d, yyyy zzz",
  weekday:    "EEEE, MMMM d, yyyy",
  time:       "HH:mm",
  "short-date": "EEE d MMM yyyy",
}

// Compact format omits year and timezone — used in dense table cells.
const COMPACT_FORMAT: Record<TimePrecision, string> = {
  second:     "MMM d, h:mm:ss a",
  minute:     "MMM d, h:mm a",
  hour:       "MMM d, h a",
  day:        "MMM d",
  weekday:    "EEEE, MMMM d",
  time:       "HH:mm",      // compact has no effect, same format
  "short-date": "EEE d MMM", // compact omits year: "Sun 3 May"
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
    // Inline natural-language relative format (replaces formatDistanceToNow).
    // Uses seconds-precision thresholds consistent with date-fns conventions.
    const diffMs = Date.now() - date.getTime()
    const absDiff = Math.abs(diffMs)
    const suffix = diffMs >= 0 ? " ago" : ""
    const prefix = diffMs < 0 ? "in " : ""
    const absSec = Math.floor(absDiff / 1_000)
    if (absSec < 45) return `${prefix}less than a minute${suffix}`
    if (absSec < 90) return `${prefix}about 1 minute${suffix}`
    const absMin = Math.round(absDiff / 60_000)
    if (absMin < 45) return `${prefix}${absMin} minutes${suffix}`
    if (absMin < 90) return `${prefix}about 1 hour${suffix}`
    const absHr = Math.round(absDiff / 3_600_000)
    if (absHr < 24) return `${prefix}about ${absHr} hours${suffix}`
    if (absHr < 36) return `${prefix}1 day${suffix}`
    const absDays = Math.round(absDiff / 86_400_000)
    if (absDays < 30) return `${prefix}${absDays} days${suffix}`
    if (absDays < 45) return `${prefix}about 1 month${suffix}`
    const absMonths = Math.round(absDiff / (30 * 86_400_000))
    if (absDays < 365) return `${prefix}${absMonths} months${suffix}`
    if (absDays < 548) return `${prefix}about 1 year${suffix}`
    const absYears = Math.round(absDiff / (365 * 86_400_000))
    return `${prefix}${absYears} years${suffix}`
  } catch {
    return date.toISOString()
  }
}

/**
 * Format a date as a compact relative string with a seconds-precision floor.
 * Values within ±60 s of now → "now". Otherwise uses single-letter suffixes:
 * "m", "h", "d" with an "ago" suffix for past times and an "in" prefix for
 * future times (e.g. "in 6m", "in 2h").
 *
 * Examples (past):   "now", "6m ago", "2h ago", "3d ago".
 * Examples (future): "now" (< 60 s), "in 6m", "in 2h", "in 3d".
 *
 * Owner-timezone-agnostic — computed purely from the wall-clock difference.
 * The precision and compact props have no effect on this mode.
 */
function formatRelativeCompact(date: Date): string {
  try {
    const diffMs = Date.now() - date.getTime()
    const absDiffSec = Math.floor(Math.abs(diffMs) / 1_000)
    if (absDiffSec < 60) return "now"
    const isPast = diffMs >= 0
    const absDiffMin = Math.floor(absDiffSec / 60)
    if (absDiffMin < 60) return isPast ? `${absDiffMin}m ago` : `in ${absDiffMin}m`
    const absDiffHr = Math.floor(absDiffMin / 60)
    if (absDiffHr < 24) return isPast ? `${absDiffHr}h ago` : `in ${absDiffHr}h`
    const absDiffDays = Math.floor(absDiffHr / 24)
    return isPast ? `${absDiffDays}d ago` : `in ${absDiffDays}d`
  } catch {
    return date.toISOString()
  }
}

/**
 * Format the current wall-clock time as "HH:MM" in the given IANA timezone.
 * Used by clock-24h-mono mode. Always reads Date.now() so the result is live.
 */
function formatClock24h(tz: string): string {
  try {
    return formatInTimeZone(new Date(), tz, "HH:mm")
  } catch {
    return "--:--"
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
 *
 * @example
 * // BoardHeader date cluster — "Sun 3 May 2026"
 * <Time value={new Date()} mode="absolute" precision="short-date" />
 *
 * @example
 * // BoardHeader live clock — "08:30" (updates every minute, monospace)
 * <Time value={new Date()} mode="clock-24h-mono" />
 *
 * @example
 * // StatusBoardCell KPI 'last' field — "6m ago", "2h ago", "3d ago"
 * <Time value={kpi.last_updated_at} mode="relative-compact" />
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

  // clock-24h-mono mode: live ticking clock that ignores `value` after mount.
  // A tick counter drives re-renders every 60 s; the display text is derived
  // from the current tz at render time so timezone changes also reflect
  // immediately on the next render, with no stale intermediate state.
  // On SSR (renderToStaticMarkup), useEffect is a no-op so the ticker never
  // increments — the first render's formatClock24h(tz) is used throughout,
  // which is correct for the server render time.
  //
  // Alignment: instead of a naive fixed 60 s interval (which can lag up to
  // 59 s if the component mounts mid-minute), we schedule an initial timeout
  // to the next minute boundary, then switch to a steady 60 s interval after
  // that first fire. This eliminates display lag at mount time.
  const [clockTick, setClockTick] = useState(0)
  useEffect(() => {
    if (mode !== "clock-24h-mono") return
    const msUntilNextMinute = 60_000 - (Date.now() % 60_000)
    let intervalId: ReturnType<typeof setInterval> | undefined
    const timeoutId = setTimeout(() => {
      setClockTick((t) => t + 1)
      intervalId = setInterval(() => {
        setClockTick((t) => t + 1)
      }, 60_000)
    }, msUntilNextMinute)
    return () => {
      clearTimeout(timeoutId)
      if (intervalId !== undefined) clearInterval(intervalId)
    }
  }, [mode])

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

  // clock-24h-mono: live ticking 24-hour clock in the owner timezone.
  // Text is derived from tz at render time (not from stale state) so it is
  // always in sync with both the current minute and the current tz.
  // clockTick is read here only to ensure React re-renders when the interval
  // fires — its numeric value is not used directly.
  if (mode === "clock-24h-mono") {
    void clockTick  // consumed to keep React's dependency tracking happy
    const text = formatClock24h(tz)
    // tabular-nums ensures digits are fixed-width (no layout shift as time changes).
    const clockClass = ["font-mono tabular-nums", className].filter(Boolean).join(" ")
    return (
      <time
        dateTime={isoString}
        title={showTitle ? isoString : undefined}
        className={clockClass}
      >
        {text}
      </time>
    )
  }

  let text: string
  if (mode === "relative-compact") {
    text = formatRelativeCompact(date)
  } else if (mode === "relative") {
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
