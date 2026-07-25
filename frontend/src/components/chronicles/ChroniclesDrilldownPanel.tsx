// ---------------------------------------------------------------------------
// ChroniclesDrilldownPanel
//
// The editorial /chronicles landing keeps the page quiet: Voice briefing, KPI
// strip, attention list, recent-days index. The lived-day detail lives here,
// below the editorial fold.
//
// IEA reframe (tasks.md §10): the misleading source-shaped pie is gone. The
// "where the time went" surface is now the plan-vs-reality Day Ribbon (with a
// ghost Intent track) plus Balance rings vs usual. Clicking a lived-activity
// block opens the EpisodeDrawer, which reveals the activity's evidence chain
// ("why is this counted?"). Alongside: who-you-were-with, low-confidence
// correction prompts wired to the corrections overlay, the where-you-went map
// trail (existing privacy contract), and a week/month zoom-out trends lens.
//
// Every new panel follows the degraded-mode convention: a failed source
// renders a SourceDegradedNote, never a truthful-empty result.
//
// The panel is driven by the page's selected day (a settled past day), so it is
// static: no time-window picker, no auto-refresh. The Gantt and Map remain
// self-lazy so they stream in without blocking the briefing above.
// ---------------------------------------------------------------------------

import { useCallback, useMemo, useState } from "react";

import {
  useChroniclesBalance,
  useChroniclesCorrectionPrompts,
  useChroniclesEpisodes,
  useChroniclesPointEvents,
  useChroniclesRollups,
  useChroniclesTrends,
  useChroniclesWhoYouWereWith,
} from "@/hooks/use-chronicles";
import { dayWindowInTz } from "@/lib/tz-format";
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
import { StreakCallouts } from "@/components/chronicles/StreakCallouts";
import { ManualRefreshButton } from "@/components/chronicles/ManualRefreshButton";
import { WorkScheduleSettings } from "@/components/chronicles/WorkScheduleSettings";
import { DayRibbon } from "@/components/chronicles/DayRibbon";
import { DayNarrative } from "@/components/chronicles/DayNarrative";
import { BalanceRings } from "@/components/chronicles/BalanceRings";
import { WhoYouWereWithPanel } from "@/components/chronicles/WhoYouWereWithPanel";
import { CorrectionPromptsPanel } from "@/components/chronicles/CorrectionPromptsPanel";
import { TrendsLens, type TrendsWindow } from "@/components/chronicles/TrendsLens";

import type { ChroniclerEventsParams, ChroniclerEpisodesParams } from "@/api/types";

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
  const [trendsWindow, setTrendsWindow] = useState<TrendsWindow>("week");
  const handleEpisodeClick = useCallback((episodeId: string) => {
    setSelectedEpisodeId(episodeId);
  }, []);
  const handleDrawerClose = useCallback(() => {
    setSelectedEpisodeId(null);
  }, []);

  const windowFrom = from.toISOString();
  const windowTo = to.toISOString();

  const episodesParams: ChroniclerEpisodesParams = useMemo(
    () => ({ overlaps_start: windowFrom, overlaps_end: windowTo, limit: 500 }),
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

  // Episodes power the Day Ribbon (activity blocks + ghost intent track). The
  // same query key backs the Gantt below, so react-query dedupes the request.
  const episodesQuery = useChroniclesEpisodes(episodesParams, { refetchInterval });
  const episodes = useMemo(() => episodesQuery.data?.data ?? [], [episodesQuery.data]);

  // Where-you-went: the existing map trail respects the map privacy contract —
  // sensitive point events are excluded before any coordinate is plotted.
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

  // Balance rings vs usual (GET /balance) — keyed on the local calendar day.
  const balance = useChroniclesBalance({ date }, { refetchInterval });

  // Who-you-were-with (GET /who-you-were-with) — the day window + owner tz.
  const who = useChroniclesWhoYouWereWith(
    { start_at: windowFrom, end_at: windowTo, tz },
    { refetchInterval },
  );

  // Low-confidence correction prompts (GET /correction-prompts) → drawer overlay.
  const prompts = useChroniclesCorrectionPrompts(
    { start_at: windowFrom, end_at: windowTo, tz },
    { refetchInterval },
  );

  // Week/month zoom-out trends lens (GET /trends), ending on the selected day.
  const trends = useChroniclesTrends(
    { window: trendsWindow, end_date: date },
    { refetchInterval },
  );

  // Optional once-daily LLM narration for the selected day (GET /rollups):
  // a one-line prose summary + per-flag labels. Absent narration is normal
  // (DayNarrative renders nothing); a genuine fetch failure degrades honestly.
  const rollups = useChroniclesRollups({ date }, { refetchInterval });

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-end">
        <ManualRefreshButton />
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
        <div className="space-y-5">
          <DayNarrative
            data={rollups.data?.data}
            isLoading={rollups.isLoading}
            isError={rollups.isError}
            onRetry={() => void rollups.refetch()}
          />
          <StreakCallouts episodeParams={episodesParams} refetchInterval={refetchInterval} />
          <DayRibbon
            episodes={episodes}
            windowStart={from}
            windowEnd={to}
            onEpisodeClick={handleEpisodeClick}
            isError={episodesQuery.isError}
            onRetry={() => void episodesQuery.refetch()}
          />
          <BalanceRings
            data={balance.data?.data}
            isLoading={balance.isLoading}
            isError={balance.isError}
            onRetry={() => void balance.refetch()}
          />
        </div>
      </Section>

      <Section eyebrow="Who you were with">
        <WhoYouWereWithPanel
          data={who.data?.data}
          isLoading={who.isLoading}
          isError={who.isError}
          onRetry={() => void who.refetch()}
        />
      </Section>

      <Section eyebrow="Needs your eye">
        <CorrectionPromptsPanel
          data={prompts.data?.data}
          isLoading={prompts.isLoading}
          isError={prompts.isError}
          onRetry={() => void prompts.refetch()}
          onSelectEpisode={handleEpisodeClick}
        />
      </Section>

      <Section eyebrow="Zoom out">
        <TrendsLens
          data={trends.data?.data}
          isLoading={trends.isLoading}
          isError={trends.isError}
          onRetry={() => void trends.refetch()}
          window={trendsWindow}
          onWindowChange={setTrendsWindow}
        />
      </Section>

      <Section eyebrow="Work schedule">
        <WorkScheduleSettings />
      </Section>

      <EpisodeDrawer
        episodeId={selectedEpisodeId}
        open={selectedEpisodeId !== null}
        onClose={handleDrawerClose}
      />
    </div>
  );
}
