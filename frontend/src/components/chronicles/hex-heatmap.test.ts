import { describe, expect, it } from "vitest"

import { buildHexHeatmap } from "./hex-heatmap"

describe("buildHexHeatmap", () => {
  it("returns an empty FeatureCollection for an empty input", () => {
    const fc = buildHexHeatmap([])
    expect(fc.type).toBe("FeatureCollection")
    expect(fc.features).toHaveLength(0)
  })

  it("emits at least one hex cell when given a single point", () => {
    const fc = buildHexHeatmap([{ lng: 103.8, lat: 1.35 }])
    expect(fc.features.length).toBeGreaterThan(0)
    const f = fc.features[0]
    expect(f.geometry.type).toBe("Polygon")
    // intensity is the only cell so it normalizes to 1.
    expect(f.properties.intensity).toBe(1)
    expect(f.properties.count).toBe(1)
    expect(f.properties.color).toMatch(/^rgb\(/)
  })

  it("merges multiple co-located points into one hex cell with higher count", () => {
    const samePlace = Array.from({ length: 5 }, () => ({ lng: 103.8, lat: 1.35 }))
    const fc = buildHexHeatmap(samePlace)
    expect(fc.features).toHaveLength(1)
    expect(fc.features[0].properties.count).toBe(5)
  })

  it("normalizes intensity by the maximum cell count", () => {
    const points = [
      // Cluster A: 4 hits at one location
      ...Array.from({ length: 4 }, () => ({ lng: 103.8, lat: 1.35 })),
      // Cluster B: 1 hit elsewhere — far enough apart at res 8 to land in a
      // distinct hex (Singapore vs Paris).
      { lng: 2.35, lat: 48.86 },
    ]
    const fc = buildHexHeatmap(points, 8, 0)
    const intensities = fc.features.map((f) => f.properties.intensity).sort()
    expect(intensities[intensities.length - 1]).toBe(1)
    expect(intensities[0]).toBeCloseTo(0.25, 5)
  })

  it("ignores non-finite coordinates without throwing", () => {
    const fc = buildHexHeatmap([
      { lng: NaN, lat: 1.35 },
      { lng: 103.8, lat: Infinity },
      { lng: 103.8, lat: 1.35 },
    ])
    expect(fc.features).toHaveLength(1)
    expect(fc.features[0].properties.count).toBe(1)
  })

  it("polygons are closed (first coordinate equals last)", () => {
    const fc = buildHexHeatmap([{ lng: 103.8, lat: 1.35 }])
    const ring = fc.features[0].geometry.coordinates[0]
    expect(ring[0]).toEqual(ring[ring.length - 1])
  })
})
