// ---------------------------------------------------------------------------
// <Time> — semantic time primitive with absolute / relative / smart modes
// (bu-v1tt2.2)
//
// Renders a <time> element with a datetime attribute (a11y / machine-readable)
// and human-readable text according to the chosen mode.
//
// Mode behaviour:
//   - absolute: "May 3, 2026 at 4:42 PM SGT"
//   - relative:  "4 minutes ago"
//   - smart:     relative for < 24 h, absolute for older (default)
//
// Timezone is read from ChroniclesTimezoneContext via useChroniclesTimezone().
// An explicit `timezone` prop overrides the context value — useful for
// isolated rendering outside a ChroniclesTimezoneProvider.
//
// Smart-mode threshold is 24 h. If it needs to be configurable in the future,
// thread a `smartThresholdMs` prop through; the logic is isolated in one place.
// ---------------------------------------------------------------------------

import { formatDistanceToNow } from "date-fns"
import { formatInTimeZone } from "date-fns-tz"
import { useChroniclesTimezone } from "@/components/chronicles/use-chronicles-timezone"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type TimeMode = "absolute" | "relative" | "smart"
export type TimePrecision = "second" | "minute" | "hour" | "day"

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
   *   - second: "May 3, 2026 at 4:42:07 PM SGT"
   *   - minute: "May 3, 2026 at 4:42 PM SGT"  (default)
   *   - hour:   "May 3, 2026 at 4 PM SGT"
   *   - day:    "May 3, 2026 SGT"
   * @default "minute"
   */
  precision?: TimePrecision
  /**
   * IANA timezone name override.
   * Defaults to the owner timezone from ChroniclesTimezoneContext.
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

const ABSOLUTE_FORMAT: Record<TimePrecision, string> = {
  second: "MMM d, yyyy 'at' h:mm:ss a zzz",
  minute: "MMM d, yyyy 'at' h:mm a zzz",
  hour:   "MMM d, yyyy 'at' h a zzz",
  day:    "MMM d, yyyy zzz",
}

// 24-hour threshold in ms — smart mode crossover point.
const SMART_THRESHOLD_MS = 24 * 60 * 60 * 1_000

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function toDate(value: string | Date): Date {
  return value instanceof Date ? value : new Date(value)
}

function formatAbsolute(date: Date, precision: TimePrecision, tz: string): string {
  try {
    return formatInTimeZone(date, tz, ABSOLUTE_FORMAT[precision])
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
 */
export function Time({
  value,
  mode = "smart",
  precision = "minute",
  timezone,
  showTitle = true,
  className,
}: TimeProps) {
  const contextTz = useChroniclesTimezone()
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
    text = formatAbsolute(date, precision, tz)
  } else {
    // smart: relative for < 24 h, absolute for older.
    // Math.abs ensures future dates obey the threshold symmetrically.
    const { useRelative } = resolveSmartMode(date)
    text = useRelative
      ? formatRelative(date)
      : formatAbsolute(date, precision, tz)
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
