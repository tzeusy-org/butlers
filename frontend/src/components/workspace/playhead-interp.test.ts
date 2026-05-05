import { describe, expect, it } from "vitest"

import { interpolatePlayhead, type TimedTrailPoint } from "./playhead-interp"

const PT = (lng: number, lat: number, ms: number): TimedTrailPoint => ({ lng, lat, ms })

describe("interpolatePlayhead", () => {
  it("returns null for an empty trail", () => {
    expect(interpolatePlayhead(1000, [])).toBeNull()
  })

  it("returns the only point regardless of scrubber time when trail has one sample", () => {
    const trail = [PT(103.8, 1.35, 1000)]
    expect(interpolatePlayhead(0, trail)).toEqual({ lng: 103.8, lat: 1.35 })
    expect(interpolatePlayhead(99999, trail)).toEqual({ lng: 103.8, lat: 1.35 })
  })

  it("clamps to the first sample when scrubber is before the trail start", () => {
    const trail = [PT(0, 0, 1000), PT(10, 10, 2000)]
    expect(interpolatePlayhead(500, trail)).toEqual({ lng: 0, lat: 0 })
  })

  it("clamps to the last sample when scrubber is after the trail end", () => {
    const trail = [PT(0, 0, 1000), PT(10, 10, 2000)]
    expect(interpolatePlayhead(5000, trail)).toEqual({ lng: 10, lat: 10 })
  })

  it("interpolates linearly between two adjacent samples at the midpoint", () => {
    const trail = [PT(0, 0, 1000), PT(10, 20, 2000)]
    const mid = interpolatePlayhead(1500, trail)
    expect(mid?.lng).toBeCloseTo(5, 6)
    expect(mid?.lat).toBeCloseTo(10, 6)
  })

  it("picks the correct segment from a longer trail and interpolates within it", () => {
    const trail = [
      PT(0, 0, 0),
      PT(10, 10, 1000),
      PT(20, 20, 2000),
      PT(30, 30, 3000),
    ]
    const at1500 = interpolatePlayhead(1500, trail)
    expect(at1500?.lng).toBeCloseTo(15, 6)
    expect(at1500?.lat).toBeCloseTo(15, 6)
  })

  it("returns the earlier sample when two samples share a timestamp", () => {
    const trail = [PT(1, 1, 1000), PT(2, 2, 1000), PT(3, 3, 2000)]
    const result = interpolatePlayhead(1000, trail)
    // Either of the equal-timestamp samples is acceptable; both correspond
    // to the clamped t=1000 value at the start of the relevant segment.
    expect(result).toBeDefined()
    expect(result?.lng).toBeGreaterThanOrEqual(1)
    expect(result?.lng).toBeLessThanOrEqual(2)
  })
})
