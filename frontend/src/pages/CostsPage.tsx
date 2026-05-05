// ---------------------------------------------------------------------------
// CostsPage — cost & usage workspace (bu-e8b5w.5)
//
// Workspace archetype: time-windowed cost data with TimeWindowPicker,
// a Scrubber over the cost-over-time chart, and per-butler breakdown.
//
// Layout:
//   - Toolbar: TimeWindowPicker
//   - Summary stats (4 cards: total cost, sessions, input/output tokens)
//   - Primary: CostStripeChart (daily cost with butler-colored stripes)
//   - Scrubber: timeline control over the primary chart
//   - Secondary: CostBreakdownTable (per-butler) + per-day stat cards
// ---------------------------------------------------------------------------

import { useCallback, useMemo, useRef } from "react"

import CostBreakdownTable from "@/components/costs/CostBreakdownTable"
import { CostStripeChart } from "@/components/costs/CostStripeChart"
import { Scrubber } from "@/components/workspace/Scrubber"
import { TimeWindowPicker } from "@/components/workspace/TimeWindowPicker"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Page } from "@/components/ui/page"
import { useCostSummary, useDailyCosts, formatCostDate } from "@/hooks/use-costs"
import { useTimeWindow, OWNER_TZ_DEFAULT } from "@/hooks/use-time-window"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatCost(amount: number): string {
  if (amount < 0.01) return "$0.00"
  return `$${amount.toFixed(2)}`
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

// fmtDate is provided by formatCostDate from @/hooks/use-costs (tz-aware).

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatsCard({ title, value }: { title: string; value: string }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function CostsPage() {
  // Time window state — drives the picker and data hooks.
  const timeWindow = useTimeWindow(OWNER_TZ_DEFAULT)

  const fromStr = formatCostDate(timeWindow.from, OWNER_TZ_DEFAULT)
  const toStr = formatCostDate(timeWindow.to, OWNER_TZ_DEFAULT)

  // Map the date window to the period format the summary API expects.
  // summary uses "today" | "7d" | "30d"; fall back to "30d" for wider windows.
  const period = useMemo(() => {
    const diffDays =
      Math.round((timeWindow.to.getTime() - timeWindow.from.getTime()) / (24 * 60 * 60 * 1000))
    if (diffDays <= 1) return "today"
    if (diffDays <= 7) return "7d"
    return "30d"
  }, [timeWindow.from, timeWindow.to])

  const { data: summaryResponse, isLoading: summaryLoading } = useCostSummary(period)
  const {
    data: dailyResponse,
    isLoading: dailyLoading,
    isError: dailyError,
  } = useDailyCosts(
    timeWindow.from,
    timeWindow.to,
    timeWindow.pollingDisabled ? false : 60_000,
  )

  const summary = summaryResponse?.data
  const dailyData = dailyResponse?.data ?? []

  // Scrubber position — stored in a ref to avoid re-renders on every tick.
  // Promote to state when a dependent chart highlight is wired in.
  const scrubberMsRef = useRef<number | null>(null)
  const handleScrub = useCallback((ms: number) => {
    scrubberMsRef.current = ms
  }, [])

  // Snap points: midday (UTC noon) of each day in the daily cost series.
  const snapMs = useMemo(
    () =>
      dailyData.map((d) => {
        const [year, month, day] = d.date.split("-").map(Number)
        return Date.UTC(year, month - 1, day, 12, 0, 0)
      }),
    [dailyData],
  )

  const windowKey = `${fromStr}-${toStr}`

  return (
    <Page
      archetype="workspace"
      title="Costs & Usage"
      loading={summaryLoading || dailyLoading}
    >
      {/* Time window picker */}
      <TimeWindowPicker window={timeWindow} />

      {/* Summary Stats */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {summaryLoading ? (
          Array.from({ length: 4 }).map((_, i) => (
            <Card key={i}>
              <CardHeader className="pb-2">
                <div className="h-4 w-24 animate-pulse rounded bg-muted" />
              </CardHeader>
              <CardContent>
                <div className="h-8 w-16 animate-pulse rounded bg-muted" />
              </CardContent>
            </Card>
          ))
        ) : (
          <>
            <StatsCard title="Total Cost" value={formatCost(summary?.total_cost_usd ?? 0)} />
            <StatsCard title="Total Sessions" value={String(summary?.total_sessions ?? 0)} />
            <StatsCard
              title="Input Tokens"
              value={formatTokens(summary?.total_input_tokens ?? 0)}
            />
            <StatsCard
              title="Output Tokens"
              value={formatTokens(summary?.total_output_tokens ?? 0)}
            />
          </>
        )}
      </div>

      {/* Primary: cost-over-time chart with butler-colored stripes */}
      <Card>
        <CardHeader>
          <CardTitle>Spending Over Time</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <CostStripeChart
            data={dailyData}
            byButler={summary?.by_butler ?? {}}
            isLoading={dailyLoading}
            isError={dailyError}
          />

          {/* Scrubber over the cost chart */}
          {dailyData.length > 0 && (
            <Scrubber
              key={windowKey}
              windowStart={timeWindow.from}
              windowEnd={timeWindow.to}
              snapMs={snapMs}
              onScrub={handleScrub}
            />
          )}
        </CardContent>
      </Card>

      {/* Secondary: per-butler breakdown */}
      <CostBreakdownTable
        byButler={summary?.by_butler ?? {}}
        totalCost={summary?.total_cost_usd ?? 0}
        isLoading={summaryLoading}
      />
    </Page>
  )
}
