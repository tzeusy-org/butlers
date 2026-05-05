// ---------------------------------------------------------------------------
// CostStripeChart — stacked bar chart of daily cost over time (bu-e8b5w.5)
//
// Shows cost_usd per day as stacked bars, one per butler.
// Each day's bar is proportionally divided by the butler share from the
// period summary's `by_butler` map. When all butlers share equally (or when
// summary is unavailable), each day renders as a single primary-colored bar.
//
// Color: deterministic mapping butler-name -> --category-1..8 CSS tokens,
// matching the SessionStripeChart visual idiom.
// ---------------------------------------------------------------------------

import {
  Bar,
  BarChart,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { ChartSkeleton } from "@/components/skeletons"
import type { DailyCost } from "@/api/types"

// ---------------------------------------------------------------------------
// Color tokens — matches SessionStripeChart
// ---------------------------------------------------------------------------

const CATEGORY_VARS = [
  "var(--category-1)",
  "var(--category-2)",
  "var(--category-3)",
  "var(--category-4)",
  "var(--category-5)",
  "var(--category-6)",
  "var(--category-7)",
  "var(--category-8)",
] as const

/** Deterministic butler-name to color. Uses the sorted name order for stability. */
function butlerColor(name: string, orderedNames: string[]): string {
  const idx = orderedNames.indexOf(name)
  if (idx !== -1) return CATEGORY_VARS[idx % CATEGORY_VARS.length]
  // Fallback: hash for unlisted butlers
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash)
  }
  return CATEGORY_VARS[Math.abs(hash) % CATEGORY_VARS.length]
}

// ---------------------------------------------------------------------------
// Data helpers
// ---------------------------------------------------------------------------

function formatDate(dateStr: string): string {
  const [year, month, day] = dateStr.split("-").map(Number)
  const d = new Date(Date.UTC(year, month - 1, day, 12, 0, 0))
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" })
}

function formatCost(value: number): string {
  return `$${value.toFixed(4)}`
}

// ---------------------------------------------------------------------------
// Row type for recharts
// ---------------------------------------------------------------------------

interface CostBucketRow {
  date: string
  /** Per-butler cost for this day (keyed by butler name). */
  [butlerName: string]: string | number
}

/**
 * Expand each DailyCost entry into per-butler slices using the aggregate
 * `by_butler` proportions from the summary. The proportions are applied
 * uniformly across days since we only have aggregate totals.
 *
 * When `byButler` is empty (no summary data yet), falls back to a single
 * "_total" key so the chart still renders.
 */
function buildRows(
  dailyCosts: DailyCost[],
  byButler: Record<string, number>,
): { rows: CostBucketRow[]; orderedNames: string[] } {
  const butlerNames = Object.keys(byButler).sort()
  const periodTotal = Object.values(byButler).reduce((sum, v) => sum + v, 0)

  if (butlerNames.length === 0 || periodTotal === 0) {
    // No butler breakdown available — show total as a single series.
    const rows: CostBucketRow[] = dailyCosts.map((d) => ({
      date: d.date,
      _total: d.cost_usd,
    }))
    return { rows, orderedNames: ["_total"] }
  }

  // Apply proportions: each day's total is split by butler share.
  const shares = Object.fromEntries(
    butlerNames.map((name) => [name, byButler[name] / periodTotal]),
  )

  const rows: CostBucketRow[] = dailyCosts.map((d) => {
    const row: CostBucketRow = { date: d.date }
    for (const name of butlerNames) {
      row[name] = Math.round(d.cost_usd * (shares[name] ?? 0) * 1_000_000) / 1_000_000
    }
    return row
  })

  return { rows, orderedNames: butlerNames }
}

// ---------------------------------------------------------------------------
// Tooltip
// ---------------------------------------------------------------------------

interface TooltipEntry {
  dataKey: string
  value: number
  color: string
}

interface CostStripeTooltipProps {
  active?: boolean
  label?: string
  payload?: TooltipEntry[]
}

function CostStripeTooltip({ active, label, payload }: CostStripeTooltipProps) {
  if (!active || !payload || payload.length === 0 || !label) return null

  const entries = payload.filter((p) => p.value > 0).sort((a, b) => b.value - a.value)
  if (entries.length === 0) return null

  const total = entries.reduce((sum, p) => sum + p.value, 0)

  return (
    <div className="rounded-md border bg-popover p-3 text-sm shadow-md">
      <p className="mb-2 font-medium">{formatDate(label)}</p>
      {entries.map((p) => (
        <div key={p.dataKey} className="flex items-center gap-2">
          <span
            className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm"
            style={{ backgroundColor: p.color }}
          />
          <span className="text-muted-foreground">
            {p.dataKey === "_total" ? "Total" : p.dataKey}:
          </span>
          <span className="ml-auto font-mono">{formatCost(p.value)}</span>
        </div>
      ))}
      {entries.length > 1 && (
        <div className="mt-2 border-t pt-2 flex justify-between text-muted-foreground">
          <span>Total</span>
          <span className="font-mono">{formatCost(total)}</span>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface CostStripeChartProps {
  /** Daily cost time series. */
  data: DailyCost[]
  /** Per-butler aggregate totals used for proportional stripe coloring. */
  byButler?: Record<string, number>
  isLoading?: boolean
  isError?: boolean
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function CostStripeChart({
  data,
  byButler = {},
  isLoading,
  isError,
}: CostStripeChartProps) {
  if (isLoading) {
    return <ChartSkeleton height="h-[256px]" testId="cost-stripe-skeleton" />
  }

  if (isError) {
    return (
      <div
        className="flex h-[256px] items-center justify-center text-sm text-muted-foreground"
        data-testid="cost-stripe-error"
      >
        Failed to load cost data.
      </div>
    )
  }

  if (data.length === 0) {
    return (
      <div
        className="flex h-[256px] items-center justify-center text-sm text-muted-foreground"
        data-testid="cost-stripe-empty"
      >
        No cost data for the selected period
      </div>
    )
  }

  const { rows, orderedNames } = buildRows(data, byButler)

  return (
    <div data-testid="cost-stripe-chart">
      <ResponsiveContainer width="100%" height={256}>
        <BarChart data={rows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
          <XAxis
            dataKey="date"
            tickFormatter={formatDate}
            tick={{ fontSize: 10 }}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tickFormatter={(v: number) => `$${v.toFixed(2)}`}
            tick={{ fontSize: 10 }}
            tickLine={false}
            axisLine={false}
            width={52}
          />
          <Tooltip content={<CostStripeTooltip />} />
          {orderedNames.length > 1 && (
            <Legend
              iconSize={10}
              wrapperStyle={{ fontSize: 11 }}
              formatter={(value) => (value === "_total" ? "Total" : value)}
            />
          )}
          {orderedNames.map((name) => (
            <Bar
              key={name}
              dataKey={name}
              stackId="day"
              fill={
                name === "_total"
                  ? "hsl(var(--primary))"
                  : butlerColor(name, orderedNames)
              }
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
