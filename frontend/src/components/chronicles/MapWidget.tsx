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

import { lazy, Suspense } from "react"

import { Skeleton } from "@/components/ui/skeleton"

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
  return <Skeleton className={`w-full ${height} rounded-md`} />
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
 * - Delegates empty-state rendering to MapWidgetInner.
 */
export function MapWidget(props: MapWidgetInnerProps) {
  return (
    <Suspense fallback={<MapLoadingSkeleton height={props.height} />}>
      <MapWidgetInner {...props} />
    </Suspense>
  )
}
