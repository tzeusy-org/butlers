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
// ---------------------------------------------------------------------------

import { latLngToCell, cellToBoundary } from "h3-js"

/**
 * H3 resolution. 8 ≈ 460 m edge length — a sensible default for personal
 * location traces (city-block scale). Higher = finer cells.
 *
 * Reference: https://h3geo.org/docs/core-library/restable
 */
export const HEX_RESOLUTION = 8

/**
 * Red → yellow → green stops, matching property_agent's ACCESSIBILITY palette.
 * `at` is the normalized intensity in [0,1]; `r/g/b` are 0–255 sRGB channels.
 *
 * For Chronicles: low density = red (rare visit), high density = green (frequent).
 */
const COLOR_STOPS: ReadonlyArray<{ at: number; r: number; g: number; b: number }> = [
  { at: 0, r: 220, g: 38, b: 38 },     // red
  { at: 0.5, r: 245, g: 190, b: 11 },  // amber
  { at: 1, r: 22, g: 163, b: 74 },     // green
]

function clamp01(v: number): number {
  if (v < 0) return 0
  if (v > 1) return 1
  return v
}

/** Linearly interpolate between two stops in sRGB. */
function interpolateColor(normalized: number): string {
  const t = clamp01(normalized)
  for (let i = 0; i < COLOR_STOPS.length - 1; i++) {
    const lo = COLOR_STOPS[i]
    const hi = COLOR_STOPS[i + 1]
    if (t >= lo.at && t <= hi.at) {
      const span = hi.at - lo.at || 1
      const f = (t - lo.at) / span
      const r = Math.round(lo.r + (hi.r - lo.r) * f)
      const g = Math.round(lo.g + (hi.g - lo.g) * f)
      const b = Math.round(lo.b + (hi.b - lo.b) * f)
      return `rgb(${r}, ${g}, ${b})`
    }
  }
  // Fallback to the final stop.
  const last = COLOR_STOPS[COLOR_STOPS.length - 1]
  return `rgb(${last.r}, ${last.g}, ${last.b})`
}

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
        color: interpolateColor(intensity),
      },
    })
  }

  return { type: "FeatureCollection", features }
}
