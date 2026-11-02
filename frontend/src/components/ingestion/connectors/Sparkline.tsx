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
 * An optional `secondaryData` series (bu-scyro) renders a visually-quiet
 * skip/filtered-volume overlay: a thin low-opacity tick hanging DOWN from the
 * ceiling of each column (mirrored against the primary bar, which grows up
 * from the baseline), normalized against its own peak — not the primary
 * series' peak — and capped at a small fraction of the row height so a
 * connector that is mostly-filtered never dwarfs the ingested bar in the same
 * column. It is a distinct signal, never stacked additively onto the primary
 * bar — this is "skip volume happened here too", not "more events".
 *
 * Accessibility: the SVG itself stays `aria-hidden` (it is a redundant visual
 * encoding of `data`), but the wrapping element carries an `aria-label` with
 * the numeric fallback — 24h total and peak hour — so the information is not
 * lost to assistive tech. When `maxValue` is omitted, bars normalize to this
 * row's own peak (not comparable across rows); pass the roster-wide peak as
 * `maxValue` to make bar heights comparable across connectors.
 *
 * An optional `listening` series (bu-s11n0s.5) hatches each `deaf` slot (no
 * heartbeat recorded) so "not listening" differs in shape from a measured
 * zero, and the aria-label names the deaf hours ("not listening 3h").
 *
 * Spec: (ingestion dispatch redesign, graduated) ingestion-connectors-a.jsx §Sparkline
 */

import type { ListeningState } from '@/api/types'
import { describeNotListening } from '@/lib/bucket-series'
import { useHatchPatternId } from '@/hooks/use-hatch-pattern-id'
import { NotListeningCell, NotListeningPattern } from './NotListeningHatch'

interface SparklineProps {
  /** Array of 24 hourly counts (oldest first, most recent last). */
  data: number[]
  /**
   * Optional DISTINCT quiet second series (bu-scyro) — e.g. filtered/skip-routed
   * volume. Rendered as a thin muted tick below each primary bar, never summed
   * into `data`. Absent or all-zero renders no overlay at all.
   */
  secondaryData?: number[]
  /** Optional per-slot listening state aligned with `data`; `deaf` slots are hatched. */
  listening?: ListeningState[]
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

/** Build the aria-label numeric fallback: 24h total + peak hour (+ filtered total). */
function describeSpark(
  bars: number[],
  secondaryBars?: number[],
  listening?: ListeningState[],
): string {
  const total = bars.reduce((sum, v) => sum + v, 0)
  const filteredTotal = secondaryBars?.reduce((sum, v) => sum + v, 0) ?? 0
  const filteredSuffix = filteredTotal > 0 ? ` (${filteredTotal} filtered)` : ''
  const notListening = describeNotListening(listening)
  const listeningSuffix = notListening ? `, ${notListening}` : ''
  if (total === 0) return `24h activity: no events${filteredSuffix}${listeningSuffix}`
  const peakIndex = bars.reduce((best, v, i) => (v > bars[best] ? i : best), 0)
  const hoursAgo = bars.length - 1 - peakIndex
  const peakWhen = hoursAgo === 0 ? 'in the last hour' : `${hoursAgo}h ago`
  return `24h activity: ${total} events total, peak ${peakWhen}${filteredSuffix}${listeningSuffix}`
}

/**
 * 24-bar sparkline for hourly throughput.
 *
 * Each bar height is proportional to `maxValue` (defaults to this row's own
 * peak). Zero bars are rendered at 1px height in the muted color. Labels
 * (00 / 12 / 24) are rendered by the parent via CSS when needed.
 */
export function Sparkline({
  data,
  secondaryData,
  listening,
  maxValue,
  height = 28,
  className,
}: SparklineProps) {
  const patternId = useHatchPatternId()
  const hasDeaf = listening?.includes('deaf') ?? false
  const bars = data.length > 0 ? data : Array(24).fill(0)
  const peak = maxValue ?? Math.max(...bars, 1)
  const secondaryBars = secondaryData && secondaryData.length > 0 ? secondaryData : undefined
  const secondaryPeak = secondaryBars ? Math.max(...secondaryBars, 1) : 1
  // Quiet overlay caps at a small fraction of the row height so it never
  // competes visually with the primary series.
  const secondaryMaxHeight = height * 0.3

  const barWidth = 3
  const barGap = 1
  const totalWidth = bars.length * (barWidth + barGap) - barGap

  return (
    <svg
      viewBox={`0 0 ${totalWidth} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={describeSpark(bars, secondaryBars, listening)}
      className={className}
      style={{ width: '100%', height }}
    >
      {hasDeaf && <NotListeningPattern id={patternId} />}
      {bars.map((v, i) => {
        const barH = Math.max(1, (v / peak) * height)
        const x = i * (barWidth + barGap)
        const y = height - barH
        const isEmpty = v === 0
        const secondaryV = secondaryBars?.[i] ?? 0
        const secondaryH = secondaryV > 0 ? Math.max(1, (secondaryV / secondaryPeak) * secondaryMaxHeight) : 0
        const isDeaf = listening?.[i] === 'deaf'
        return (
          <g key={i} data-slot={isDeaf ? 'deaf' : undefined}>
            {isDeaf && (
              <NotListeningCell x={x} width={barWidth} height={height} patternId={patternId} />
            )}
            {(!isDeaf || !isEmpty) && (
              <rect
                x={x}
                y={y}
                width={barWidth}
                height={barH}
                rx={0.5}
                className={isEmpty ? 'fill-border' : 'fill-foreground/60'}
              />
            )}
            {secondaryH > 0 && (
              <rect
                aria-hidden="true"
                x={x}
                y={0}
                width={barWidth}
                height={secondaryH}
                rx={0.5}
                className="fill-muted-foreground/25"
              />
            )}
          </g>
        )
      })}
    </svg>
  )
}
