/**
 * ConnectorHistogram — 24h per-hour bar chart for connector detail.
 *
 * 96px tall bars, 24 columns (one per hour, oldest left to newest right).
 * Peak bar uses full foreground; others use 60% opacity. Zero bars use the
 * border color. Hour labels below (00 / 03 / 06 / 09 / 12 / 15 / 18 / 21 / 23).
 *
 * All-zero windows show an explicit "no throughput recorded" empty state
 * instead of a misleading min-height baseline.
 *
 * No card chrome. One elevation. No color fills beyond foreground treatment.
 *
 * Spec: docs/redesigns/ingestion-connector-detail.jsx §ConnectorHistogram
 */

interface ConnectorHistogramProps {
  /** 24-length array of hourly ingested counts (oldest first). */
  data: number[]
  /**
   * Optional DISTINCT skip/filtered-volume series (bu-c48im), reusing the
   * roster Sparkline's secondary-overlay vocabulary: a thin low-opacity tick
   * hanging DOWN from the ceiling of each column, normalized against its OWN
   * peak (not the ingested peak) and capped at 30% of the row height so a
   * mostly-filtered connector never dwarfs the ingested bar. It is never summed
   * into `data` — this is "skip volume happened here too", not "more events".
   * Absent or all-zero renders no overlay.
   */
  secondaryData?: number[]
  height?: number
}

const HOUR_LABELS = ['00', '03', '06', '09', '12', '15', '18', '21', '23']

/**
 * 24-column throughput histogram.
 *
 * Peak bar rendered in full foreground; others at 60% opacity.
 * Zero bars use the border color. Hour labels shown at known offsets below.
 * When all buckets are zero, renders an explicit "no throughput recorded" state.
 *
 * An optional `secondaryData` (filtered/skip volume) overlay is drawn as a quiet
 * tick hanging down from the top of each column — a distinct signal, never
 * stacked additively onto the primary ingested bars.
 */
export function ConnectorHistogram({ data, secondaryData, height = 96 }: ConnectorHistogramProps) {
  const bars = data.length === 24 ? data : Array(24).fill(0).map((_, i) => data[i] ?? 0)
  const peakValue = Math.max(...bars)

  // Quiet filtered overlay: own-peak normalization + 30% height cap (mirrors the
  // roster Sparkline). Absent or all-zero (peak 0) disables the overlay entirely.
  const secondaryBars =
    secondaryData && secondaryData.length > 0 ? secondaryData : undefined
  const secondaryPeak = secondaryBars ? Math.max(...secondaryBars, 1) : 1
  const secondaryMaxHeight = height * 0.3

  const hasSecondaryVolume = secondaryBars ? secondaryBars.some((v) => v > 0) : false

  const barWidth = 3
  const barGap = 1
  const totalWidth = bars.length * (barWidth + barGap) - barGap

  // All-zero window: no data recorded — render explicit empty state rather
  // than a row of min-height baseline bars that look like real (near-zero) data.
  // A connector that is 100% skip-routed has zero ingested but real filtered
  // volume (bu-c48im) — do NOT collapse to the empty state in that case, or the
  // skip volume the histogram exists to surface would be hidden.
  if (peakValue === 0 && !hasSecondaryVolume) {
    return (
      <div data-testid="histogram-empty">
        <div
          style={{ height }}
          className="flex items-center justify-center border border-dashed border-border rounded-sm"
        >
          <span className="font-mono text-[9px] tracking-[0.12em] uppercase text-muted-foreground/40">
            no throughput recorded
          </span>
        </div>
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

  // Guard against divide-by-zero: peakValue can be 0 here when the connector is
  // 100% skip-routed (zero ingested, non-zero filtered overlay).
  const peak = Math.max(peakValue, 1)

  return (
    <div data-testid="histogram-bars" data-has-filtered={hasSecondaryVolume ? 'true' : undefined}>
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
          const secondaryV = secondaryBars?.[i] ?? 0
          const secondaryH =
            secondaryV > 0 ? Math.max(1, (secondaryV / secondaryPeak) * secondaryMaxHeight) : 0
          return (
            <g key={i}>
              <rect
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
              {secondaryH > 0 && (
                <rect
                  x={x}
                  y={0}
                  width={barWidth}
                  height={secondaryH}
                  className="fill-muted-foreground/25"
                />
              )}
            </g>
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
