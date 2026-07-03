// ---------------------------------------------------------------------------
// CostStripeChart — daily cost-over-time chart (bu-e8b5w.5)
//
// Renders real cost_usd per day as a single bar per day.
//
// bu-86c4c.1 (truth amnesty): this chart previously split each day's total
// into per-butler stripes by applying the *period-aggregate* by_butler
// proportions uniformly to every day — every bar ended up with identical
// butler ratios, and the tooltip printed those fabricated per-butler dollar
// values to 4 decimal places as if they were measured. The backend's
// GET /api/spend/daily endpoint fans out per-butler daily stats internally
// (see spend.py:_get_butler_daily_stats) but discards butler identity when
// merging across butlers into the single-series response, so there is no
// real per-butler-per-day figure to render today. Rather than keep
// inventing one, this renders an honest single-color total bar per day;
// the per-butler breakdown lives in CostBreakdownTable as a period
// aggregate, never smeared across days.
//
// Follow-up (not yet implemented): extend /api/spend/daily to optionally
// preserve butler identity per day so this chart can stack real per-butler
// values instead of a single total.
// ---------------------------------------------------------------------------

import {
  Bar,
  BarChart,
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

  const entry = payload[0]
  if (!entry || entry.value == null || entry.value === 0) return null

  return (
    <div className="rounded-md border bg-popover p-3 text-sm shadow-md">
      <p className="mb-2 font-medium">{formatDate(label)}</p>
      <div className="flex items-center gap-2">
        <span
          className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm"
          style={{ backgroundColor: entry.color }}
        />
        <span className="text-muted-foreground">Total:</span>
        <span className="ml-auto font-mono">{formatCostUsd(entry.value)}</span>
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
        <BarChart data={data} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
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
          <Bar dataKey="cost_usd" fill={chartColor()} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
