// ---------------------------------------------------------------------------
// Tests for map-pan-store — bu-ig72b.24
//
// Covers:
//   1. parseLatLng — valid coordinate strings
//   2. parseLatLng — invalid/non-coordinate strings (no-op contract)
//   3. parseLatLng — boundary / edge values
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { parseLatLng } from "./location-utils"

// ---------------------------------------------------------------------------
// parseLatLng — valid inputs
// ---------------------------------------------------------------------------

describe("parseLatLng — valid lat,lng strings", () => {
  it("parses a simple integer pair", () => {
    expect(parseLatLng("1,103")).toEqual({ lat: 1, lng: 103 })
  })

  it("parses decimal degrees", () => {
    expect(parseLatLng("1.3521,103.8198")).toEqual({ lat: 1.3521, lng: 103.8198 })
  })

  it("parses negative latitude", () => {
    expect(parseLatLng("-33.8688,151.2093")).toEqual({ lat: -33.8688, lng: 151.2093 })
  })

  it("parses negative longitude", () => {
    expect(parseLatLng("40.7128,-74.0060")).toEqual({ lat: 40.7128, lng: -74.006 })
  })

  it("tolerates spaces around the comma", () => {
    expect(parseLatLng("1.35 , 103.82")).toEqual({ lat: 1.35, lng: 103.82 })
  })

  it("tolerates leading/trailing whitespace", () => {
    expect(parseLatLng("  51.5074 , -0.1278  ")).toEqual({ lat: 51.5074, lng: -0.1278 })
  })

  it("parses zero-zero", () => {
    expect(parseLatLng("0,0")).toEqual({ lat: 0, lng: 0 })
  })

  it("parses boundary values lat=90 lng=180", () => {
    expect(parseLatLng("90,180")).toEqual({ lat: 90, lng: 180 })
  })

  it("parses boundary values lat=-90 lng=-180", () => {
    expect(parseLatLng("-90,-180")).toEqual({ lat: -90, lng: -180 })
  })
})

// ---------------------------------------------------------------------------
// parseLatLng — invalid / non-coordinate strings (must return null)
// ---------------------------------------------------------------------------

describe("parseLatLng — non-coordinate strings return null", () => {
  it("returns null for a plain address string", () => {
    expect(parseLatLng("1 Infinite Loop, Cupertino, CA")).toBeNull()
  })

  it("returns null for an empty string", () => {
    expect(parseLatLng("")).toBeNull()
  })

  it("returns null for a single number", () => {
    expect(parseLatLng("1.35")).toBeNull()
  })

  it("returns null for three numbers", () => {
    expect(parseLatLng("1.35,103.82,10")).toBeNull()
  })

  it("returns null for non-numeric tokens", () => {
    expect(parseLatLng("lat,lng")).toBeNull()
  })

  it("returns null when lat is out of range (> 90)", () => {
    expect(parseLatLng("91,0")).toBeNull()
  })

  it("returns null when lat is out of range (< -90)", () => {
    expect(parseLatLng("-91,0")).toBeNull()
  })

  it("returns null when lng is out of range (> 180)", () => {
    expect(parseLatLng("0,181")).toBeNull()
  })

  it("returns null when lng is out of range (< -180)", () => {
    expect(parseLatLng("0,-181")).toBeNull()
  })

  it("returns null for a URL string", () => {
    expect(parseLatLng("https://maps.google.com/?q=1.35,103.82")).toBeNull()
  })

  it("returns null for 'N/A'", () => {
    expect(parseLatLng("N/A")).toBeNull()
  })
})
