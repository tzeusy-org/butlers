// ---------------------------------------------------------------------------
// ActivityStripe — 24-hour session-count visualisation strip
// (bu-hb7dh.6)
//
// Renders 24 equal-width cells in a horizontal flex row. Each cell is
// colored by the session count relative to the row maximum:
//   - empty cells (count = 0):  neutral wash (bg-muted/40)
//   - filled cells (count > 0): foreground at (0.20 + count/max * 0.55) opacity
//   - unreported cells (null):  dashed outline, no fill (bu-s11n0s.5.1) — an
//     hour nobody reported is never drawn as a measured zero
//
// Inline style EXEMPTION: the intensity opacity on filled cells is a typed
// primitive owning one dynamic prop — this is explicitly exempt from the
// "no inline style" rule per design-language.md "One token system or none".
//
// Aria: role="img" with aria-label describing total sessions and peak hour
// when read-only; a labeled button group when an interactive caller supplies
// onBarClick.
// ---------------------------------------------------------------------------

interface ActivityStripeProps {
  /**
   * 24 hourly session counts, oldest first (slot 0 = oldest). Length MUST be 24.
   * `null` marks an hour no source reported (bu-s11n0s.5.1).
   */
  counts: (number | null)[]
  /**
   * UTC-aligned window end timestamp used to compute the peak-hour label.
   *
   * The slots are UTC-aligned (`hourlySlotStarts` in lib/bucket-series.ts) —
   * slot 0 is the oldest UTC-hour bucket, slot 23 the one containing the
   * window end. When `windowEnd`
   * is provided, the peak-hour label is derived from its UTC-hour floor so it
   * is consistent with the bucketing boundary. When omitted, `new Date()` is
   * used as the reference, but the label is still computed from UTC hours, so
   * the fallback is also UTC-based (not local time).
   */
  windowEnd?: Date
  /** Optional className forwarded to the container element. */
  className?: string
  /** Optional handler for a clicked hourly slot (0 = oldest, 23 = newest). */
  onBarClick?: (index: number) => void
}

/**
 * Horizontal 24-cell stripe showing hourly session density for the past 24 hours.
 *
 * @param counts - 24 hourly session counts, oldest first; null = unreported. Length MUST be 24.
 * @param windowEnd - UTC-aligned window end for peak-hour label computation.
 *   Pass the same `nowMs` reference used by `hourlySlotStarts` for alignment.
 *   Defaults to `new Date()` when omitted.
 * @param className - Optional className forwarded to the container element.
 * @param onBarClick - Optional click handler that makes each slot a keyboard-accessible button.
 *
 * @example
 *   <ActivityStripe counts={row.hourlyStripe} windowEnd={stripeEndAt} />
 */
export function ActivityStripe({ counts, windowEnd, className, onBarClick }: ActivityStripeProps) {
  if (import.meta.env.DEV && counts.length !== 24) {
    console.error(`ActivityStripe: counts.length must be 24, got ${counts.length}`)
  }

  const reported = counts.filter((count): count is number => count !== null)
  const unreported = counts.length - reported.length
  const max = Math.max(...reported, 0)
  const total = reported.reduce((s, n) => s + n, 0)

  // Determine peak hour label (0-23, oldest-first slot index maps to HH:00 UTC).
  // Slot 23 corresponds to utcHourFloor(windowEnd); slot peakIdx is (23 - peakIdx)
  // hours earlier. We use UTC hours to stay aligned with the bucketing utility.
  const peakIdx = counts.indexOf(max)
  const referenceDate = windowEnd ?? new Date()
  const slot23UTCHour = referenceDate.getUTCHours()
  const slotLabel = (index: number) =>
    `${String((slot23UTCHour - 23 + index + 24) % 24).padStart(2, "0")}:00 UTC`

  const unreportedSuffix = unreported > 0 ? `, ${unreported}h not reported` : ""
  const ariaLabel = reported.length === 0
    ? "24-hour activity not reported"
    : `24-hour activity, total ${total} sessions, peak ${max} at ${slotLabel(peakIdx)}${unreportedSuffix}`
  const interactiveBarClassName = onBarClick
    ? "min-h-6 min-w-6 shrink-0 cursor-pointer p-0 transition-opacity hover:opacity-80 focus-visible:outline focus-visible:outline-1 focus-visible:outline-focus focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2"
    : ""

  function slotAriaLabel(index: number, count: number | null): string {
    if (count === null) return `not reported, ${slotLabel(index)}`
    return `${count} session${count === 1 ? "" : "s"}, ${slotLabel(index)}`
  }

  return (
    <div
      role={onBarClick ? "group" : "img"}
      aria-label={ariaLabel}
      className={[
        "flex gap-px",
        onBarClick ? "h-6 overflow-x-auto" : "h-[22px]",
        className,
      ].filter(Boolean).join(" ")}
    >
      {counts.map((count, i) => {
        if (count === null) {
          const className = "flex-1 rounded-[1px] border border-dashed border-border"
          if (onBarClick) {
            return (
              <button
                key={i}
                type="button"
                data-slot="unreported"
                onClick={() => onBarClick(i)}
                aria-label={slotAriaLabel(i, count)}
                className={`${className} ${interactiveBarClassName}`}
              />
            )
          }
          return <div key={i} data-slot="unreported" className={className} />
        }
        const isEmpty = count === 0 || max === 0
        if (isEmpty) {
          if (onBarClick) {
            return (
              <button
                key={i}
                type="button"
                onClick={() => onBarClick(i)}
                aria-label={slotAriaLabel(i, count)}
                className={`flex-1 rounded-[1px] border-0 bg-muted/40 ${interactiveBarClassName}`}
              />
            )
          }
          return (
            <div
              key={i}
              className="flex-1 rounded-[1px] bg-muted/40"
            />
          )
        }
        // Typed primitive exemption: intensity is a single dynamic CSS prop.
        const intensity = 0.20 + (count / max) * 0.55
        if (onBarClick) {
          return (
            <button
              key={i}
              type="button"
              onClick={() => onBarClick(i)}
              aria-label={slotAriaLabel(i, count)}
              className={`flex-1 rounded-[1px] border-0 ${interactiveBarClassName}`}
              style={{ backgroundColor: `color-mix(in oklch, var(--foreground) ${Math.round(intensity * 100)}%, transparent)` }}
            />
          )
        }
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
