// ---------------------------------------------------------------------------
// CostStripeChart — daily cost-over-time chart (bu-e8b5w.5)
//
// bu-86c4c.1 (truth amnesty): this chart used to split each day's total into
// per-butler stripes by applying the *period-aggregate* by_butler
// proportions uniformly to every day — every bar ended up with identical
// butler ratios, fabricating a per-day distribution that never existed. It
// was demoted to an honest single-color total bar per day until real data
// existed.
//
// bu-86c4c.11: GET /api/spend/daily now preserves per-butler identity per
// day (spend.py:get_daily_costs zips the per-butler fan-out against
// `configs` instead of discarding it at the merge step), so this chart
// stacks the REAL per-butler-per-day values when `by_butler` is present on
// the series. Falls back to a single honest total bar when it is absent
// (e.g. all butlers unreachable that day) — never re-fabricates a split.
// ---------------------------------------------------------------------------

import { useMemo } from "react"
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
import type { DailySpend } from "@/api/types"
import { chartColor } from "@/lib/chart-colors"
import { formatCostUsd } from "@/lib/format-cost"

// ---------------------------------------------------------------------------
// Data helpers
// ---------------------------------------------------------------------------

function formatDate(dateStr: string): string {
  const [year, month, day] = dateStr.split("-").map(Number)
  const d = new Date(Date.UTC(year, month - 1, day, 12, 0, 0))
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" })
}

/**
 * Distinct butler names across the series, ordered by total cost descending
 * (most significant butler first — used for both stack order and legend
 * order). Empty when no day carries real by_butler data.
 */
function collectButlers(data: DailySpend[]): string[] {
  const totals = new Map<string, number>()
  for (const day of data) {
    for (const [name, cost] of Object.entries(day.by_butler ?? {})) {
      totals.set(name, (totals.get(name) ?? 0) + cost)
    }
  }
  return Array.from(totals.entries())
    .sort(([, a], [, b]) => b - a)
    .map(([name]) => name)
}

// ---------------------------------------------------------------------------
// Tooltip
// ---------------------------------------------------------------------------

interface TooltipEntry {
  dataKey: string
  value: number
  color: string
  payload: { cost_usd: number }
}

interface CostStripeTooltipProps {
  active?: boolean
  label?: string
  payload?: TooltipEntry[]
}

function CostStripeTooltip({ active, label, payload }: CostStripeTooltipProps) {
  if (!active || !payload || payload.length === 0 || !label) return null

  const total = payload[0]?.payload?.cost_usd ?? 0
  if (total === 0) return null

  // Real per-butler contributions for this day, largest first — only
  // nonzero entries (a butler with $0 that day is omitted, not a fabricated
  // "$0.00" row).
  const rows = payload
    .filter((e) => e.value != null && e.value > 0)
    .sort((a, b) => b.value - a.value)

  return (
    <div className="rounded-md border bg-popover p-3 text-sm shadow-md">
      <p className="mb-2 font-medium">{formatDate(label)}</p>
      <div className="flex flex-col gap-1">
        {rows.length > 1 &&
          rows.map((entry) => (
            <div key={entry.dataKey} className="flex items-center gap-2">
              <span
                className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm"
                style={{ backgroundColor: entry.color }}
              />
              <span className="text-muted-foreground">{entry.dataKey}:</span>
              <span className="ml-auto font-mono">{formatCostUsd(entry.value)}</span>
            </div>
          ))}
        <div className="flex items-center gap-2">
          {rows.length <= 1 && (
            <span
              className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm"
              style={{ backgroundColor: rows[0]?.color ?? chartColor() }}
            />
          )}
          <span className="text-muted-foreground">Total:</span>
          <span className="ml-auto font-mono">{formatCostUsd(total)}</span>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface CostStripeChartProps {
  /** Daily cost time series. */
  data: DailySpend[]
  isLoading?: boolean
  isError?: boolean
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function CostStripeChart({
  data,
  isLoading,
  isError,
}: CostStripeChartProps) {
  const butlers = useMemo(() => collectButlers(data), [data])
  const hasButlerData = butlers.length > 0

  // Flatten each day's by_butler map into top-level numeric keys so recharts
  // can stack one <Bar> per butler (dataKey cannot address nested paths).
  const rows = useMemo(
    () =>
      data.map((d) => {
        const row: Record<string, number | string> = { date: d.date, cost_usd: d.cost_usd }
        for (const name of butlers) {
          row[name] = d.by_butler?.[name] ?? 0
        }
        return row
      }),
    [data, butlers],
  )

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
          {hasButlerData ? (
            <>
              {butlers.map((name, i) => (
                <Bar
                  key={name}
                  dataKey={name}
                  stackId="cost"
                  fill={chartColor(i)}
                  isAnimationActive={false}
                />
              ))}
              <Legend wrapperStyle={{ fontSize: 10 }} iconSize={8} iconType="square" />
            </>
          ) : (
            <Bar dataKey="cost_usd" fill={chartColor()} isAnimationActive={false} />
          )}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
