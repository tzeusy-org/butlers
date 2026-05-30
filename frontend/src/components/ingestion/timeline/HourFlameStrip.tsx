/**
 * HourFlameStrip — 1-px-wide per-minute event density bar for an hour group.
 *
 * Renders 60 thin bars (one per minute), height proportional to the number of
 * events that arrived in that minute. Uses the same SVG bar pattern as
 * Sparkline.tsx in the connectors roster.
 *
 * The strip is always 60px wide (1px bar, no gaps) so the timeline column
 * width stays stable.
 *
 * Per-minute counts are computed externally via deriveMinuteCounts (see
 * ./deriveMinuteCounts.ts) so this component stays pure.
 *
 * Spec: pr/overview/ingestion-redesign/INGESTION_HANDOFF.md §"Ledger" (inline flame strip)
 * Design: hairline bar fill, foreground at 40% opacity, border-color for zero bars.
 */

interface HourFlameStripProps {
  /**
   * Array of 60 per-minute event counts (minute 0 = oldest, minute 59 = newest).
   * Short arrays are right-padded with zeros. Long arrays are truncated at 60.
   */
  minuteCounts: number[]
  /** Override the peak value used for scaling. Defaults to max of minuteCounts. */
  maxValue?: number
  /** Height in pixels. Default 16. */
  height?: number
  /** Additional Tailwind classes. */
  className?: string
}

/**
 * 60-bar per-minute density sparkline for an hour group.
 *
 * Each bar is 1px wide with no gap. Zero-count minutes render at 1px
 * height in the border color. Non-zero bars use foreground at 40% opacity.
 */
export function HourFlameStrip({
  minuteCounts,
  maxValue,
  height = 16,
  className,
}: HourFlameStripProps) {
  // Normalise to exactly 60 minutes
  const bars: number[] = Array(60).fill(0)
  for (let i = 0; i < Math.min(minuteCounts.length, 60); i++) {
    bars[i] = minuteCounts[i] ?? 0
  }

  const peak = maxValue ?? Math.max(...bars, 1)
  const totalWidth = 60 // 1px per bar, no gaps

  return (
    <svg
      viewBox={`0 0 ${totalWidth} ${height}`}
      preserveAspectRatio="none"
      aria-hidden="true"
      className={className}
      style={{ width: totalWidth, height }}
    >
      {bars.map((v, i) => {
        const barH = Math.max(1, (v / peak) * height)
        const x = i
        const y = height - barH
        const isEmpty = v === 0
        return (
          <rect
            key={i}
            x={x}
            y={y}
            width={1}
            height={barH}
            className={isEmpty ? 'fill-border' : 'fill-foreground/40'}
          />
        )
      })}
    </svg>
  )
}
