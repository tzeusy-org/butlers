/**
 * Sparkline — 24-bar mini histogram for connector 24h throughput.
 *
 * Renders bars proportional to the peak value. Zero bars use a muted border
 * color; non-zero bars use the foreground at reduced opacity. Uses SVG for
 * precision without relying on display flex height tricks.
 *
 * Design: no card chrome, foreground bars only (no colored fills except for
 * zero/empty state). Consistent with Dispatch visual language.
 *
 * Accessibility: the SVG itself stays `aria-hidden` (it is a redundant visual
 * encoding of `data`), but the wrapping element carries an `aria-label` with
 * the numeric fallback — 24h total and peak hour — so the information is not
 * lost to assistive tech. When `maxValue` is omitted, bars normalize to this
 * row's own peak (not comparable across rows); pass the roster-wide peak as
 * `maxValue` to make bar heights comparable across connectors.
 *
 * Spec: (ingestion dispatch redesign, graduated) ingestion-connectors-a.jsx §Sparkline
 */

interface SparklineProps {
  /** Array of 24 hourly counts (oldest first, most recent last). */
  data: number[]
  /**
   * Override the normalization peak. Defaults to this row's own
   * `Math.max(...data, 1)` — pass a shared value (e.g. the roster-wide peak)
   * to make bar heights comparable across rows.
   */
  maxValue?: number
  /** Height in pixels. Default 28. */
  height?: number
  /** Width in pixels. Default 100% of parent via viewBox. */
  className?: string
}

/** Build the aria-label numeric fallback: 24h total + peak hour. */
function describeSpark(bars: number[]): string {
  const total = bars.reduce((sum, v) => sum + v, 0)
  if (total === 0) return '24h activity: no events'
  const peakIndex = bars.reduce((best, v, i) => (v > bars[best] ? i : best), 0)
  const hoursAgo = bars.length - 1 - peakIndex
  const peakWhen = hoursAgo === 0 ? 'in the last hour' : `${hoursAgo}h ago`
  return `24h activity: ${total} events total, peak ${peakWhen}`
}

/**
 * 24-bar sparkline for hourly throughput.
 *
 * Each bar height is proportional to `maxValue` (defaults to this row's own
 * peak). Zero bars are rendered at 1px height in the muted color. Labels
 * (00 / 12 / 24) are rendered by the parent via CSS when needed.
 */
export function Sparkline({ data, maxValue, height = 28, className }: SparklineProps) {
  const bars = data.length > 0 ? data : Array(24).fill(0)
  const peak = maxValue ?? Math.max(...bars, 1)

  const barWidth = 3
  const barGap = 1
  const totalWidth = bars.length * (barWidth + barGap) - barGap

  return (
    <svg
      viewBox={`0 0 ${totalWidth} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={describeSpark(bars)}
      className={className}
      style={{ width: '100%', height }}
    >
      {bars.map((v, i) => {
        const barH = Math.max(1, (v / peak) * height)
        const x = i * (barWidth + barGap)
        const y = height - barH
        const isEmpty = v === 0
        return (
          <rect
            key={i}
            x={x}
            y={y}
            width={barWidth}
            height={barH}
            rx={0.5}
            className={isEmpty ? 'fill-border' : 'fill-foreground/60'}
          />
        )
      })}
    </svg>
  )
}
