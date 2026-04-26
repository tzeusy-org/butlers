// ---------------------------------------------------------------------------
// MapWidgetInner — inner MapLibre-GL component (bu-ig72b.14)
//
// Imported only via the lazy() split in MapWidget.tsx so that maplibre-gl
// lands in its own async chunk and does not inflate the main bundle.
//
// Responsibilities:
//   - Mount / tear-down a MapLibre map with an OSM tile source.
//   - Show an EmptyState overlay when the `points` array is empty.
//   - Accept optional GeoJSON points and fit the map to their bounds.
//   - Render an OwnTracks trail as a connected line layer (bu-ig72b.35).
//   - Attribution is rendered by MapLibre natively (OSM attribution required).
// ---------------------------------------------------------------------------

import maplibreGl, { type GeoJSONSource, type Map as MapLibreMap } from "maplibre-gl"
import "maplibre-gl/dist/maplibre-gl.css"
import { MapPin } from "lucide-react"
import { useEffect, useMemo, useRef } from "react"

import { EmptyState } from "@/components/ui/empty-state"
import { useRegisterMapPan } from "./map-pan-store"
import { buildTrailGeoJSON } from "./trail-geojson"

// ---------------------------------------------------------------------------
// Playhead marker helpers
// ---------------------------------------------------------------------------

/** Create a DOM element for the playhead marker (filled circle). */
function createPlayheadEl(): HTMLElement {
  const el = document.createElement("div")
  el.setAttribute("data-testid", "map-playhead")
  el.style.cssText = [
    "width: 14px",
    "height: 14px",
    "border-radius: 50%",
    "background: hsl(0 84% 60%)",  // destructive red — clearly distinguishable
    "border: 2px solid white",
    "box-shadow: 0 0 0 2px hsl(0 84% 60% / 40%)",
    "pointer-events: none",
  ].join(";")
  return el
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** A single displayable location point. */
export interface MapPoint {
  /** Longitude in decimal degrees (WGS 84). */
  lng: number
  /** Latitude in decimal degrees (WGS 84). */
  lat: number
  /** Optional label shown in the marker popup. */
  label?: string
  /** Optional category string from the lane taxonomy (used for future coloring). */
  category?: string
  /**
   * Privacy tier inherited from the linked episode.
   * Points with privacy_tier "sensitive" are never plotted on the map.
   */
  privacy_tier?: string
}

export interface MapWidgetInnerProps {
  /** Location points to display in the current time window. */
  points: MapPoint[]
  /** Height class for the map container. @default "h-80" */
  height?: string
  /**
   * The snapped playhead point (lng, lat) in epoch ms coordinates.
   * When set, a distinct marker is placed at this position (D12 — map
   * playhead follows scrubber). The nearest point in `points` matching
   * this epoch ms is highlighted; if none match, no playhead is shown.
   *
   * Pass null or undefined when no scrubber position is active.
   */
  playheadPoint?: { lng: number; lat: number } | null
  /**
   * OwnTracks trail points to render as a connected line layer (bu-ig72b.35).
   * Points are already sorted by occurred_at and pre-filtered (sensitive
   * events excluded). A LineString is rendered when ≥2 points are provided.
   * Empty array or single point → empty FeatureCollection (no line drawn).
   */
  trailPoints?: Array<{ lng: number; lat: number }>
}

// ---------------------------------------------------------------------------
// OSM tile style
// ---------------------------------------------------------------------------

/** Minimal MapLibre style that uses the OpenStreetMap raster tile service. */
const OSM_STYLE: maplibreGl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
      maxzoom: 19,
    },
  },
  layers: [
    {
      id: "osm-tiles",
      type: "raster",
      source: "osm",
    },
  ],
}

// Default center (0,0) and zoom when there are no points.
const DEFAULT_CENTER: [number, number] = [0, 0]
const DEFAULT_ZOOM = 1

// OwnTracks trail layer identifiers (bu-ig72b.35).
const TRAIL_SOURCE_ID = "owntracks-trail"
const TRAIL_LAYER_ID = "owntracks-trail-line"

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MapWidgetInner({
  points,
  height = "h-80",
  playheadPoint,
  trailPoints = [],
}: MapWidgetInnerProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const markersRef = useRef<maplibreGl.Marker[]>([])
  const playheadMarkerRef = useRef<maplibreGl.Marker | null>(null)
  // Whether the trail source/layer have been added to the current map instance.
  const trailReadyRef = useRef<boolean>(false)

  // Register flyTo with the pan store so Gantt episode clicks can pan the map.
  // useRegisterMapPan returns a no-op if there is no MapPanContext provider,
  // which keeps MapWidgetInner usable in standalone / test contexts.
  const registerMapPan = useRegisterMapPan()

  // Sensitive points are never plotted — filter them out before rendering.
  // Memoised to avoid recreating the array on every render (stable reference
  // prevents the marker-sync useEffect from firing spuriously).
  const visiblePoints = useMemo(
    () => points.filter((p) => p.privacy_tier !== "sensitive"),
    [points],
  )

  // hasPoints determines whether the map container is rendered.  The map
  // initialisation effect depends on this flag so that React re-runs the
  // effect (and mounts a fresh map instance) whenever the component switches
  // between the empty-state overlay and the real map container.
  const hasPoints = visiblePoints.length > 0

  // Initialise the map once the container is present; tear down on unmount or
  // when the component transitions back to the empty state.
  useEffect(() => {
    if (!containerRef.current) return

    const map = new maplibreGl.Map({
      container: containerRef.current,
      style: OSM_STYLE,
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
      // Attribution control uses default options (compact mode); OSM attribution is in OSM_STYLE sources.
      attributionControl: { compact: false },
    })

    mapRef.current = map

    return () => {
      // Remove playhead marker before destroying the map instance.
      playheadMarkerRef.current?.remove()
      playheadMarkerRef.current = null
      // Remove all regular markers before destroying the map instance.
      for (const marker of markersRef.current) {
        marker.remove()
      }
      markersRef.current = []
      trailReadyRef.current = false
      map.remove()
      mapRef.current = null
    }
  }, [hasPoints])

  // Register a panTo implementation with the shared pan store.
  // Re-runs whenever hasPoints changes (same dependency as the map init effect).
  // The closure captures mapRef so it always calls the current map instance.
  useEffect(() => {
    registerMapPan((lat, lng) => {
      mapRef.current?.flyTo({ center: [lng, lat], zoom: 13 })
    })
  }, [registerMapPan, hasPoints])

  // Sync markers and fit bounds whenever points change.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    // Clear existing markers.
    for (const marker of markersRef.current) {
      marker.remove()
    }
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

      if (popup) {
        marker.setPopup(popup)
      }

      markersRef.current.push(marker)
      bounds.extend([point.lng, point.lat])
    }

    map.fitBounds(bounds, { padding: 48, maxZoom: 14 })
  }, [visiblePoints])

  // Sync the playhead marker position whenever `playheadPoint` changes.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    if (!playheadPoint) {
      // No active playhead — remove the marker if it exists.
      playheadMarkerRef.current?.remove()
      playheadMarkerRef.current = null
      return
    }

    const { lng, lat } = playheadPoint

    if (playheadMarkerRef.current) {
      // Update position of existing marker (cheap — avoids DOM churn).
      playheadMarkerRef.current.setLngLat([lng, lat])
    } else {
      // Create a new playhead marker.
      playheadMarkerRef.current = new maplibreGl.Marker({ element: createPlayheadEl() })
        .setLngLat([lng, lat])
        .addTo(map)
    }
  }, [playheadPoint])

  // Sync the OwnTracks trail source/layer whenever `trailPoints` changes (bu-ig72b.35).
  //
  // The trail source ('owntracks-trail') and layer ('owntracks-trail-line') are
  // added lazily on the first render after the map is ready. Subsequent updates
  // call setData() on the existing source — cheaper than remove/re-add.
  //
  // A LineString requires ≥2 coordinate pairs; buildTrailGeoJSON returns an
  // empty FeatureCollection for 0 or 1 points so the layer renders nothing.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    const geoJSON = buildTrailGeoJSON(trailPoints)

    if (!trailReadyRef.current) {
      // First time: add the GeoJSON source and line layer.
      map.addSource(TRAIL_SOURCE_ID, {
        type: "geojson",
        data: geoJSON,
      })
      map.addLayer({
        id: TRAIL_LAYER_ID,
        type: "line",
        source: TRAIL_SOURCE_ID,
        layout: {
          "line-join": "round",
          "line-cap": "round",
        },
        paint: {
          "line-color": "hsl(220 90% 56%)",
          "line-width": 3,
          "line-opacity": 0.85,
        },
      })
      trailReadyRef.current = true
    } else {
      // Source already registered — update its data in-place.
      ;(map.getSource(TRAIL_SOURCE_ID) as GeoJSONSource).setData(geoJSON)
    }
  }, [trailPoints])

  if (visiblePoints.length === 0) {
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
