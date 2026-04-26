// ---------------------------------------------------------------------------
// ChroniclesPage — Chronicles dashboard (bu-ig72b)
//
// Widget regions will be filled by follow-up issues:
//   - Gantt area (bu-ig72b.5)
//   - Map area (bu-ig72b.14)
//   - Aggregations area (bu-ig72b.7, bu-ig72b.33)
//
// Time window state lives here and flows down to all three widget regions
// via props so each widget can filter its data to the selected range.
// Auto-refresh is gated by `pollingDisabled` from `useTimeWindow`:
//   - Today / recent windows (pollingDisabled=false): 30s polling by default.
//   - Older windows (pollingDisabled=true): no polling.
// ---------------------------------------------------------------------------

import { useMemo } from "react"
import { useTimeWindow } from "@/hooks/use-time-window"
import { TimeWindowPicker } from "@/components/chronicles/TimeWindowPicker"
import { MapWidget } from "@/components/chronicles/MapWidget"
import { GanttSwimlane } from "@/components/chronicles/GanttSwimlane"
import { SourceStateBadgeStrip } from "@/components/chronicles/SourceStateBadgeStrip"
import { AggregateStackedBar } from "@/components/chronicles/AggregateStackedBar"
import { AggregatePieChart } from "@/components/chronicles/AggregatePieChart"
import { StreakCallouts } from "@/components/chronicles/StreakCallouts"
import { useChroniclesAggregates } from "@/hooks/use-chronicles"
import { useAutoRefresh } from "@/hooks/use-auto-refresh"
import { AutoRefreshToggle } from "@/components/ui/auto-refresh-toggle"

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ChroniclesPage() {
  const timeWindow = useTimeWindow()
  const autoRefreshControl = useAutoRefresh(30_000)

  // When the time window ends more than 24h ago, disable polling entirely.
  // Otherwise use the user-configured interval (pause/resume still respected).
  // Gantt and Aggregations both consume refetchInterval.
  const refetchInterval = timeWindow.pollingDisabled
    ? (false as const)
    : autoRefreshControl.refetchInterval

  const windowFrom = timeWindow.from.toISOString()
  const windowTo = timeWindow.to.toISOString()

  const aggregateParams = useMemo(
    () => ({
      start_at: windowFrom,
      end_at: windowTo,
    }),
    [windowFrom, windowTo],
  )

  // Episodes params use overlaps_start/overlaps_end to fetch all episodes
  // that fall within the active time window.
  const episodesParams = useMemo(
    () => ({
      overlaps_start: windowFrom,
      overlaps_end: windowTo,
    }),
    [windowFrom, windowTo],
  )

  const { byCategory, byDay } = useChroniclesAggregates(aggregateParams, aggregateParams, {
    refetchInterval,
    enabled: true,
  })

  const byDayRows = byDay.data ?? []
  const categoryBuckets = byCategory.data?.data.buckets ?? []

  // Refetch callbacks for error retry buttons
  function handleByDayRetry() { void byDay.refetch() }
  function handleByCategoryRetry() { void byCategory.refetch() }

  return (
    <div className="space-y-6">
      {/* Page heading */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Chronicles</h1>
          <p className="text-muted-foreground mt-1">
            Retrospective view of lived past time reconstructed from butler evidence.
          </p>
        </div>
        {!timeWindow.pollingDisabled && (
          <AutoRefreshToggle
            enabled={autoRefreshControl.enabled}
            interval={autoRefreshControl.interval}
            onToggle={autoRefreshControl.setEnabled}
            onIntervalChange={autoRefreshControl.setInterval}
          />
        )}
      </div>

      {/* Source adapter state badge strip */}
      <SourceStateBadgeStrip />

      {/* Time window picker */}
      <TimeWindowPicker window={timeWindow} />

      {/* Gantt area */}
      <section aria-label="Gantt area" className="rounded-lg border bg-card p-6">
        <h2 className="text-sm font-medium text-muted-foreground mb-4">Gantt area</h2>
        <GanttSwimlane
          windowStart={timeWindow.from}
          windowEnd={timeWindow.to}
          refetchInterval={refetchInterval}
        />
      </section>

      {/* Map area */}
      <section aria-label="Map area" className="rounded-lg border bg-card p-6">
        <h2 className="text-sm font-medium text-muted-foreground mb-4">Map area</h2>
        <MapWidget points={[]} />
      </section>

      {/* Aggregations area */}
      <section aria-label="Aggregations area" className="rounded-lg border bg-card p-6">
        <h2 className="text-sm font-medium text-muted-foreground mb-4">Aggregations area</h2>
        <StreakCallouts
          episodeParams={episodesParams}
          refetchInterval={refetchInterval}
        />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <AggregateStackedBar
            data={byDayRows}
            isLoading={byDay.isLoading}
            isError={byDay.isError}
            onRetry={handleByDayRetry}
          />
          <AggregatePieChart
            buckets={categoryBuckets}
            isLoading={byCategory.isLoading}
            isError={byCategory.isError}
            onRetry={handleByCategoryRetry}
          />
        </div>
      </section>
    </div>
  )
}

