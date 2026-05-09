// ---------------------------------------------------------------------------
// ChroniclesDrilldownPanel (bu-i29ix)
//
// The editorial /chronicles landing surface keeps the page quiet: Voice
// briefing, KPI strip, attention list, recent-days index. The Gantt, Map,
// Scrubber, aggregations charts, source-state strip, streak callouts, and
// EpisodeDrawer that used to be the primary view are mounted here as a
// collapsible drilldown panel below the editorial fold.
//
// The panel is closed by default. Opening it lazy-loads the heavy widgets
// (Gantt and Map already self-lazy via React.lazy / dynamic import).
// ---------------------------------------------------------------------------

import { useCallback, useMemo, useState } from "react";

import { useTimeWindow } from "@/hooks/use-time-window";
import {
  useChroniclesAggregates,
  useChroniclesPointEvents,
} from "@/hooks/use-chronicles";
import { useAutoRefresh } from "@/hooks/use-auto-refresh";
import { useTimezone } from "@/components/ui/timezone-context";
import { Button } from "@/components/ui/button";
import { TimeWindowPicker } from "@/components/workspace/TimeWindowPicker";
import { Scrubber } from "@/components/workspace/Scrubber";
import {
  MapPanContext,
  useMapPanContextValue,
} from "@/components/workspace/map-pan-store";
import {
  interpolatePlayhead,
  type TimedTrailPoint,
} from "@/components/workspace/playhead-interp";
import { GanttSwimlane } from "@/components/chronicles/GanttSwimlane";
import { FloatingMapMinimap } from "@/components/chronicles/FloatingMapMinimap";
import { EpisodeDrawer } from "@/components/chronicles/EpisodeDrawer";
import { SourceStateBadgeStrip } from "@/components/chronicles/SourceStateBadgeStrip";
import { AggregateStackedBar } from "@/components/chronicles/AggregateStackedBar";
import { AggregatePieChart } from "@/components/chronicles/AggregatePieChart";
import { StreakCallouts } from "@/components/chronicles/StreakCallouts";
import { ManualRefreshButton } from "@/components/chronicles/ManualRefreshButton";
import { AutoRefreshToggle } from "@/components/ui/auto-refresh-toggle";

import type { ChroniclerEventsParams } from "@/api/types";

interface ChroniclesDrilldownPanelProps {
  /** Initial open/closed state. Defaults to closed (editorial-first). */
  defaultOpen?: boolean;
}

export function ChroniclesDrilldownPanel({
  defaultOpen = false,
}: ChroniclesDrilldownPanelProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <section aria-label="Chronicles drilldown" className="space-y-4">
      <div className="flex items-center justify-between">
        <p
          className="tnum uppercase"
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "10px",
            letterSpacing: "0.14em",
            color: "var(--muted-foreground)",
          }}
        >
          Drilldown
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setOpen((prev) => !prev)}
          aria-expanded={open}
        >
          {open ? "Close drilldown" : "Open drilldown"}
        </Button>
      </div>
      {open ? <DrilldownBody /> : null}
    </section>
  );
}

function DrilldownBody() {
  // Timezone provided by AppTimezoneProvider (bu-ldj6y).
  const ownerTz = useTimezone();
  const timeWindow = useTimeWindow(ownerTz);
  const autoRefreshControl = useAutoRefresh(30_000);
  const mapPanValue = useMapPanContextValue();

  const [selectedEpisodeId, setSelectedEpisodeId] = useState<string | null>(null);
  const handleEpisodeClick = useCallback((episodeId: string) => {
    setSelectedEpisodeId(episodeId);
  }, []);
  const handleDrawerClose = useCallback(() => {
    setSelectedEpisodeId(null);
  }, []);

  const refetchInterval = timeWindow.pollingDisabled
    ? (false as const)
    : autoRefreshControl.refetchInterval;

  const windowFrom = timeWindow.from.toISOString();
  const windowTo = timeWindow.to.toISOString();

  const aggregateParams = useMemo(
    () => ({ start_at: windowFrom, end_at: windowTo }),
    [windowFrom, windowTo],
  );

  const episodesParams = useMemo(
    () => ({ overlaps_start: windowFrom, overlaps_end: windowTo }),
    [windowFrom, windowTo],
  );

  const pointEventsParams: ChroniclerEventsParams = useMemo(
    () => ({ since: windowFrom, until: windowTo, limit: 500 }),
    [windowFrom, windowTo],
  );

  const { data: pointEventsData } = useChroniclesPointEvents(pointEventsParams, {
    refetchInterval,
  });
  const pointEvents = useMemo(
    () => pointEventsData?.data ?? [],
    [pointEventsData],
  );

  const timedTrail = useMemo<TimedTrailPoint[]>(() => {
    return pointEvents
      .filter((ev) => ev.canonical_privacy !== "sensitive")
      .filter((ev) => {
        const lat = ev.payload.lat;
        const lon = ev.payload.lon ?? ev.payload.lng;
        return typeof lat === "number" && typeof lon === "number";
      })
      .map((ev) => ({
        lng: (ev.payload.lon ?? ev.payload.lng) as number,
        lat: ev.payload.lat as number,
        ms: new Date(ev.canonical_occurred_at).getTime(),
      }))
      .sort((a, b) => a.ms - b.ms);
  }, [pointEvents]);

  const trailPoints = useMemo(
    () => timedTrail.map(({ lng, lat }) => ({ lng, lat })),
    [timedTrail],
  );

  const [snappedMs, setSnappedMs] = useState<number | null>(null);
  const [scrubberMs, setScrubberMs] = useState<number | null>(null);

  const handleScrub = useCallback(
    (newScrubberMs: number, newSnappedMs: number | null) => {
      setSnappedMs(newSnappedMs);
      setScrubberMs(newScrubberMs);
    },
    [],
  );

  const playheadPoint = useMemo(() => {
    if (scrubberMs === null) return null;
    return interpolatePlayhead(scrubberMs, timedTrail);
  }, [scrubberMs, timedTrail]);

  const { byCategory, byDay } = useChroniclesAggregates(
    aggregateParams,
    aggregateParams,
    { refetchInterval, enabled: true },
  );

  const byDayRows = byDay.data ?? [];
  const categoryBuckets = byCategory.data?.data.buckets ?? [];

  function handleByDayRetry() {
    void byDay.refetch();
  }
  function handleByCategoryRetry() {
    void byCategory.refetch();
  }

  return (
    <div className="space-y-6 pb-72">
      <div className="flex items-center justify-end gap-2">
        <ManualRefreshButton timeWindow={timeWindow} />
        {!timeWindow.pollingDisabled && (
          <AutoRefreshToggle
            enabled={autoRefreshControl.enabled}
            interval={autoRefreshControl.interval}
            onToggle={autoRefreshControl.setEnabled}
            onIntervalChange={autoRefreshControl.setInterval}
          />
        )}
      </div>

      <SourceStateBadgeStrip />
      <TimeWindowPicker window={timeWindow} />

      <section
        aria-label="Scrubber"
        className="rounded-lg border bg-card px-6 py-4"
      >
        <Scrubber
          key={`${windowFrom}-${windowTo}`}
          windowStart={timeWindow.from}
          windowEnd={timeWindow.to}
          snapMs={pointEvents.map((e) =>
            new Date(e.canonical_occurred_at).getTime(),
          )}
          tz={ownerTz}
          onScrub={handleScrub}
        />
      </section>

      <MapPanContext.Provider value={mapPanValue}>
        <section
          aria-label="Gantt area"
          className="rounded-lg border bg-card p-6"
        >
          <h2 className="text-sm font-medium text-muted-foreground mb-4">
            Gantt area
          </h2>
          <GanttSwimlane
            windowStart={timeWindow.from}
            windowEnd={timeWindow.to}
            refetchInterval={refetchInterval}
            onEpisodeClick={handleEpisodeClick}
            cursorMs={snappedMs}
          />
        </section>

        <FloatingMapMinimap
          playheadPoint={playheadPoint}
          trailPoints={trailPoints}
        />
      </MapPanContext.Provider>

      <section
        aria-label="Aggregations area"
        className="rounded-lg border bg-card p-6"
      >
        <h2 className="text-sm font-medium text-muted-foreground mb-4">
          Aggregations area
        </h2>
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

      <EpisodeDrawer
        episodeId={selectedEpisodeId}
        open={selectedEpisodeId !== null}
        onClose={handleDrawerClose}
      />
    </div>
  );
}
