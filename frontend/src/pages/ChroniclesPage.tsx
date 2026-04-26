// ---------------------------------------------------------------------------
// ChroniclesPage — Chronicles dashboard (bu-ig72b)
//
// Widget regions:
//   - Gantt area (bu-ig72b.5 / bu-ig72b.28)
//   - Map area (bu-ig72b.14)
//   - Scrubber (bu-ig72b.23) — single time-scrubber driving Gantt cursor and map playhead
//   - Aggregations area (bu-ig72b.7, bu-ig72b.33)
//
// Time window state lives here and flows down to all three widget regions
// via props so each widget can filter its data to the selected range.
// Auto-refresh is gated by `pollingDisabled` from `useTimeWindow`:
//   - Today / recent windows (pollingDisabled=false): 30s polling by default.
//   - Older windows (pollingDisabled=true): no polling.
//
// Playhead state is lifted here (D12):
//   - `scrubberMs`  — raw slider position
//   - `snappedMs`   — snapped to nearest point event (drives Gantt cursor)
//   - `playheadPoint` — {lng, lat} for the snapped point (drives map marker)
//
// OwnTracks trail (bu-ig72b.35):
//   - `trailPoints` — derived from pointEvents; sorted by canonical_occurred_at;
//     sensitive events excluded. Passed to MapWidget as a connected line layer.
//     Empty when the sibling adapter (bu-ahs9z) has not yet landed.
// ---------------------------------------------------------------------------

import { useCallback, useMemo, useState } from "react"
import { useTimeWindow } from "@/hooks/use-time-window"
import { TimeWindowPicker } from "@/components/chronicles/TimeWindowPicker"
import { MapWidget } from "@/components/chronicles/MapWidget"
import { GanttSwimlane } from "@/components/chronicles/GanttSwimlane"
import { EpisodeDrawer } from "@/components/chronicles/EpisodeDrawer"
import { Scrubber } from "@/components/chronicles/Scrubber"
import { SourceStateBadgeStrip } from "@/components/chronicles/SourceStateBadgeStrip"
import { AggregateStackedBar } from "@/components/chronicles/AggregateStackedBar"
import { AggregatePieChart } from "@/components/chronicles/AggregatePieChart"
import { StreakCallouts } from "@/components/chronicles/StreakCallouts"
import { useChroniclesAggregates, useChroniclesPointEvents } from "@/hooks/use-chronicles"
import { useAutoRefresh } from "@/hooks/use-auto-refresh"
import { AutoRefreshToggle } from "@/components/ui/auto-refresh-toggle"
import { ManualRefreshButton } from "@/components/chronicles/ManualRefreshButton"
import type { ChroniclerEventsParams } from "@/api/types"
import { MapPanContext, useMapPanContextValue } from "@/components/chronicles/map-pan-store"

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ChroniclesPage() {
  const timeWindow = useTimeWindow()
  const autoRefreshControl = useAutoRefresh(30_000)
  // Map pan store: Gantt episode clicks wire through this context to the MapWidget.
  const mapPanValue = useMapPanContextValue()

  // Episode drawer state — holds the ID of the clicked episode, or null.
  const [selectedEpisodeId, setSelectedEpisodeId] = useState<string | null>(null)

  const handleEpisodeClick = useCallback((episodeId: string) => {
    setSelectedEpisodeId(episodeId)
  }, [])

  const handleDrawerClose = useCallback(() => {
    setSelectedEpisodeId(null)
  }, [])

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

  // Point events for the scrubber (D12). Fetch up to 500 events per window.
  // source_name is not filtered here — all point events in the window are
  // eligible for scrubbing. OwnTracks events carry lat/lon in payload.
  const pointEventsParams: ChroniclerEventsParams = useMemo(
    () => ({
      since: windowFrom,
      until: windowTo,
      limit: 500,
    }),
    [windowFrom, windowTo],
  )

  const { data: pointEventsData } = useChroniclesPointEvents(pointEventsParams, {
    refetchInterval,
  })
  const pointEvents = useMemo(() => pointEventsData?.data ?? [], [pointEventsData])

  // OwnTracks trail (bu-ig72b.35): derive {lng, lat} pairs from point events.
  //
  // Filter: exclude events where canonical_privacy === 'sensitive' (masking
  // policy per bu-ig72b.29) and events without lat/lon coordinates in payload.
  // Sort:   ascending canonical_occurred_at so the line connects in time order.
  // Tombstoned events are excluded by the hook default (include_tombstoned=false).
  //
  // This is intentionally empty until the OwnTracks projection adapter
  // (bu-ahs9z) lands and starts emitting point events with lat/lon payloads.
  const trailPoints = useMemo(() => {
    return pointEvents
      .filter((ev) => ev.canonical_privacy !== "sensitive")
      .filter((ev) => {
        const lat = ev.payload.lat
        const lon = ev.payload.lon ?? ev.payload.lng
        return typeof lat === "number" && typeof lon === "number"
      })
      .sort(
        (a, b) =>
          new Date(a.canonical_occurred_at).getTime() -
          new Date(b.canonical_occurred_at).getTime(),
      )
      .map((ev) => ({
        lng: (ev.payload.lon ?? ev.payload.lng) as number,
        lat: ev.payload.lat as number,
      }))
  }, [pointEvents])

  // Playhead state (D12): lifted here and passed down to Gantt + MapWidget.
  // snappedMs drives the Gantt cursor line.
  // playheadPoint drives the map marker (derived from snapped point event payload).
  const [snappedMs, setSnappedMs] = useState<number | null>(null)
  const [playheadPoint, setPlayheadPoint] = useState<{ lng: number; lat: number } | null>(null)

  const handleScrub = useCallback(
    (_scrubberMs: number, newSnappedMs: number | null) => {
      setSnappedMs(newSnappedMs)

      // Resolve the snapped point event to extract {lng, lat} from its payload.
      // Only events from the owntracks source carry lat/lon coordinates.
      if (newSnappedMs === null) {
        setPlayheadPoint(null)
        return
      }

      const snappedEvent = pointEvents.find(
        (ev) => new Date(ev.canonical_occurred_at).getTime() === newSnappedMs,
      )
      if (!snappedEvent) {
        setPlayheadPoint(null)
        return
      }

      // Never plot coordinates for sensitive events — consistent with MapWidgetInner's
      // sensitive-point exclusion and the bu-ig72b.29 masking policy.
      if (snappedEvent.canonical_privacy === "sensitive") {
        setPlayheadPoint(null)
        return
      }

      const lat = snappedEvent.payload.lat
      const lon = snappedEvent.payload.lon ?? snappedEvent.payload.lng
      if (typeof lat === "number" && typeof lon === "number") {
        setPlayheadPoint({ lng: lon, lat })
      } else {
        setPlayheadPoint(null)
      }
    },
    [pointEvents],
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

      {/* Time window picker + manual refresh for static windows */}
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <TimeWindowPicker window={timeWindow} />
        </div>
        {timeWindow.pollingDisabled && <ManualRefreshButton />}
      </div>

      {/* Scrubber (D12) — single playhead control for Gantt cursor and map marker */}
      <section aria-label="Scrubber" className="rounded-lg border bg-card px-6 py-4">
        {/* key resets scrubber state when the time window changes */}
        <Scrubber
          key={`${windowFrom}-${windowTo}`}
          windowStart={timeWindow.from}
          windowEnd={timeWindow.to}
          pointEvents={pointEvents}
          onScrub={handleScrub}
        />
      </section>

      {/* Gantt + Map share the MapPanContext so calendar episode clicks can pan the map. */}
      <MapPanContext.Provider value={mapPanValue}>
        {/* Gantt area */}
        <section aria-label="Gantt area" className="rounded-lg border bg-card p-6">
          <h2 className="text-sm font-medium text-muted-foreground mb-4">Gantt area</h2>
          <GanttSwimlane
            windowStart={timeWindow.from}
            windowEnd={timeWindow.to}
            refetchInterval={refetchInterval}
            onEpisodeClick={handleEpisodeClick}
            cursorMs={snappedMs}
          />
        </section>

        {/* Map area */}
        <section aria-label="Map area" className="rounded-lg border bg-card p-6">
          <h2 className="text-sm font-medium text-muted-foreground mb-4">Map area</h2>
          <MapWidget points={[]} playheadPoint={playheadPoint} trailPoints={trailPoints} />
        </section>
      </MapPanContext.Provider>

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

      {/* Episode drilldown drawer — Tier-2 LLM path (RFC 0014 §D5) */}
      <EpisodeDrawer
        episodeId={selectedEpisodeId}
        open={selectedEpisodeId !== null}
        onClose={handleDrawerClose}
      />
    </div>
  )
}

