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
//   - Attribution is rendered by MapLibre natively (OSM attribution required).
// ---------------------------------------------------------------------------

import maplibreGl, { type Map as MapLibreMap } from "maplibre-gl"
import "maplibre-gl/dist/maplibre-gl.css"
import { MapPin } from "lucide-react"
import { useEffect, useRef } from "react"

import { EmptyState } from "@/components/ui/empty-state"

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

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MapWidgetInner({ points, height = "h-80" }: MapWidgetInnerProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const markersRef = useRef<maplibreGl.Marker[]>([])

  // Sensitive points are never plotted — filter them out before rendering.
  const visiblePoints = points.filter((p) => p.privacy_tier !== "sensitive")

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
      // Remove all markers before destroying the map instance.
      for (const marker of markersRef.current) {
        marker.remove()
      }
      markersRef.current = []
      map.remove()
      mapRef.current = null
    }
  }, [hasPoints])

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

  if (visiblePoints.length === 0) {
    return (
      <div className={`relative w-full ${height} flex items-center justify-center`}>
        <EmptyState
          title="No location data"
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
