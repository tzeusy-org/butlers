/**
 * GeoJSON builder for the OwnTracks trail layer (bu-ig72b.35).
 *
 * Separated from MapWidgetInner.tsx so non-component exports do not
 * interfere with React Fast Refresh (react-refresh/only-export-components).
 */

/**
 * Whether a point event's payload carries the Chronicler's impossible-jump
 * marker (bu-2tsgqb.6). The projection keeps such fixes as evidence but flags
 * them with the same policy it uses for episodes, so the trail must skip them.
 */
export function isTrailOutlier(payload: Record<string, unknown>): boolean {
  const marker = payload.trail_outlier
  return typeof marker === "object" && marker !== null
}

/**
 * Build a GeoJSON FeatureCollection containing one LineString feature
 * that connects the provided trail points in order.
 *
 * Returns an empty FeatureCollection when fewer than 2 points are given —
 * a GeoJSON LineString requires at least 2 coordinate pairs.
 */
export function buildTrailGeoJSON(
  trail: Array<{ lng: number; lat: number }>,
): GeoJSON.FeatureCollection {
  if (trail.length < 2) {
    return { type: "FeatureCollection", features: [] }
  }
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: trail.map((p) => [p.lng, p.lat]),
        },
        properties: {},
      },
    ],
  }
}
