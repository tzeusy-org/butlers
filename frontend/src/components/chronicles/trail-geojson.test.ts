import { describe, expect, it } from "vitest"
import { buildTrailGeoJSON, isTrailOutlier } from "./trail-geojson"

describe("isTrailOutlier", () => {
  it("flags fixes the Chronicler marked as impossible jumps", () => {
    expect(isTrailOutlier({ lat: 52.12, lon: 22.06, trail_outlier: { reason: "implied_speed" } })).toBe(true)
  })

  it("keeps ordinary fixes", () => {
    expect(isTrailOutlier({ lat: 39.9, lon: 116.4 })).toBe(false)
    expect(isTrailOutlier({ lat: 39.9, lon: 116.4, trail_outlier: null })).toBe(false)
  })
})

describe("buildTrailGeoJSON", () => {
  it("needs two points for a line", () => {
    expect(buildTrailGeoJSON([{ lng: 1, lat: 2 }]).features).toHaveLength(0)
    expect(buildTrailGeoJSON([{ lng: 1, lat: 2 }, { lng: 3, lat: 4 }]).features).toHaveLength(1)
  })
})
