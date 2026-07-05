// ---------------------------------------------------------------------------
// Spend movers -- ranked butler spend deltas vs a prior window of equal
// length. Extracted from SpendPage's "What changed" Movers strip so both the
// strip and the SpendVerdictOpener page opener (bu-qvnce.9, JARVIS pursuit
// move 9) read the exact same honest-delta logic instead of duplicating it.
//
// A butler with prior=0 is "new"; a butler with current=0 is "stopped" --
// both are honest deltas, not fabricated ones. A butler whose cost data was
// unavailable on either side of the comparison is excluded entirely: a
// current-vs-0 or 0-vs-prior comparison against unknown data would fabricate
// a "+$X · new" or "· stopped" callout that isn't real (bu-qvnce.1 -- honest
// aggregation).
// ---------------------------------------------------------------------------

export interface Mover {
  name: string
  current: number
  prior: number
  delta: number
}

export function computeMovers(
  current: Record<string, number>,
  prior: Record<string, number>,
  unavailable: ReadonlySet<string>,
  limit = 6,
): Mover[] {
  const names = new Set([...Object.keys(current), ...Object.keys(prior)])
  const movers: Mover[] = Array.from(names)
    // A butler whose cost data was unavailable on either side of the
    // comparison has an unreliable delta -- see doctrine above.
    .filter((name) => !unavailable.has(name))
    .map((name) => {
      const c = current[name] ?? 0
      const p = prior[name] ?? 0
      return { name, current: c, prior: p, delta: c - p }
    })
  movers.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
  return movers.filter((m) => Math.abs(m.delta) >= 0.000001).slice(0, limit)
}
