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
 * Spec: (ingestion dispatch redesign, graduated) ingestion-connectors-a.jsx §Sparkline
 */

interface SparklineProps {
  /** Array of 24 hourly counts (oldest first). */
  data: number[]
  /** Override the max value. Defaults to Math.max(...data, 1). */
  maxValue?: number
  /** Height in pixels. Default 28. */
  height?: number
  /** Width in pixels. Default 100% of parent via viewBox. */
  className?: string
}

/**
 * 24-bar sparkline for hourly throughput.
 *
 * Each bar height is proportional to the peak bar. Zero bars are rendered
 * at 1px height in the muted color. Labels (00 / 12 / 24) are rendered
 * by the parent via CSS when needed.
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
      aria-hidden="true"
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
