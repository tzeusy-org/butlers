/**
 * ConnectorHistogram — 24h per-hour bar chart for connector detail.
 *
 * 96px tall bars, 24 columns (one per hour, oldest left to newest right).
 * Peak bar uses full foreground; others use 60% opacity. Zero bars use the
 * border color. Hour labels below (00 / 03 / 06 / 09 / 12 / 15 / 18 / 21 / 23).
 *
 * No card chrome. One elevation. No color fills beyond foreground treatment.
 *
 * Spec: pr/overview/ingestion-redesign/ingestion-connector-detail.jsx §ConnectorHistogram
 */

interface ConnectorHistogramProps {
  /** 24-length array of hourly counts (oldest first). */
  data: number[]
  height?: number
}

const HOUR_LABELS = ['00', '03', '06', '09', '12', '15', '18', '21', '23']

/**
 * 24-column throughput histogram.
 *
 * Peak bar rendered in full foreground; others at 60% opacity.
 * Zero bars use the border color. Hour labels shown at known offsets below.
 */
export function ConnectorHistogram({ data, height = 96 }: ConnectorHistogramProps) {
  const bars = data.length === 24 ? data : Array(24).fill(0).map((_, i) => data[i] ?? 0)
  const peak = Math.max(...bars, 1)
  const peakValue = Math.max(...bars)

  const barWidth = 3
  const barGap = 1
  const totalWidth = bars.length * (barWidth + barGap) - barGap

  return (
    <div>
      <svg
        viewBox={`0 0 ${totalWidth} ${height}`}
        preserveAspectRatio="none"
        aria-hidden="true"
        style={{ width: '100%', height }}
      >
        {bars.map((v, i) => {
          const barH = Math.max(2, (v / peak) * height)
          const x = i * (barWidth + barGap)
          const y = height - barH
          const isPeak = v === peakValue && v > 0
          const isEmpty = v === 0
          return (
            <rect
              key={i}
              x={x}
              y={y}
              width={barWidth}
              height={barH}
              className={
                isEmpty
                  ? 'fill-border'
                  : isPeak
                    ? 'fill-foreground'
                    : 'fill-foreground/60'
              }
            />
          )
        })}
      </svg>
      {/* Hour labels */}
      <div className="flex justify-between mt-1.5">
        {HOUR_LABELS.map((label) => (
          <span
            key={label}
            className="font-mono text-[9.5px] tracking-[0.06em] text-muted-foreground/60"
          >
            {label}
          </span>
        ))}
      </div>
    </div>
  )
}
