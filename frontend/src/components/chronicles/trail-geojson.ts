/**
 * GeoJSON builder for the OwnTracks trail layer (bu-ig72b.35).
 *
 * Separated from MapWidgetInner.tsx so non-component exports do not
 * interfere with React Fast Refresh (react-refresh/only-export-components).
 */

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
