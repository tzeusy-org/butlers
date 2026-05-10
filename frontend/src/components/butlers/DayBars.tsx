// ---------------------------------------------------------------------------
// DayBars — daily-count bar series companion to ActivityStripe
// (bu-iuol4.15)
//
// Renders N vertical bars in a horizontal flex row sized by max value in
// the series. Intended for 7-day or 30-day windows (range !== '24h').
// Accepts any N.
//
// Inline style EXEMPTION: bar height is a typed primitive owning one dynamic
// CSS prop (height as a percentage of the container) — explicitly exempt from
// the "no inline style" rule per design-language.md Non-negotiable 1.b
// ("A <Progress> whose internal style={{ width: '42%' }} is unavoidable does
// not violate the rule").
//
// Color prop contract:
//   The optional `color` prop accepts a Tailwind background-color utility
//   class name (e.g. "bg-primary", "bg-chart-1", "bg-muted-foreground").
//   Only token-based utilities are permitted — no hex, no oklch literals.
//   Defaults to "bg-foreground/60" when omitted.
//
// Aria: role="img" with aria-label describing total and max.
// ---------------------------------------------------------------------------

interface DayBarsProps {
  /** Daily session / spend counts, one per day, oldest first. Any length. */
  data: number[]
  /** Container height in pixels. Defaults to 32. */
  height?: number
  /**
   * Tailwind background-color utility class applied to filled bars.
   * Must be a token-based utility (e.g. "bg-primary", "bg-chart-2").
   * Defaults to "bg-foreground/60".
   */
  color?: string
  /** Optional className forwarded to the container element. */
  className?: string
}

/**
 * Horizontal N-bar series showing daily counts sized relative to the max.
 *
 * @param data   - Daily counts, oldest first. Any length (7 and 30 are typical).
 * @param height - Container height in px. Defaults to 32.
 * @param color  - Tailwind bg-* utility for filled bars. Token-only.
 *
 * @example
 *   <DayBars data={row.dailyCounts7d} />
 *   <DayBars data={row.dailyCounts30d} height={24} color="bg-chart-1" />
 */
export function DayBars({ data, height = 32, color = "bg-foreground/60", className }: DayBarsProps) {
  if (data.length === 0) {
    return null
  }

  const max = data.reduce((a, b) => Math.max(a, b), 0)
  const total = data.reduce((s, n) => s + n, 0)

  const ariaLabel = `${data.length}-day activity, total ${total}, peak ${max}`

  return (
    <div
      role="img"
      aria-label={ariaLabel}
      // Typed primitive exemption: height is a single dynamic CSS prop.
      style={{ height }}
      className={["flex items-end gap-px w-full", className].filter(Boolean).join(" ")}
    >
      {data.map((count, i) => {
        if (max === 0) {
          // All-zero: render a minimal visible bar so the row has presence.
          return (
            <div
              key={i}
              className="flex-1 rounded-[1px] bg-muted/40 h-px"
            />
          )
        }
        const pct = Math.max((count / max) * 100, count > 0 ? 4 : 2)
        return (
          <div
            key={i}
            className={`flex-1 rounded-[1px] ${color}`}
            // Typed primitive exemption: bar height is a single dynamic CSS prop.
            style={{ height: `${pct}%` }}
          />
        )
      })}
    </div>
  )
}
