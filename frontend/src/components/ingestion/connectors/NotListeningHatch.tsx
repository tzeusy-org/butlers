/**
 * Hatched "not listening" cell for count strips (bu-s11n0s.5).
 *
 * A deaf bucket (no heartbeat recorded from the endpoint) is drawn as a
 * diagonal-hatched full-height cell, so it differs in shape, not just shade,
 * from a measured-zero bar. Both the roster Sparkline and the detail
 * ConnectorHistogram use it, with an id from `useHatchPatternId`.
 */

/** The hatch pattern definition; render once inside each `<svg>`. */
export function NotListeningPattern({ id }: { id: string }) {
  return (
    <defs>
      <pattern
        id={id}
        width={3}
        height={3}
        patternUnits="userSpaceOnUse"
        patternTransform="rotate(45)"
      >
        <line x1={0} y1={0} x2={0} y2={3} strokeWidth={1} className="stroke-muted-foreground/50" />
      </pattern>
    </defs>
  )
}

/** One full-height hatched cell at column `x`. */
export function NotListeningCell({
  x,
  width,
  height,
  patternId,
}: {
  x: number
  width: number
  height: number
  patternId: string
}) {
  return (
    <rect data-hatch="" x={x} y={0} width={width} height={height} fill={`url(#${patternId})`} />
  )
}
