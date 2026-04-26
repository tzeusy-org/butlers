// ---------------------------------------------------------------------------
// AggregatePieChart — Pie chart of total time by category (bu-ig72b.32)
//
// Consumes `ChroniclerCategoryBucket[]` from `useChroniclesAggregates` and
// renders a recharts PieChart with one slice per category bucket.
//
// Slices:
//   - Ordered by total_seconds DESC (server sort order is preserved as-is).
//   - Filled using `LANE_TAXONOMY[category].hex` so colours match the rest of
//     the Chronicles UI.
//
// Tooltip shows: category label, total_seconds (formatted as H h M m),
// episode_count, and percentage of total.
//
// Empty state: renders a plain text notice when buckets is empty.
// ---------------------------------------------------------------------------

import type { ChroniclerCategoryBucket } from "@/api/types"
import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import { LANE_TAXONOMY } from "./lane-taxonomy"
import type { Category } from "./lane-taxonomy"
import {
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  type TooltipProps,
} from "recharts"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PieSliceDatum {
  name: string
  value: number
  hex: string
  episodeCount: number
  category: string
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Format a duration in seconds as "Xh Ym" (e.g. "2h 15m"). */
function formatDuration(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  if (hours === 0) return `${minutes}m`
  if (minutes === 0) return `${hours}h`
  return `${hours}h ${minutes}m`
}

/** Map a raw category string to its taxonomy entry, falling back to "other". */
function resolveCategory(category: string) {
  return LANE_TAXONOMY[(category as Category) in LANE_TAXONOMY ? (category as Category) : "other"]
}

/** Convert `ChroniclerCategoryBucket[]` to recharts pie data. */
function toBuckets(buckets: ChroniclerCategoryBucket[]): PieSliceDatum[] {
  return buckets.map((b) => {
    const lane = resolveCategory(b.category)
    return {
      name: lane.label,
      value: b.total_seconds,
      hex: lane.hex,
      episodeCount: b.episode_count,
      category: b.category,
    }
  })
}

// ---------------------------------------------------------------------------
// Custom Tooltip
// ---------------------------------------------------------------------------

interface CustomTooltipPayload {
  name: string
  value: number
  payload: PieSliceDatum
}

function CustomTooltip({ active, payload }: TooltipProps<number, string>) {
  if (!active || !payload || payload.length === 0) return null
  const entry = payload[0] as CustomTooltipPayload
  const { name, value, payload: slice } = entry
  const totalInChart = (payload[0] as { payload: { _total?: number } }).payload._total ?? value
  const pct = totalInChart > 0 ? ((value / totalInChart) * 100).toFixed(1) : "0.0"

  return (
    <div
      data-testid="pie-tooltip"
      className="rounded-md border bg-popover px-3 py-2 text-sm shadow-md"
    >
      <p className="font-semibold">{name}</p>
      <p className="text-muted-foreground">{formatDuration(value)}</p>
      <p className="text-muted-foreground">{slice.episodeCount} episode{slice.episodeCount !== 1 ? "s" : ""}</p>
      <p className="text-muted-foreground">{pct}% of total</p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function PieChartSkeleton() {
  return (
    <div
      className="flex h-64 items-center justify-center"
      data-testid="pie-skeleton"
      role="status"
      aria-label="Loading pie chart"
    >
      {/* Circle placeholder for the pie */}
      <Skeleton className="h-48 w-48 rounded-full" />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Error fallback
// ---------------------------------------------------------------------------

function PieChartErrorFallback({ onRetry }: { onRetry?: () => void }) {
  return (
    <div
      className="flex h-48 flex-col items-center justify-center gap-3 text-sm text-muted-foreground"
      data-testid="pie-error"
    >
      <p>Failed to load category breakdown.</p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState() {
  return (
    <div
      data-testid="pie-empty-state"
      className="flex items-center justify-center h-48 text-sm text-muted-foreground italic"
    >
      No activity recorded for this window.
    </div>
  )
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface AggregatePieChartProps {
  /** Category buckets from `useChroniclesAggregates`, already sorted by total_seconds DESC. */
  buckets: ChroniclerCategoryBucket[]
  /** Show loading skeleton while data is being fetched. */
  isLoading?: boolean
  /** Show error fallback when the query failed. */
  isError?: boolean
  /** Called when the user clicks the retry button in the error state. */
  onRetry?: () => void
}

/**
 * Renders a recharts PieChart with one slice per category bucket.
 *
 * Slice colours come from `LANE_TAXONOMY[category].hex`.
 * Slices are displayed in API sort order (total_seconds DESC).
 */
export function AggregatePieChart({ buckets, isLoading, isError, onRetry }: AggregatePieChartProps) {
  if (isLoading) {
    return <PieChartSkeleton />
  }

  if (isError) {
    return <PieChartErrorFallback onRetry={onRetry} />
  }

  if (buckets.length === 0) {
    return <EmptyState />
  }

  const totalSeconds = buckets.reduce((acc, b) => acc + b.total_seconds, 0)
  // Attach _total to each datum so the tooltip can compute percentage without
  // closing over an external value.
  const data = toBuckets(buckets).map((d) => ({ ...d, _total: totalSeconds }))

  return (
    <div data-testid="pie-chart-container" className="w-full h-64">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            cx="50%"
            cy="50%"
            outerRadius={100}
            label={({ name, percent }) =>
              `${name} ${(percent * 100).toFixed(0)}%`
            }
            labelLine={false}
          >
            {data.map((entry) => (
              <Cell key={entry.category} fill={entry.hex} />
            ))}
          </Pie>
          <Tooltip content={<CustomTooltip />} />
        </PieChart>
      </ResponsiveContainer>
    </div>
  )
}
