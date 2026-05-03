// ---------------------------------------------------------------------------
// MapWidgetInner — inner MapLibre-GL component (bu-ig72b.14)
//
// Imported only via the lazy() split in MapWidget.tsx so that maplibre-gl
// (and h3-js) land in a separate async chunk and don't inflate the main bundle.
//
// Responsibilities:
//   - Mount / tear-down a MapLibre map with a theme-aware CARTO basemap
//     (Positron No Labels in light mode, Dark Matter No Labels in dark mode).
//   - Show an EmptyState overlay when both `points` and `trailPoints` are empty.
//   - Render an OwnTracks trail as a connected line layer (bu-ig72b.35).
//   - Overlay a hexagonal density heatmap (H3 binning, red→green palette)
//     derived from `trailPoints`.
//   - Position a red playhead marker that glides smoothly between trail
//     samples as the user drags the scrubber.
// ---------------------------------------------------------------------------

import maplibreGl, { type GeoJSONSource, type Map as MapLibreMap } from "maplibre-gl"
import "maplibre-gl/dist/maplibre-gl.css"
import { MapPin } from "lucide-react"
import { useEffect, useMemo, useRef } from "react"

import { EmptyState } from "@/components/ui/empty-state"
import { useDarkMode } from "@/hooks/useDarkMode"
import { useRegisterMapPan } from "./map-pan-store"
import { buildTrailGeoJSON } from "./trail-geojson"
import { buildHexHeatmap, type HexFeatureCollection } from "./hex-heatmap"

// ---------------------------------------------------------------------------
// Playhead marker helpers
// ---------------------------------------------------------------------------

function createPlayheadEl(): HTMLElement {
  const el = document.createElement("div")
  el.setAttribute("data-testid", "map-playhead")
  el.style.cssText = [
    "width: 14px",
    "height: 14px",
    "border-radius: 50%",
    "background: hsl(0 84% 60%)",
    "border: 2px solid white",
    "box-shadow: 0 0 0 2px hsl(0 84% 60% / 40%)",
    "pointer-events: none",
  ].join(";")
  return el
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface MapPoint {
  lng: number
  lat: number
  label?: string
  category?: string
  /** Points with privacy_tier "sensitive" are never plotted. */
  privacy_tier?: string
}

export interface MapWidgetInnerProps {
  /** Location points to display in the current time window. */
  points: MapPoint[]
  /** Height class for the map container. @default "h-80" */
  height?: string
  /** Live playhead position (lng, lat) — updates as the scrubber moves. */
  playheadPoint?: { lng: number; lat: number } | null
  /**
   * OwnTracks trail points to render as a connected line layer (bu-ig72b.35).
   * Pre-sorted by occurred_at and pre-filtered (sensitive events excluded).
   */
  trailPoints?: Array<{ lng: number; lat: number }>
  /**
   * Whether the hex heatmap overlay is visible. The hexagons are always
   * computed from `trailPoints` (so toggling does not stutter); the layer
   * visibility is flipped via setLayoutProperty.
   * @default true
   */
  heatmapVisible?: boolean
}

// ---------------------------------------------------------------------------
// CARTO basemaps (theme-aware)
//
// Positron No Labels (light) and Dark Matter No Labels (dark) — CARTO's
// label-free basemaps. Labels are stripped because the heatmap and markers
// already carry the location story; labels would compete visually.
// Free for non-commercial use; attribution is provided in the style.
// ---------------------------------------------------------------------------

const CARTO_LIGHT_TILES = [
  "https://a.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png",
  "https://b.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png",
  "https://c.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png",
  "https://d.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png",
]

const CARTO_DARK_TILES = [
  "https://a.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png",
  "https://b.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png",
  "https://c.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png",
  "https://d.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png",
]

const CARTO_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions" target="_blank" rel="noopener">CARTO</a>'

function cartoStyle(isDark: boolean): maplibreGl.StyleSpecification {
  return {
    version: 8,
    sources: {
      basemap: {
        type: "raster",
        tiles: isDark ? CARTO_DARK_TILES : CARTO_LIGHT_TILES,
        tileSize: 256,
        attribution: CARTO_ATTRIBUTION,
        maxzoom: 19,
      },
    },
    layers: [{ id: "basemap-tiles", type: "raster", source: "basemap" }],
  }
}

// Default center (0,0) and zoom when there are no points.
const DEFAULT_CENTER: [number, number] = [0, 0]
const DEFAULT_ZOOM = 1

// Layer / source identifiers.
const TRAIL_SOURCE_ID = "owntracks-trail"
const TRAIL_LAYER_ID = "owntracks-trail-line"
const HEATMAP_SOURCE_ID = "owntracks-heatmap"
const HEATMAP_FILL_LAYER_ID = "owntracks-heatmap-fill"
const HEATMAP_OUTLINE_LAYER_ID = "owntracks-heatmap-outline"

// ---------------------------------------------------------------------------
// Overlay install helpers
// ---------------------------------------------------------------------------

function installOverlayLayers(
  map: MapLibreMap,
  trailGeoJSON: GeoJSON.FeatureCollection,
  heatmapGeoJSON: HexFeatureCollection,
  heatmapVisible: boolean,
): void {
  // Heatmap is added first so it sits beneath the trail line in z-order.
  map.addSource(HEATMAP_SOURCE_ID, { type: "geojson", data: heatmapGeoJSON })
  map.addLayer({
    id: HEATMAP_FILL_LAYER_ID,
    type: "fill",
    source: HEATMAP_SOURCE_ID,
    layout: { visibility: heatmapVisible ? "visible" : "none" },
    paint: {
      "fill-color": ["get", "color"],
      "fill-opacity": [
        "interpolate", ["linear"], ["get", "intensity"],
        0, 0.35,
        1, 0.65,
      ],
    },
  })
  map.addLayer({
    id: HEATMAP_OUTLINE_LAYER_ID,
    type: "line",
    source: HEATMAP_SOURCE_ID,
    layout: { visibility: heatmapVisible ? "visible" : "none" },
    paint: {
      "line-color": ["get", "color"],
      "line-width": 0.5,
      "line-opacity": 0.55,
    },
  })

  map.addSource(TRAIL_SOURCE_ID, { type: "geojson", data: trailGeoJSON })
  map.addLayer({
    id: TRAIL_LAYER_ID,
    type: "line",
    source: TRAIL_SOURCE_ID,
    layout: { "line-join": "round", "line-cap": "round" },
    paint: {
      "line-color": "hsl(220 90% 56%)",
      "line-width": 3,
      "line-opacity": 0.85,
    },
  })
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MapWidgetInner({
  points,
  height = "h-80",
  playheadPoint,
  trailPoints = [],
  heatmapVisible = true,
}: MapWidgetInnerProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const markersRef = useRef<maplibreGl.Marker[]>([])
  const playheadMarkerRef = useRef<maplibreGl.Marker | null>(null)
  const overlaysReadyRef = useRef<boolean>(false)

  const { resolvedTheme } = useDarkMode()
  const isDark = resolvedTheme === "dark"

  const registerMapPan = useRegisterMapPan()

  const visiblePoints = useMemo(
    () => points.filter((p) => p.privacy_tier !== "sensitive"),
    [points],
  )

  const hasTrailPoints = trailPoints.length > 0
  const hasMapData = visiblePoints.length > 0 || hasTrailPoints

  // Hex heatmap GeoJSON — memoised on trailPoints so scrubber drags do NOT
  // recompute the binning. This is the key performance lever: layer geometry
  // is uploaded to the GPU once per data change, not per frame.
  const heatmapGeoJSON = useMemo(
    () => buildHexHeatmap(trailPoints),
    [trailPoints],
  )

  // Trail GeoJSON — memoised for the same reason.
  const trailGeoJSON = useMemo(() => buildTrailGeoJSON(trailPoints), [trailPoints])

  // Initialise the map once the container is present; tear down on unmount or
  // when the component transitions back to the empty state.
  // We deliberately do NOT depend on `isDark` — theme switches reuse the
  // existing map instance via setStyle().
  useEffect(() => {
    if (!containerRef.current) return

    const map = new maplibreGl.Map({
      container: containerRef.current,
      style: cartoStyle(isDark),
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
      attributionControl: { compact: false },
    })

    mapRef.current = map

    return () => {
      playheadMarkerRef.current?.remove()
      playheadMarkerRef.current = null
      for (const marker of markersRef.current) marker.remove()
      markersRef.current = []
      overlaysReadyRef.current = false
      map.remove()
      mapRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasMapData])

  // Theme switch — replace the basemap style without remounting. setStyle
  // strips our custom sources/layers, so flip overlaysReadyRef and let the
  // overlay-sync effect re-install them once the new style finishes loading.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    overlaysReadyRef.current = false
    map.setStyle(cartoStyle(isDark))
  }, [isDark])

  // Register a panTo implementation with the shared pan store.
  useEffect(() => {
    registerMapPan((lat, lng) => {
      mapRef.current?.flyTo({ center: [lng, lat], zoom: 13 })
    })
  }, [registerMapPan, hasMapData])

  // Sync markers and fit bounds whenever points change.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    for (const marker of markersRef.current) marker.remove()
    markersRef.current = []

    if (visiblePoints.length === 0) return

    const bounds = new maplibreGl.LngLatBounds()
    for (const point of visiblePoints) {
      const popup = point.label
        ? new maplibreGl.Popup({ offset: 25 }).setText(point.label)
        : undefined
      const marker = new maplibreGl.Marker()
        .setLngLat([point.lng, point.lat])
        .addTo(map)
      if (popup) marker.setPopup(popup)
      markersRef.current.push(marker)
      bounds.extend([point.lng, point.lat])
    }
    map.fitBounds(bounds, { padding: 48, maxZoom: 14 })
  }, [visiblePoints])

  // Fit the map to the trail's bounding box on first load so the user sees
  // their movement immediately, even when no marker `points` are provided.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (visiblePoints.length > 0) return // marker effect already fits
    if (trailPoints.length === 0) return

    const bounds = new maplibreGl.LngLatBounds()
    for (const p of trailPoints) bounds.extend([p.lng, p.lat])
    map.fitBounds(bounds, { padding: 48, maxZoom: 14, duration: 0 })
    // We only want to auto-fit on data changes (window switch / refresh);
    // we don't want to fight the user's pan/zoom while they scrub.
  }, [trailPoints, visiblePoints.length])

  // Sync the playhead marker. setLngLat is just a transform update — cheap
  // enough to call on every scrubber tick without debouncing.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    if (!playheadPoint) {
      playheadMarkerRef.current?.remove()
      playheadMarkerRef.current = null
      return
    }

    const { lng, lat } = playheadPoint
    if (playheadMarkerRef.current) {
      playheadMarkerRef.current.setLngLat([lng, lat])
    } else {
      playheadMarkerRef.current = new maplibreGl.Marker({ element: createPlayheadEl() })
        .setLngLat([lng, lat])
        .addTo(map)
    }
  }, [playheadPoint])

  // Overlay sync — adds heatmap + trail sources/layers when the style is
  // ready, and updates source data on subsequent renders. A single styledata
  // listener handles both the initial map load and any later setStyle() call
  // (theme switch). overlaysReadyRef guards against duplicate add* calls.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    const sync = () => {
      if (!mapRef.current) return
      if (!map.isStyleLoaded()) return

      if (overlaysReadyRef.current) {
        ;(map.getSource(HEATMAP_SOURCE_ID) as GeoJSONSource | undefined)?.setData(heatmapGeoJSON)
        ;(map.getSource(TRAIL_SOURCE_ID) as GeoJSONSource | undefined)?.setData(trailGeoJSON)
        // Toggle visibility if heatmapVisible flipped while overlays existed.
        if (map.getLayer(HEATMAP_FILL_LAYER_ID)) {
          map.setLayoutProperty(
            HEATMAP_FILL_LAYER_ID,
            "visibility",
            heatmapVisible ? "visible" : "none",
          )
        }
        if (map.getLayer(HEATMAP_OUTLINE_LAYER_ID)) {
          map.setLayoutProperty(
            HEATMAP_OUTLINE_LAYER_ID,
            "visibility",
            heatmapVisible ? "visible" : "none",
          )
        }
        return
      }
      installOverlayLayers(map, trailGeoJSON, heatmapGeoJSON, heatmapVisible)
      overlaysReadyRef.current = true
    }

    if (map.isStyleLoaded()) {
      sync()
    }
    // styledata fires after the initial style load AND after any setStyle()
    // call from the theme-switch effect, so a single listener covers both.
    map.on("styledata", sync)
    return () => {
      map.off("styledata", sync)
    }
  }, [heatmapGeoJSON, trailGeoJSON, heatmapVisible])

  if (!hasMapData) {
    return (
      <div
        className={`relative w-full ${height} flex items-center justify-center`}
        data-testid="map-empty"
      >
        <EmptyState
          title="No activity recorded for this window"
          description="Location points will appear here when the chronicler detects travel or place events in the current window."
          icon={<MapPin />}
        />
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className={`w-full ${height} rounded-md overflow-hidden`}
      aria-label="Location map"
      data-testid="map-container"
    />
  )
}
