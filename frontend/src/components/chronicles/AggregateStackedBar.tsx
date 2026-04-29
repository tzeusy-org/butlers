// ---------------------------------------------------------------------------
// AggregateStackedBar — stacked bar chart of by-day × category (bu-ig72b.33)
//
// Renders a recharts BarChart with one stacked Bar per category.
// X = calendar day, Y = total_seconds, stacked by category.
// DST days are handled correctly because the server provides per-day buckets;
// this component renders what it receives without timezone arithmetic.
// ---------------------------------------------------------------------------

import {
  Bar,
  BarChart,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import { LANE_TAXONOMY, type Category } from "./lane-taxonomy"
import { pivotByDay } from "./aggregate-stacked-bar-utils"
import type { ChroniclerAggregateByDayRow } from "@/api/types"

// ---------------------------------------------------------------------------
// Category colour lookup — delegates to LANE_TAXONOMY.hex so colours stay in
// sync with the rest of the Chronicles UI without a separate mapping table.
// ---------------------------------------------------------------------------

function categoryColour(category: Category): string {
  return LANE_TAXONOMY[category].hex
}

// ---------------------------------------------------------------------------
// Format helpers
// ---------------------------------------------------------------------------

function formatDay(day: string): string {
  // day is YYYY-MM-DD; parse as a local noon to avoid UTC-offset boundary issues
  const [year, month, date] = day.split("-").map(Number)
  const d = new Date(year, month - 1, date, 12, 0, 0)
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" })
}

function formatSeconds(totalSeconds: number): string {
  const h = Math.floor(totalSeconds / 3600)
  const m = Math.floor((totalSeconds % 3600) / 60)
  if (h === 0) return `${m}m`
  if (m === 0) return `${h}h`
  return `${h}h ${m}m`
}

// ---------------------------------------------------------------------------
// Tooltip content
// ---------------------------------------------------------------------------

interface TooltipPayloadEntry {
  dataKey: string
  value: number
  color: string
}

interface CustomTooltipProps {
  active?: boolean
  label?: string
  payload?: TooltipPayloadEntry[]
}

function StackedBarTooltip({ active, label, payload }: CustomTooltipProps) {
  if (!active || !payload || payload.length === 0 || !label) return null

  const entries = payload
    .filter((p) => p.value > 0)
    .sort((a, b) => b.value - a.value)

  const total = entries.reduce((sum, p) => sum + p.value, 0)

  return (
    <div className="rounded-md border bg-popover p-3 text-sm shadow-md">
      <p className="mb-2 font-medium">{formatDay(label)}</p>
      {entries.map((p) => {
        const cat = p.dataKey as Category
        const label_text = LANE_TAXONOMY[cat]?.label ?? cat
        return (
          <div key={cat} className="flex items-center gap-2">
            <span
              className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm"
              style={{ backgroundColor: p.color }}
            />
            <span className="text-muted-foreground">{label_text}:</span>
            <span className="ml-auto font-mono">{formatSeconds(p.value)}</span>
          </div>
        )
      })}
      {entries.length > 1 && (
        <div className="mt-2 border-t pt-2 flex justify-between text-muted-foreground">
          <span>Total</span>
          <span className="font-mono">{formatSeconds(total)}</span>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sorted categories — render in LANE_TAXONOMY sortOrder
// ---------------------------------------------------------------------------

const SORTED_CATEGORIES = (Object.keys(LANE_TAXONOMY) as Category[]).sort(
  (a, b) => LANE_TAXONOMY[a].sortOrder - LANE_TAXONOMY[b].sortOrder,
)

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function StackedBarSkeleton() {
  return (
    <div
      className="flex h-[280px] flex-col gap-2 py-4"
      data-testid="stacked-bar-skeleton"
      role="status"
      aria-label="Loading stacked bar chart"
    >
      {/* Y-axis + bar columns side by side */}
      <div className="flex h-full gap-2">
        <Skeleton className="w-12 h-full rounded-md" />
        <div className="flex flex-1 items-end gap-1">
          {Array.from({ length: 7 }, (_, i) => (
            <Skeleton
              key={i}
              className="flex-1 rounded-md"
              style={{ height: `${40 + (i % 3) * 20}%` }}
            />
          ))}
        </div>
      </div>
      {/* X-axis labels */}
      <div className="flex gap-1 pl-14">
        {Array.from({ length: 7 }, (_, i) => (
          <Skeleton key={i} className="flex-1 h-3 rounded" />
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Error fallback
// ---------------------------------------------------------------------------

function StackedBarErrorFallback({ onRetry }: { onRetry?: () => void }) {
  return (
    <div
      className="flex h-48 flex-col items-center justify-center gap-3 text-sm text-muted-foreground"
      data-testid="stacked-bar-error"
    >
      <p>Failed to load activity data.</p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface AggregateStackedBarProps {
  data: ChroniclerAggregateByDayRow[]
  /** Show loading skeleton while data is being fetched. */
  isLoading?: boolean
  /** Show error fallback when the query failed. */
  isError?: boolean
  /** Called when the user clicks the retry button in the error state. */
  onRetry?: () => void
}

export function AggregateStackedBar({ data, isLoading, isError, onRetry }: AggregateStackedBarProps) {
  if (isLoading) {
    return <StackedBarSkeleton />
  }

  if (isError) {
    return <StackedBarErrorFallback onRetry={onRetry} />
  }

  if (data.length === 0) {
    return (
      <div
        className="flex h-48 items-center justify-center text-sm text-muted-foreground"
        data-testid="stacked-bar-empty"
      >
        No activity recorded for this window.
      </div>
    )
  }

  const pivoted = pivotByDay(data)

  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={pivoted} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
        <XAxis
          dataKey="day"
          tickFormatter={formatDay}
          tick={{ fontSize: 11 }}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          tickFormatter={(v: number) => formatSeconds(v)}
          tick={{ fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          width={56}
        />
        <Tooltip content={<StackedBarTooltip />} />
        <Legend
          formatter={(value: string) => {
            const cat = value as Category
            return LANE_TAXONOMY[cat]?.label ?? value
          }}
        />
        {SORTED_CATEGORIES.map((cat) => (
          <Bar
            key={cat}
            dataKey={cat}
            stackId="day"
            fill={categoryColour(cat)}
            isAnimationActive={false}
          >
            {pivoted.map((_, i) => (
              <Cell key={i} fill={categoryColour(cat)} />
            ))}
          </Bar>
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}
