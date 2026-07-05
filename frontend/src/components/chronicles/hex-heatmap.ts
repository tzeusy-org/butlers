// ---------------------------------------------------------------------------
// hex-heatmap — H3 hexagonal density binning for the Chronicles map.
//
// Uses Uber's H3 library to bin trail points into hexagonal cells, then emits
// a GeoJSON FeatureCollection that MapLibre can render via a single
// GPU-accelerated fill layer.
//
// Performance notes:
//   - Binning is O(N) over trail points; cellToBoundary is O(C) over unique
//     cells. Both are cheap (typical N ≤ 500, C ≤ a few dozen).
//   - The result is memoised on trailPoints in MapWidgetInner so scrubbing
//     does not recompute the heatmap.
//   - Rendered as a single FillLayer — MapLibre uploads the geometry to the
//     GPU once and pans/zooms without per-frame JS work.
//
// Cell fill color: bu-qvnce.7 moved this off a bespoke red->amber->green
// ramp (borrowed from health/severity signaling — density/visit-frequency is
// not a state signal) onto chart-colors.ts's neutralDensityColor(), the
// shared achromatic neutral-density-ramp channel.
// ---------------------------------------------------------------------------

import { latLngToCell, cellToBoundary } from "h3-js"

import { neutralDensityColor } from "@/lib/chart-colors"

/**
 * H3 resolution. 8 ≈ 460 m edge length — a sensible default for personal
 * location traces (city-block scale). Higher = finer cells.
 *
 * Reference: https://h3geo.org/docs/core-library/restable
 */
export const HEX_RESOLUTION = 8

/** Per-cell properties carried inside each hex Feature. */
export interface HexCellProperties {
  /** H3 cell index (string form). */
  cell: string
  /** Raw point count inside the cell. */
  count: number
  /** Normalized intensity in [0,1] (count / maxCount). */
  intensity: number
  /** Pre-computed fill color for the layer's data-driven paint. */
  color: string
}

export type HexFeatureCollection = GeoJSON.FeatureCollection<
  GeoJSON.Polygon,
  HexCellProperties
>

/**
 * Build a GeoJSON FeatureCollection of hexagonal density cells from a list of
 * trail points (already filtered, sensitive points excluded).
 *
 * - Empty input → empty FeatureCollection.
 * - Cells with very low intensity (< minIntensity) are dropped to keep the
 *   layer visually clean and avoid drawing a sea of barely-coloured hexes.
 *
 * @param points  Array of {lng, lat} trail points.
 * @param resolution  H3 resolution (default {@link HEX_RESOLUTION}).
 * @param minIntensity  Drop cells below this normalized intensity (default 0.05).
 */
export function buildHexHeatmap(
  points: ReadonlyArray<{ lng: number; lat: number }>,
  resolution: number = HEX_RESOLUTION,
  minIntensity: number = 0.05,
): HexFeatureCollection {
  if (points.length === 0) {
    return { type: "FeatureCollection", features: [] }
  }

  // Bin points into H3 cells — O(N).
  const counts = new Map<string, number>()
  for (const p of points) {
    if (!Number.isFinite(p.lat) || !Number.isFinite(p.lng)) continue
    const cell = latLngToCell(p.lat, p.lng, resolution)
    counts.set(cell, (counts.get(cell) ?? 0) + 1)
  }

  if (counts.size === 0) {
    return { type: "FeatureCollection", features: [] }
  }

  let maxCount = 0
  for (const c of counts.values()) {
    if (c > maxCount) maxCount = c
  }

  const features: GeoJSON.Feature<GeoJSON.Polygon, HexCellProperties>[] = []
  for (const [cell, count] of counts) {
    const intensity = maxCount > 0 ? count / maxCount : 0
    if (intensity < minIntensity) continue

    // cellToBoundary returns [lat, lng] pairs by default; ask for [lng, lat]
    // (GeoJSON order) by passing the second argument as true.
    const ring = cellToBoundary(cell, true)
    // Polygon needs a closed ring (first === last).
    const closed = [...ring, ring[0]]

    features.push({
      type: "Feature",
      geometry: { type: "Polygon", coordinates: [closed] },
      properties: {
        cell,
        count,
        intensity,
        color: neutralDensityColor(intensity),
      },
    })
  }

  return { type: "FeatureCollection", features }
}
