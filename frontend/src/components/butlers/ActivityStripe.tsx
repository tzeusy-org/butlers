// ---------------------------------------------------------------------------
// ActivityStripe — 24-hour session-count visualisation strip
// (bu-hb7dh.6)
//
// Renders 24 equal-width cells in a horizontal flex row. Each cell is
// colored by the session count relative to the row maximum:
//   - empty cells (count = 0):  neutral wash (bg-muted/40)
//   - filled cells (count > 0): foreground at (0.20 + count/max * 0.55) opacity
//
// Inline style EXEMPTION: the intensity opacity on filled cells is a typed
// primitive owning one dynamic prop — this is explicitly exempt from the
// "no inline style" rule per design-language.md "One token system or none".
//
// Aria: role="img" with aria-label describing total sessions and peak hour.
// ---------------------------------------------------------------------------

interface ActivityStripeProps {
  /** 24 hourly session counts, oldest first (slot 0 = oldest). Length MUST be 24. */
  counts: number[]
  /**
   * UTC-aligned window end timestamp used to compute the peak-hour label.
   *
   * The slots produced by `bucketSessionsByHour` are UTC-aligned — slot 0 is
   * the oldest UTC-hour bucket, slot 23 is the most recent. When `windowEnd`
   * is provided, the peak-hour label is derived from its UTC-hour floor so it
   * is consistent with the bucketing boundary. When omitted, the local clock
   * (`new Date()`) is used as a fallback, which is correct for users in UTC
   * but may diverge by one hour for users in other timezones.
   */
  windowEnd?: Date
  /** Optional className forwarded to the container element. */
  className?: string
}

/**
 * Horizontal 24-cell stripe showing hourly session density for the past 24 hours.
 *
 * @param counts - 24 hourly session counts, oldest first. Length MUST be 24.
 * @param windowEnd - UTC-aligned window end for peak-hour label computation.
 *   Pass the same `endAt` reference used by `bucketSessionsByHour` for exact
 *   timezone alignment. Defaults to `new Date()` when omitted.
 * @param className - Optional className forwarded to the container element.
 *
 * @example
 *   <ActivityStripe counts={row.hourlyStripe} windowEnd={stripeEndAt} />
 */
export function ActivityStripe({ counts, windowEnd, className }: ActivityStripeProps) {
  if (import.meta.env.DEV && counts.length !== 24) {
    console.error(`ActivityStripe: counts.length must be 24, got ${counts.length}`)
  }

  const max = Math.max(...counts, 0)
  const total = counts.reduce((s, n) => s + n, 0)

  // Determine peak hour label (0-23, oldest-first slot index maps to HH:00 UTC).
  // Slot 23 corresponds to utcHourFloor(windowEnd); slot peakIdx is (23 - peakIdx)
  // hours earlier. We use UTC hours to stay aligned with the bucketing utility.
  const peakIdx = counts.indexOf(Math.max(...counts))
  const referenceDate = windowEnd ?? new Date()
  const slot23UTCHour = referenceDate.getUTCHours()
  const peakHour = (slot23UTCHour - 23 + peakIdx + 24) % 24
  const peakLabel = String(peakHour).padStart(2, "0")

  const ariaLabel = `24-hour activity, total ${total} sessions, peak ${Math.max(...counts)} at ${peakLabel}:00`

  return (
    <div
      role="img"
      aria-label={ariaLabel}
      className={["flex gap-px h-[22px]", className].filter(Boolean).join(" ")}
    >
      {counts.map((count, i) => {
        const isEmpty = count === 0 || max === 0
        if (isEmpty) {
          return (
            <div
              key={i}
              className="flex-1 rounded-[1px] bg-muted/40"
            />
          )
        }
        // Typed primitive exemption: intensity is a single dynamic CSS prop.
        const intensity = 0.20 + (count / max) * 0.55
        return (
          <div
            key={i}
            className="flex-1 rounded-[1px]"
            style={{ backgroundColor: `color-mix(in oklch, var(--foreground) ${Math.round(intensity * 100)}%, transparent)` }}
          />
        )
      })}
    </div>
  )
}
