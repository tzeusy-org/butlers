// ---------------------------------------------------------------------------
// playhead-interp — interpolate the map playhead position from raw scrubber
// time and a time-ordered list of trail samples.
//
// As the user drags the time bar, the red marker should glide between known
// OwnTracks samples instead of jumping only when a sample's timestamp is hit.
// We linearly interpolate between the two nearest samples by time.
// ---------------------------------------------------------------------------

/** A trail sample with both coordinates and the canonical occurrence time. */
export interface TimedTrailPoint {
  lng: number
  lat: number
  /** Epoch milliseconds for `canonical_occurred_at`. */
  ms: number
}

/**
 * Linearly interpolate the playhead position at `scrubberMs` along a
 * time-ordered trail.
 *
 * Returns `null` when the trail is empty.
 *
 * Outside the trail's time range we clamp to the first/last sample so the
 * marker stays put at the edges of the window rather than disappearing.
 *
 * Callers MUST pass `points` sorted ascending by `ms`. (ChroniclesPage
 * already sorts the trail by canonical_occurred_at.)
 */
export function interpolatePlayhead(
  scrubberMs: number,
  points: ReadonlyArray<TimedTrailPoint>,
): { lng: number; lat: number } | null {
  if (points.length === 0) return null
  if (points.length === 1) return { lng: points[0].lng, lat: points[0].lat }

  // Clamp to ends.
  if (scrubberMs <= points[0].ms) {
    return { lng: points[0].lng, lat: points[0].lat }
  }
  const last = points[points.length - 1]
  if (scrubberMs >= last.ms) {
    return { lng: last.lng, lat: last.lat }
  }

  // Binary search for the segment containing scrubberMs.
  let lo = 0
  let hi = points.length - 1
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (points[mid].ms <= scrubberMs) lo = mid
    else hi = mid
  }

  const a = points[lo]
  const b = points[hi]
  const span = b.ms - a.ms
  // Two samples with the same timestamp — fall back to the earlier one.
  if (span <= 0) return { lng: a.lng, lat: a.lat }
  const f = (scrubberMs - a.ms) / span
  return {
    lng: a.lng + (b.lng - a.lng) * f,
    lat: a.lat + (b.lat - a.lat) * f,
  }
}
