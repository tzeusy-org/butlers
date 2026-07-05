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
// Empty state: renders a plain text notice when buckets is empty AND there is
// no untracked time either (nothing has been queried/recorded at all).
//
// Untracked slice (bu-whhll.13 — pie-chart honesty): the pie previously
// renormalised over tracked evidence only, so e.g. a 4h-evidence day rendered
// as a full day. `untrackedSeconds` (from `CategoryBuckets.untracked_seconds`)
// is rendered as an extra slice sized against the same waking-window total,
// hatched/neutral via `neutralDensityColor` — deliberately NOT a
// `LANE_TAXONOMY` entry/lane hue, since "untracked" is a chart-only derived
// pseudo-slice, not an Activity lane the backend ever attaches to an episode.
// ---------------------------------------------------------------------------

import type { ChroniclerCategoryBucket } from "@/api/types"
import { neutralDensityColor } from "@/lib/chart-colors"
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
  type TooltipContentProps,
} from "recharts"

// SVG pattern id for the untracked slice's hatch fill (see <defs> below).
const UNTRACKED_PATTERN_ID = "pie-untracked-hatch"
const UNTRACKED_LABEL = "Untracked"

// All categories sorted by sortOrder — used to render the complete legend.
const ALL_CATEGORIES = (Object.keys(LANE_TAXONOMY) as Category[]).sort(
  (a, b) => LANE_TAXONOMY[a].sortOrder - LANE_TAXONOMY[b].sortOrder,
)

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PieSliceDatum {
  name: string
  value: number
  hex: string
  episodeCount: number
  category: string
  /** True only for the synthetic untracked slice (see file header). */
  isUntracked?: boolean
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

function CustomTooltip({ active, payload }: TooltipContentProps<number, string>) {
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
      {slice.isUntracked ? (
        <p className="text-muted-foreground italic" data-testid="pie-tooltip-untracked-note">
          No recorded evidence in this window.
        </p>
      ) : (
        <p className="text-muted-foreground">{slice.episodeCount} episode{slice.episodeCount !== 1 ? "s" : ""}</p>
      )}
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
// All-categories legend (bu-p4vd3)
//
// Renders every LANE_TAXONOMY entry as a legend row. Categories with data are
// shown at full opacity with their total time; empty categories are shown at
// reduced opacity with a "No data this period" affordance so it is clear
// they are intentionally absent rather than missing from the render.
// ---------------------------------------------------------------------------

interface AllCategoriesLegendProps {
  /** Set of category strings that have at least one bucket. */
  activeCategories: Set<string>
  /** Map of category → formatted time label for active entries. */
  categoryLabels: Map<string, string>
}

function AllCategoriesLegend({ activeCategories, categoryLabels }: AllCategoriesLegendProps) {
  return (
    <div
      className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-xs"
      data-testid="pie-all-categories-legend"
      aria-label="Category legend"
    >
      {ALL_CATEGORIES.map((category) => {
        const lane = LANE_TAXONOMY[category]
        const hasData = activeCategories.has(category)
        const timeLabel = categoryLabels.get(category)
        return (
          <div
            key={category}
            className={[
              "flex items-center gap-1",
              hasData ? "text-foreground" : "text-muted-foreground/40",
            ].join(" ")}
            data-testid={`pie-legend-${category}`}
            aria-label={
              hasData
                ? `${lane.label}: ${timeLabel}`
                : `${lane.label}: no data this period`
            }
          >
            <span
              className="inline-block w-2.5 h-2.5 rounded-sm shrink-0"
              style={{
                backgroundColor: lane.hex,
                opacity: hasData ? 1 : 0.3,
              }}
            />
            <span>{lane.label}</span>
            {hasData && timeLabel && (
              <span className="text-muted-foreground">{timeLabel}</span>
            )}
            {!hasData && (
              <span
                className="italic text-[10px]"
                data-testid={`pie-legend-empty-${category}`}
              >
                —
              </span>
            )}
          </div>
        )
      })}
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
      className="flex flex-col items-center justify-center h-48 text-sm text-muted-foreground italic gap-4"
    >
      <span>No activity recorded for this window.</span>
      <AllCategoriesLegend
        activeCategories={new Set()}
        categoryLabels={new Map()}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface AggregatePieChartProps {
  /** Category buckets from `useChroniclesAggregates`, already sorted by total_seconds DESC. */
  buckets: ChroniclerCategoryBucket[]
  /**
   * `CategoryBuckets.untracked_seconds` — waking-window seconds not covered
   * by any activity-layer episode (bu-whhll.13). Defaults to 0 so older
   * cached responses / stale fixtures without the field render exactly as
   * before (no untracked slice).
   */
  untrackedSeconds?: number
  /** Show loading skeleton while data is being fetched. */
  isLoading?: boolean
  /** Show error fallback when the query failed. */
  isError?: boolean
  /** Called when the user clicks the retry button in the error state. */
  onRetry?: () => void
}

/**
 * Renders a recharts PieChart with one slice per category bucket, plus an
 * honest hatched "Untracked" slice sized from `untrackedSeconds` when > 0.
 *
 * Slice colours come from `LANE_TAXONOMY[category].hex`; the untracked slice
 * deliberately uses a neutral-density hatch instead (see file header).
 * Category slices are displayed in API sort order (total_seconds DESC); the
 * untracked slice, when present, is always last.
 */
export function AggregatePieChart({
  buckets,
  untrackedSeconds = 0,
  isLoading,
  isError,
  onRetry,
}: AggregatePieChartProps) {
  if (isLoading) {
    return <PieChartSkeleton />
  }

  if (isError) {
    return <PieChartErrorFallback onRetry={onRetry} />
  }

  const untracked = Math.max(0, untrackedSeconds)

  // Only genuinely empty (no tracked evidence AND no untracked time either —
  // i.e. nothing to show at all) falls back to the empty state. A day with
  // zero tracked evidence but a positive untracked_seconds is not "empty": it
  // is a fully-untracked day, and should render as a 100% untracked slice
  // rather than the "no activity" notice.
  if (buckets.length === 0 && untracked <= 0) {
    return <EmptyState />
  }

  const bucketSeconds = buckets.reduce((acc, b) => acc + b.total_seconds, 0)
  const totalSeconds = bucketSeconds + untracked
  // Attach _total to each datum so the tooltip can compute percentage without
  // closing over an external value.
  const data: Array<PieSliceDatum & { _total: number }> = toBuckets(buckets).map((d) => ({
    ...d,
    _total: totalSeconds,
  }))
  if (untracked > 0) {
    data.push({
      name: UNTRACKED_LABEL,
      value: untracked,
      hex: "", // unused for the untracked slice — it renders via the hatch pattern below
      episodeCount: 0,
      category: "untracked",
      isUntracked: true,
      _total: totalSeconds,
    })
  }

  // Build legend helpers: which categories are active, and their formatted labels.
  const activeCategories = new Set(buckets.map((b) => b.category))
  const categoryLabels = new Map(
    buckets.map((b) => [b.category, formatDuration(b.total_seconds)]),
  )

  return (
    <div data-testid="pie-chart-container" className="w-full">
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            {/* Hatch pattern for the untracked slice — neutral-density, not a
                lane hue or severity colour (see file header). */}
            <defs>
              <pattern
                id={UNTRACKED_PATTERN_ID}
                patternUnits="userSpaceOnUse"
                width={8}
                height={8}
                patternTransform="rotate(45)"
              >
                <rect
                  width={8}
                  height={8}
                  fill={neutralDensityColor(0.15)}
                  fillOpacity={0.5}
                />
                <line
                  x1={0}
                  y1={0}
                  x2={0}
                  y2={8}
                  stroke={neutralDensityColor(0.75)}
                  strokeWidth={3}
                  strokeOpacity={0.7}
                />
              </pattern>
            </defs>
            <Pie
              data={data}
              dataKey="value"
              nameKey="name"
              cx="50%"
              cy="50%"
              outerRadius={100}
              label={({ name, percent }) =>
                `${name} ${((percent ?? 0) * 100).toFixed(0)}%`
              }
              labelLine={false}
            >
              {data.map((entry) => (
                <Cell
                  key={entry.category}
                  fill={entry.isUntracked ? `url(#${UNTRACKED_PATTERN_ID})` : entry.hex}
                />
              ))}
            </Pie>
            <Tooltip<number, string> content={(props) => <CustomTooltip {...props} />} />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <AllCategoriesLegend
        activeCategories={activeCategories}
        categoryLabels={categoryLabels}
      />
      {untracked > 0 && (
        <div
          className="flex items-center gap-1 mt-2 text-xs text-muted-foreground"
          data-testid="pie-untracked-legend"
          aria-label={`${UNTRACKED_LABEL}: ${formatDuration(untracked)}, no recorded evidence`}
        >
          <span
            className="inline-block w-2.5 h-2.5 rounded-sm shrink-0"
            aria-hidden="true"
            style={{
              backgroundImage: `repeating-linear-gradient(45deg, ${neutralDensityColor(0.75)}, ${neutralDensityColor(0.75)} 1px, ${neutralDensityColor(0.15)} 1px, ${neutralDensityColor(0.15)} 3px)`,
            }}
          />
          <span>{UNTRACKED_LABEL}</span>
          <span>{formatDuration(untracked)}</span>
        </div>
      )}
    </div>
  )
}
