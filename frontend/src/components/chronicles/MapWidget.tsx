// ---------------------------------------------------------------------------
// MapWidget — code-split entry point (bu-ig72b.14)
//
// maplibre-gl is ~250 kB gzip. Importing it statically would push the main
// chunk over the 500 kB threshold (pre-PR: ~492 kB gzip). This wrapper uses
// React.lazy() so that Vite emits maplibre-gl into a separate async chunk
// that is only fetched when the user navigates to the Chronicles page.
//
// Usage:
//   <MapWidget points={[{ lng: 103.8, lat: 1.3, label: "Singapore" }]} />
//   <MapWidget points={[]} />   {/* renders empty state */}
// ---------------------------------------------------------------------------

import { Component, lazy, Suspense, type ReactNode } from "react"

import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"

import type { MapWidgetInnerProps } from "./MapWidgetInner"

// ---------------------------------------------------------------------------
// Lazy inner component — maplibre-gl lives in this async chunk.
// ---------------------------------------------------------------------------

const MapWidgetInner = lazy(() =>
  import("./MapWidgetInner").then((m) => ({ default: m.MapWidgetInner }))
)

// ---------------------------------------------------------------------------
// Loading skeleton — mirrors the map container height.
// ---------------------------------------------------------------------------

function MapLoadingSkeleton({ height = "h-80" }: { height?: string }) {
  return (
    <Skeleton
      className={`w-full ${height} rounded-md`}
      data-testid="map-skeleton"
      role="status"
      aria-label="Loading map"
    />
  )
}

// ---------------------------------------------------------------------------
// Error fallback — shown when the lazy chunk or map render throws.
// ---------------------------------------------------------------------------

function MapErrorFallback({ height = "h-80", onRetry }: { height?: string; onRetry?: () => void }) {
  return (
    <div
      className={`flex flex-col items-center justify-center w-full ${height} gap-3 rounded-md border border-dashed text-sm text-muted-foreground`}
      data-testid="map-error"
    >
      <p>Failed to load the map.</p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Error boundary — catches render and lazy-load errors in MapWidgetInner.
// ---------------------------------------------------------------------------

interface MapErrorBoundaryState {
  hasError: boolean
}

interface MapErrorBoundaryProps {
  height?: string
  children: ReactNode
}

class MapErrorBoundary extends Component<MapErrorBoundaryProps, MapErrorBoundaryState> {
  constructor(props: MapErrorBoundaryProps) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(): MapErrorBoundaryState {
    return { hasError: true }
  }

  render() {
    if (this.state.hasError) {
      return (
        <MapErrorFallback
          height={this.props.height}
          onRetry={() => this.setState({ hasError: false })}
        />
      )
    }
    return this.props.children
  }
}

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export type { MapPoint } from "./MapWidgetInner"

/** Props forwarded to the inner map implementation. */
export type { MapWidgetInnerProps as MapWidgetProps }

/**
 * Map widget with OSM tiles via MapLibre GL.
 *
 * - Lazy-loads maplibre-gl into a separate async chunk.
 * - Shows a loading skeleton while the chunk is fetching.
 * - Shows an error fallback (with retry) if the lazy chunk or map render throws.
 * - Delegates empty-state rendering to MapWidgetInner.
 */
export function MapWidget(props: MapWidgetInnerProps) {
  return (
    <MapErrorBoundary height={props.height}>
      <Suspense fallback={<MapLoadingSkeleton height={props.height} />}>
        <MapWidgetInner {...props} />
      </Suspense>
    </MapErrorBoundary>
  )
}
