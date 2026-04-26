// ---------------------------------------------------------------------------
// Tests for MapWidget — bu-ig72b.14
//
// Strategy: unit tests that cover the public interface and inner logic without
// requiring a real WebGL context. We mock maplibre-gl so tests exercise our
// component logic — empty state, map container rendering — without spawning
// a real map instance. React component rendering uses renderToStaticMarkup
// (same pattern as existing component tests in this codebase).
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

// ---------------------------------------------------------------------------
// Mock maplibre-gl before any component imports.
// Must be hoisted — vi.mock is hoisted automatically by vitest.
// ---------------------------------------------------------------------------

vi.mock("maplibre-gl", async () => {
  class MockMap {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    constructor(..._args: unknown[]) {}
    isStyleLoaded() { return true }
    fitBounds() {}
    remove() {}
    on() {}
    off() {}
  }

  class MockMarker {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    setLngLat(..._args: unknown[]) { return this }
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    addTo(..._args: unknown[]) { return this }
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    setPopup(..._args: unknown[]) { return this }
    remove() {}
  }

  class MockPopup {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    setText(..._args: unknown[]) { return this }
  }

  class MockLngLatBounds {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    extend(..._args: unknown[]) { return this }
  }

  const mock = {
    Map: MockMap,
    Marker: MockMarker,
    Popup: MockPopup,
    LngLatBounds: MockLngLatBounds,
  }
  return { default: mock, ...mock }
})

// Mock the CSS import so jsdom/server rendering doesn't choke on it.
vi.mock("maplibre-gl/dist/maplibre-gl.css", () => ({}))

// ---------------------------------------------------------------------------
// Imports after mocks are registered.
// ---------------------------------------------------------------------------

import { MapWidgetInner } from "./MapWidgetInner"
import { buildTrailGeoJSON } from "./trail-geojson"
import type { MapPoint } from "./MapWidget"

// ---------------------------------------------------------------------------
// MapWidgetInner — empty state (zero points)
// ---------------------------------------------------------------------------

describe("MapWidgetInner empty state", () => {
  it("renders 'No activity recorded for this window' heading when points array is empty", () => {
    const html = renderToStaticMarkup(<MapWidgetInner points={[]} />)
    expect(html).toContain("No activity recorded for this window")
  })

  it("renders the descriptive empty-state text when points is empty", () => {
    const html = renderToStaticMarkup(<MapWidgetInner points={[]} />)
    expect(html).toContain("Location points will appear here")
  })

  it("does NOT render a map container when points is empty", () => {
    const html = renderToStaticMarkup(<MapWidgetInner points={[]} />)
    // The map container has data-testid="map-container"
    expect(html).not.toContain("map-container")
  })
})

// ---------------------------------------------------------------------------
// MapWidgetInner — with points
// ---------------------------------------------------------------------------

describe("MapWidgetInner with points", () => {
  const samplePoints: MapPoint[] = [
    { lng: 103.8, lat: 1.35, label: "Singapore" },
    { lng: 2.35, lat: 48.86, label: "Paris" },
  ]

  it("renders the map container element when points are provided", () => {
    const html = renderToStaticMarkup(<MapWidgetInner points={samplePoints} />)
    expect(html).toContain("map-container")
  })

  it("does NOT render the empty-state heading when points are provided", () => {
    const html = renderToStaticMarkup(<MapWidgetInner points={samplePoints} />)
    expect(html).not.toContain("No activity recorded for this window")
  })
})

// ---------------------------------------------------------------------------
// MapWidgetInner — height prop
// ---------------------------------------------------------------------------

describe("MapWidgetInner height prop", () => {
  it("applies default h-80 height class to the empty-state container", () => {
    const html = renderToStaticMarkup(<MapWidgetInner points={[]} />)
    expect(html).toContain("h-80")
  })

  it("applies custom height class when height prop is provided", () => {
    const html = renderToStaticMarkup(<MapWidgetInner points={[]} height="h-96" />)
    expect(html).toContain("h-96")
  })

  it("applies height class to map container when points are provided", () => {
    const points: MapPoint[] = [{ lng: 0, lat: 0 }]
    const html = renderToStaticMarkup(<MapWidgetInner points={points} height="h-96" />)
    expect(html).toContain("h-96")
  })
})

// ---------------------------------------------------------------------------
// MapWidgetInner — sensitive point exclusion (privacy contract)
// ---------------------------------------------------------------------------

describe("MapWidgetInner sensitive point exclusion", () => {
  it("does NOT render a map container when only sensitive points are provided", () => {
    const sensitivePoint: MapPoint = {
      lng: 103.8,
      lat: 1.35,
      label: "Secret location",
      privacy_tier: "sensitive",
    }
    const html = renderToStaticMarkup(<MapWidgetInner points={[sensitivePoint]} />)
    // All supplied points are sensitive — widget must show empty state, not the map.
    expect(html).not.toContain("map-container")
    expect(html).toContain("No activity recorded for this window")
  })

  it("renders only non-sensitive points when mixed privacy_tier values are provided", () => {
    const normalPoint: MapPoint = { lng: 2.35, lat: 48.86, label: "Paris" }
    const sensitivePoint: MapPoint = {
      lng: 103.8,
      lat: 1.35,
      label: "Secret location",
      privacy_tier: "sensitive",
    }
    const html = renderToStaticMarkup(
      <MapWidgetInner points={[normalPoint, sensitivePoint]} />,
    )
    // Normal point keeps the map rendered.
    expect(html).toContain("map-container")
  })

  it("renders map container when points have no privacy_tier set", () => {
    const point: MapPoint = { lng: 0, lat: 0 }
    const html = renderToStaticMarkup(<MapWidgetInner points={[point]} />)
    expect(html).toContain("map-container")
  })
})

// ---------------------------------------------------------------------------
// MapPoint type contract (compile-time guard via explicit cast)
// ---------------------------------------------------------------------------

describe("MapPoint type", () => {
  it("accepts required lng/lat fields", () => {
    const point: MapPoint = { lng: 0, lat: 0 }
    expect(point.lng).toBe(0)
    expect(point.lat).toBe(0)
  })

  it("accepts optional label field", () => {
    const point: MapPoint = { lng: 1, lat: 2, label: "Home" }
    expect(point.label).toBe("Home")
  })

  it("accepts optional category field", () => {
    const point: MapPoint = { lng: 1, lat: 2, category: "travel" }
    expect(point.category).toBe("travel")
  })

  it("accepts optional privacy_tier field", () => {
    const point: MapPoint = { lng: 1, lat: 2, privacy_tier: "sensitive" }
    expect(point.privacy_tier).toBe("sensitive")
  })
})

// ---------------------------------------------------------------------------
// buildTrailGeoJSON — OwnTracks trail GeoJSON builder (bu-ig72b.35)
// ---------------------------------------------------------------------------

describe("buildTrailGeoJSON", () => {
  it("returns an empty FeatureCollection when given zero points", () => {
    const result = buildTrailGeoJSON([])
    expect(result.type).toBe("FeatureCollection")
    expect(result.features).toHaveLength(0)
  })

  it("returns an empty FeatureCollection for a single point (LineString needs ≥2)", () => {
    const result = buildTrailGeoJSON([{ lng: 103.8, lat: 1.35 }])
    expect(result.type).toBe("FeatureCollection")
    expect(result.features).toHaveLength(0)
  })

  it("returns a FeatureCollection with one LineString feature for two points", () => {
    const result = buildTrailGeoJSON([
      { lng: 103.8, lat: 1.35 },
      { lng: 2.35, lat: 48.86 },
    ])
    expect(result.type).toBe("FeatureCollection")
    expect(result.features).toHaveLength(1)
    const feature = result.features[0]
    expect(feature.type).toBe("Feature")
    expect(feature.geometry.type).toBe("LineString")
  })

  it("builds coordinates in [lng, lat] order (GeoJSON spec)", () => {
    const result = buildTrailGeoJSON([
      { lng: 103.8, lat: 1.35 },
      { lng: 2.35, lat: 48.86 },
    ])
    // geometry is GeoJSON.LineString — access coordinates directly
    const geom = result.features[0].geometry
    expect(geom.type).toBe("LineString")
    const coords = (geom as { type: string; coordinates: number[][] }).coordinates
    expect(coords[0]).toEqual([103.8, 1.35])
    expect(coords[1]).toEqual([2.35, 48.86])
  })

  it("preserves the order of input points (caller is responsible for sorting)", () => {
    const points = [
      { lng: 1.0, lat: 10.0 },
      { lng: 2.0, lat: 20.0 },
      { lng: 3.0, lat: 30.0 },
    ]
    const result = buildTrailGeoJSON(points)
    const geom = result.features[0].geometry
    const coords = (geom as { type: string; coordinates: number[][] }).coordinates
    expect(coords).toHaveLength(3)
    expect(coords[0]).toEqual([1.0, 10.0])
    expect(coords[2]).toEqual([3.0, 30.0])
  })
})

// ---------------------------------------------------------------------------
// MapWidgetInner — trailPoints prop contract (bu-ig72b.35)
// ---------------------------------------------------------------------------

describe("MapWidgetInner trailPoints prop", () => {
  it("accepts trailPoints prop without error when map container is rendered", () => {
    // Provide a visible point so the map container renders, then pass trailPoints.
    const html = renderToStaticMarkup(
      <MapWidgetInner
        points={[{ lng: 103.8, lat: 1.35 }]}
        trailPoints={[
          { lng: 103.8, lat: 1.35 },
          { lng: 2.35, lat: 48.86 },
        ]}
      />,
    )
    // The map container is rendered when visible points exist.
    expect(html).toContain("map-container")
  })

  it("accepts empty trailPoints without error", () => {
    const html = renderToStaticMarkup(
      <MapWidgetInner
        points={[{ lng: 103.8, lat: 1.35 }]}
        trailPoints={[]}
      />,
    )
    expect(html).toContain("map-container")
  })

  it("omitting trailPoints (undefined) does not affect map rendering", () => {
    const html = renderToStaticMarkup(
      <MapWidgetInner points={[{ lng: 0, lat: 0 }]} />,
    )
    expect(html).toContain("map-container")
  })
})

// ---------------------------------------------------------------------------
// MapWidgetInner — trail-only render (bu-2xpqt)
//
// The original bug: when points=[] but trailPoints is non-empty, the map
// canvas was never mounted because `if (visiblePoints.length === 0)` returned
// the EmptyState before the canvas div was rendered, making the trail layer
// unreachable.  Fix: gate on hasMapData = visiblePoints.length > 0 || hasTrailPoints.
// ---------------------------------------------------------------------------

describe("MapWidgetInner trail-only render (bu-2xpqt)", () => {
  const trailOnlyPoints = [
    { lng: 103.8, lat: 1.35 },
    { lng: 103.81, lat: 1.36 },
  ]

  it("renders map container when points=[] but trailPoints is non-empty", () => {
    const html = renderToStaticMarkup(
      <MapWidgetInner points={[]} trailPoints={trailOnlyPoints} />,
    )
    expect(html).toContain("map-container")
  })

  it("does NOT render EmptyState when only trailPoints are provided", () => {
    const html = renderToStaticMarkup(
      <MapWidgetInner points={[]} trailPoints={trailOnlyPoints} />,
    )
    expect(html).not.toContain("No activity recorded for this window")
  })

  it("renders EmptyState when both points and trailPoints are empty", () => {
    const html = renderToStaticMarkup(
      <MapWidgetInner points={[]} trailPoints={[]} />,
    )
    expect(html).toContain("No activity recorded for this window")
    expect(html).not.toContain("map-container")
  })

  it("renders EmptyState when all points are sensitive and trailPoints is empty", () => {
    const sensitivePoint = { lng: 103.8, lat: 1.35, privacy_tier: "sensitive" as const }
    const html = renderToStaticMarkup(
      <MapWidgetInner points={[sensitivePoint]} trailPoints={[]} />,
    )
    expect(html).toContain("No activity recorded for this window")
    expect(html).not.toContain("map-container")
  })

  it("renders map container when all points are sensitive but trailPoints is non-empty", () => {
    const sensitivePoint = { lng: 103.8, lat: 1.35, privacy_tier: "sensitive" as const }
    const html = renderToStaticMarkup(
      <MapWidgetInner points={[sensitivePoint]} trailPoints={trailOnlyPoints} />,
    )
    // trailPoints exist — canvas must mount even though no visible marker points.
    expect(html).toContain("map-container")
    expect(html).not.toContain("No activity recorded for this window")
  })
})
