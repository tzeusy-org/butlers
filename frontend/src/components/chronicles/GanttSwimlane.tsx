// ---------------------------------------------------------------------------
// GanttSwimlane — code-split entry point (bu-ig72b.28)
//
// The GanttSwimlaneInner imports LANE_TAXONOMY and builds a hand-rolled SVG
// Gantt. No new npm dependencies are introduced; the component is code-split
// via React.lazy() to keep it out of the main bundle, matching the pattern
// established by MapWidget.tsx for maplibre-gl.
//
// Usage:
//   <GanttSwimlane windowStart={from} windowEnd={to} />
//
// Props are optional; the component is self-fetching via useChroniclesEpisodes.
// Caller passes windowStart / windowEnd to scope the query to the active
// time window.
// ---------------------------------------------------------------------------

import { lazy, Suspense } from "react"

import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import { useChroniclesEpisodes } from "@/hooks/use-chronicles"
import type { ChroniclerEpisodesParams } from "@/api/types"

// ---------------------------------------------------------------------------
// Lazy inner component
// ---------------------------------------------------------------------------

const GanttSwimlaneInner = lazy(() =>
  import("./GanttSwimlaneInner").then((m) => ({ default: m.GanttSwimlaneInner }))
)

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function GanttLoadingSkeleton() {
  return (
    <div
      className="space-y-2"
      data-testid="gantt-skeleton"
      role="status"
      aria-label="Loading Gantt chart"
    >
      {/* Simulate 4 swimlane rows */}
      {Array.from({ length: 4 }, (_, i) => (
        <div key={i} className="flex items-center gap-2">
          <Skeleton className="h-4 w-20 rounded" />
          <Skeleton className="h-5 flex-1 rounded-md" />
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Error fallback
// ---------------------------------------------------------------------------

function GanttErrorFallback({ onRetry }: { onRetry?: () => void }) {
  return (
    <div
      className="flex flex-col items-center justify-center rounded-md border border-dashed py-12 gap-3 text-sm text-muted-foreground"
      data-testid="gantt-error"
    >
      <p>Failed to load timeline data.</p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export interface GanttSwimlaneProps {
  windowStart: Date
  windowEnd: Date
  /** Override refetch interval (ms). Pass false to disable. Default 30s. */
  refetchInterval?: number | false
}

/**
 * Gantt swimlane with one row per category from LANE_TAXONOMY.
 *
 * - Fetches episodes via useChroniclesEpisodes scoped to the active window.
 * - Lazy-loads the SVG renderer into a separate async chunk.
 * - Shows a loading skeleton while the chunk or data is loading.
 * - Restricted episodes are excluded at the server layer by default.
 * - Sensitive episodes rendered as masked bars (bu-D3 follow-up).
 */
export function GanttSwimlane({ windowStart, windowEnd, refetchInterval }: GanttSwimlaneProps) {
  // Build query params scoped to the visible time window.
  // overlaps_start/overlaps_end returns episodes that overlap [windowStart, windowEnd].
  const params: ChroniclerEpisodesParams = {
    overlaps_start: windowStart.toISOString(),
    overlaps_end: windowEnd.toISOString(),
    limit: 500,
  }

  const { data, isLoading, isError, refetch } = useChroniclesEpisodes(params, { refetchInterval })

  const episodes = data?.data ?? []

  if (isLoading && episodes.length === 0) {
    return <GanttLoadingSkeleton />
  }

  if (isError) {
    return <GanttErrorFallback onRetry={() => { void refetch() }} />
  }

  return (
    <Suspense fallback={<GanttLoadingSkeleton />}>
      <GanttSwimlaneInner
        episodes={episodes}
        windowStart={windowStart}
        windowEnd={windowEnd}
      />
    </Suspense>
  )
}
