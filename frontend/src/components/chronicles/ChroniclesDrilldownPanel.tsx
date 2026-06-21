// ---------------------------------------------------------------------------
// ChroniclesDrilldownPanel
//
// The editorial /chronicles landing keeps the page quiet: Voice briefing, KPI
// strip, attention list, recent-days index. The Gantt timeline, Map, Scrubber,
// breakdown charts, source-state strip, streak callouts, and EpisodeDrawer live
// here, below the editorial fold.
//
// The panel is driven by the page's selected day (a settled past day), so it is
// static: no time-window picker, no auto-refresh. The detail body is always
// shown; its heavy widgets (Gantt and Map) remain self-lazy via React.lazy /
// dynamic import so they stream in without blocking the briefing above.
// ---------------------------------------------------------------------------

import { useCallback, useMemo, useState } from "react";

import { useChroniclesAggregates, useChroniclesPointEvents } from "@/hooks/use-chronicles";
import { dayWindowInTz } from "@/components/chronicles/tz-format";
import { Section } from "@/components/overview/Section";
import { Scrubber } from "@/components/workspace/Scrubber";
import { MapPanContext, useMapPanContextValue } from "@/components/workspace/map-pan-store";
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

import type { ChroniclerEventsParams } from "@/api/types";

interface ChroniclesDrilldownPanelProps {
  /** The selected day (owner-tz calendar date, YYYY-MM-DD). */
  date: string;
  /** Owner IANA timezone for resolving the day window. */
  tz: string;
}

export function ChroniclesDrilldownPanel({ date, tz }: ChroniclesDrilldownPanelProps) {
  return (
    <section
      aria-label="Day detail"
      className="space-y-6 border-t pt-8"
      style={{ borderColor: "var(--border)" }}
    >
      <div id="chronicles-day-detail">
        <DrilldownBody date={date} tz={tz} />
      </div>
    </section>
  );
}

function DrilldownBody({ date, tz }: ChroniclesDrilldownPanelProps) {
  const mapPanValue = useMapPanContextValue();

  // The day window: tz-local midnight boundaries, identical to the backend's
  // day_window_utc for every zone (the day string is treated as naive local
  // midnight in tz, never reinterpreted through a UTC anchor).
  const { from, to } = useMemo(() => dayWindowInTz(date, tz), [date, tz]);

  // A settled past day never changes: polling is off.
  const refetchInterval = false as const;

  const [selectedEpisodeId, setSelectedEpisodeId] = useState<string | null>(null);
  const handleEpisodeClick = useCallback((episodeId: string) => {
    setSelectedEpisodeId(episodeId);
  }, []);
  const handleDrawerClose = useCallback(() => {
    setSelectedEpisodeId(null);
  }, []);

  const windowFrom = from.toISOString();
  const windowTo = to.toISOString();

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
  const pointEvents = useMemo(() => pointEventsData?.data ?? [], [pointEventsData]);

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

  const handleScrub = useCallback((newScrubberMs: number, newSnappedMs: number | null) => {
    setSnappedMs(newSnappedMs);
    setScrubberMs(newScrubberMs);
  }, []);

  const playheadPoint = useMemo(() => {
    if (scrubberMs === null) return null;
    return interpolatePlayhead(scrubberMs, timedTrail);
  }, [scrubberMs, timedTrail]);

  const { byCategory, byDay } = useChroniclesAggregates(aggregateParams, aggregateParams, {
    refetchInterval,
    enabled: true,
  });

  const byDayRows = byDay.data ?? [];
  const categoryBuckets = byCategory.data?.data.buckets ?? [];

  function handleByDayRetry() {
    void byDay.refetch();
  }
  function handleByCategoryRetry() {
    void byCategory.refetch();
  }

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-end">
        <ManualRefreshButton timeWindow={{ from, to }} />
      </div>

      <SourceStateBadgeStrip />

      <MapPanContext.Provider value={mapPanValue}>
        <Section eyebrow="Timeline">
          <div className="space-y-4">
            <Scrubber
              key={`${windowFrom}-${windowTo}`}
              windowStart={from}
              windowEnd={to}
              snapMs={pointEvents.map((e) => new Date(e.canonical_occurred_at).getTime())}
              tz={tz}
              onScrub={handleScrub}
            />
            <GanttSwimlane
              windowStart={from}
              windowEnd={to}
              refetchInterval={refetchInterval}
              onEpisodeClick={handleEpisodeClick}
              cursorMs={snappedMs}
            />
          </div>
        </Section>

        <FloatingMapMinimap playheadPoint={playheadPoint} trailPoints={trailPoints} />
      </MapPanContext.Provider>

      <Section eyebrow="Where the time went">
        <div className="space-y-4">
          <StreakCallouts episodeParams={episodesParams} refetchInterval={refetchInterval} />
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
        </div>
      </Section>

      <EpisodeDrawer
        episodeId={selectedEpisodeId}
        open={selectedEpisodeId !== null}
        onClose={handleDrawerClose}
      />
    </div>
  );
}
