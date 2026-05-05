// ---------------------------------------------------------------------------
// Scrubber utilities — extracted so the Scrubber component file only exports
// React components (required for react-refresh fast-refresh).
// ---------------------------------------------------------------------------

/**
 * Snap `valueMs` to the nearest timestamp in the snapMs array.
 * Returns null if snapMs is empty.
 *
 * Exported for direct unit testing.
 */
export function snapToNearest(valueMs: number, snapMs: number[]): number | null {
  if (snapMs.length === 0) return null

  let best = snapMs[0]
  let bestDelta = Math.abs(snapMs[0] - valueMs)

  for (let i = 1; i < snapMs.length; i++) {
    const delta = Math.abs(snapMs[i] - valueMs)
    if (delta < bestDelta) {
      bestDelta = delta
      best = snapMs[i]
    }
  }

  return best
}
