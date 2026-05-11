// ---------------------------------------------------------------------------
// ButlerChroniclerTimelinesTab — bu-iuol4.25
//
// Timelines bespoke tab for the Chronicler butler detail page.
//
// Layout (4-col panel grid):
//   Row 1: 4 KPI cells (full width)
//     - Today events     — episode count for today
//     - Sources live     — count of active sources (last_run_at within 2h)
//     - Longest gap      — longest stretch with no events today
//     - Next assembly    — next chronicler_day_close schedule fire time
//
//   Row 2: Today timeline (span 3) + Sources (span 1)
//
// The old Category Breakdown and Day-close prose panels are deprecated.
// Rationale: those sections belong to the editorial landing (ChroniclesPage),
// not the per-butler drilldown. The editorial landing already surfaces the
// day-close prose and category aggregates via the briefing pathway.
//
// All data comes from existing hooks. No new HTTP routes are added.
// No duplication with chronicles-editorial-rewrite: this file is the
// per-butler detail tab (ButlerChroniclerTimelinesTab), not the editorial
// landing (ChroniclesPage). It does not touch /briefing, /attention, or
// /api/chronicler/kpi beyond its existing usage.
// ---------------------------------------------------------------------------

import { useMemo } from "react";
import { AlertTriangle } from "lucide-react";

import type {
  ChroniclesKpi,
  ChroniclerEpisode,
  ChroniclerSourceStateRow,
} from "@/api/types";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Time } from "@/components/ui/time";
import { useTimezone } from "@/components/ui/timezone-context";
import { Panel, KpiCell } from "@/components/butler-detail/atoms";
import { useChroniclesKpi } from "@/hooks/use-chronicles-kpi";
import {
  useChroniclesEpisodesInfinite,
  useChroniclesSourceState,
} from "@/hooks/use-chronicles";
import { useSchedules } from "@/hooks/use-schedules";
import { LANE_TAXONOMY } from "@/components/chronicles/lane-taxonomy";
import type { Category } from "@/components/chronicles/lane-taxonomy";
import { getBadgeState } from "@/components/chronicles/source-state-utils";
import {
  startOfDayInTz,
  endOfDayInTz,
  formatTimeInTz,
  formatInTimeZone,
} from "@/components/chronicles/tz-format";

// ---------------------------------------------------------------------------
// Date helpers
// ---------------------------------------------------------------------------

function todayInTz(tz: string): string {
  return formatInTimeZone(new Date(), tz, "yyyy-MM-dd");
}

function dayStart(dateStr: string, tz: string): string {
  const [y, m, d] = dateStr.split("-").map(Number);
  return startOfDayInTz(new Date(y, m - 1, d), tz).toISOString();
}

function dayEnd(dateStr: string, tz: string): string {
  const [y, m, d] = dateStr.split("-").map(Number);
  return endOfDayInTz(new Date(y, m - 1, d), tz).toISOString();
}

/** Format minutes as "Xh Ym", "Xh", or "Ym". */
function fmtMinutes(min: number): string {
  if (min <= 0) return "0m";
  const h = Math.floor(min / 60);
  const m = min % 60;
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

function fmtTime(isoStr: string, tz: string): string {
  return formatTimeInTz(isoStr, tz);
}

// ---------------------------------------------------------------------------
// Source status classification (live / stale / offline / planned)
//
// "live"    — active + last_run_at within 2 hours
// "stale"   — active + last_run_at more than 2 hours ago or null
// "offline" — inactive
// "planned" — planned/deferred (no data yet)
// ---------------------------------------------------------------------------

const TWO_HOURS_MS = 2 * 60 * 60 * 1000;

type SourceStatus = "live" | "stale" | "offline" | "planned";

function classifySourceStatus(row: ChroniclerSourceStateRow, nowMs: number): SourceStatus | null {
  const badgeState = getBadgeState(row);
  if (badgeState === null) return null; // not_time_bearing — hide
  if (badgeState === "planned" || badgeState === "deferred") return "planned";
  if (badgeState === "inactive") return "offline";
  // active
  if (!row.last_run_at) return "stale";
  const ageMs = nowMs - new Date(row.last_run_at).getTime();
  return ageMs <= TWO_HOURS_MS ? "live" : "stale";
}

// ---------------------------------------------------------------------------
// KPI Row — 4 cells
// ---------------------------------------------------------------------------

interface KpiRowProps {
  kpi: ChroniclesKpi | undefined;
  kpiLoading: boolean;
  episodeCount: number;
  episodesLoading: boolean;
  sourcesLive: number;
  sourcesLoading: boolean;
  nextAssemblyAt: string | null;
  schedulesLoading: boolean;
}

function KpiRow({
  kpi,
  kpiLoading,
  episodeCount,
  episodesLoading,
  sourcesLive,
  sourcesLoading,
  nextAssemblyAt,
  schedulesLoading,
}: KpiRowProps) {
  const longestGapMin = kpi?.longest_gap_minutes ?? null;
  const longestGapFmt = longestGapMin != null ? fmtMinutes(longestGapMin) : null;

  return (
    <Panel span={4}>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 py-1">
        {/* KPI 1: Today events */}
        {episodesLoading && episodeCount === 0 ? (
          <div className="space-y-1" data-testid="loading-line">
            <Skeleton className="h-2 w-20 rounded" />
            <Skeleton className="h-7 w-10 rounded" />
          </div>
        ) : (
          <div data-testid="kpi-item">
            <KpiCell label="Today events" value={episodeCount} />
          </div>
        )}

        {/* KPI 2: Sources live */}
        {sourcesLoading ? (
          <div className="space-y-1" data-testid="loading-line">
            <Skeleton className="h-2 w-20 rounded" />
            <Skeleton className="h-7 w-10 rounded" />
          </div>
        ) : (
          <div data-testid="kpi-item">
            <KpiCell label="Sources live" value={sourcesLive} />
          </div>
        )}

        {/* KPI 3: Longest gap */}
        {kpiLoading && !kpi ? (
          <div className="space-y-1" data-testid="loading-line">
            <Skeleton className="h-2 w-20 rounded" />
            <Skeleton className="h-7 w-10 rounded" />
          </div>
        ) : (
          <div data-testid="kpi-item">
            <KpiCell label="Longest gap" value={longestGapFmt ?? "—"} sub="today" />
          </div>
        )}

        {/* KPI 4: Next assembly */}
        {schedulesLoading ? (
          <div className="space-y-1" data-testid="loading-line">
            <Skeleton className="h-2 w-20 rounded" />
            <Skeleton className="h-7 w-10 rounded" />
          </div>
        ) : (
          <div data-testid="kpi-item">
            <KpiCell
              label="Next assembly"
              value={
                nextAssemblyAt ? (
                  <Time
                    value={nextAssemblyAt}
                    mode="relative-compact"
                    className="font-mono tnum text-[22px] font-medium leading-none text-foreground"
                    data-testid="kpi-next-assembly"
                  />
                ) : (
                  "—"
                )
              }
            />
          </div>
        )}
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Episode spine — scrollable vertical timeline
// ---------------------------------------------------------------------------

interface EpisodeSpineProps {
  episodes: ChroniclerEpisode[];
  isLoading: boolean;
  tz: string;
  hasMore: boolean;
  isFetchingMore: boolean;
  onLoadMore: () => void;
}

function EpisodeSpine({
  episodes,
  isLoading,
  tz,
  hasMore,
  isFetchingMore,
  onLoadMore,
}: EpisodeSpineProps) {
  if (isLoading && episodes.length === 0) {
    return (
      <div className="space-y-3" data-testid="episode-spine-loading">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="flex items-start gap-3" data-testid="loading-line">
            <Skeleton className="mt-1 h-2 w-2 rounded-full shrink-0" />
            <div className="flex-1 space-y-1">
              <Skeleton className="h-3 w-24 rounded" />
              <Skeleton className="h-4 w-48 rounded" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (episodes.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
        No episodes recorded for today.
      </p>
    );
  }

  return (
    <div>
      <ol
        className="relative space-y-3 border-l border-border pl-4"
        aria-label="Today's episode timeline"
        data-testid="episode-spine"
      >
        {episodes.map((ep) => {
          const taxonomy = LANE_TAXONOMY[ep.category as Category];
          const dotColour = taxonomy?.colour ?? "bg-slate-400";
          const label = taxonomy?.label ?? ep.category;
          const isSensitive =
            ep.canonical_privacy === "sensitive" || ep.canonical_privacy === "restricted";
          const title = isSensitive
            ? "···"
            : (ep.canonical_title ?? ep.title ?? ep.episode_type);
          const startTime = fmtTime(ep.canonical_start_at, tz);
          const endTime = ep.canonical_end_at ? fmtTime(ep.canonical_end_at, tz) : null;

          return (
            <li
              key={ep.id}
              className="relative flex items-start gap-3"
              data-testid="episode-spine-item"
            >
              <span
                className={`absolute -left-[1.125rem] mt-1.5 h-2.5 w-2.5 rounded-full border-2 border-background ${dotColour}`}
                aria-hidden
              />
              <div className="min-w-0">
                <p className="text-xs text-muted-foreground tnum">
                  {startTime}
                  {endTime && ` – ${endTime}`}
                  <span className="ml-2 inline-block">{label}</span>
                  {isSensitive && (
                    <span className="ml-1 text-xs text-muted-foreground/60">(private)</span>
                  )}
                </p>
                <p className="text-sm truncate">{title}</p>
                {!isSensitive && ep.source_name && (
                  <p className="text-xs text-muted-foreground/70 truncate">{ep.source_name}</p>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {hasMore && (
        <div className="mt-4 flex justify-center" data-testid="load-more-container">
          <Button
            variant="outline"
            size="sm"
            onClick={onLoadMore}
            disabled={isFetchingMore}
            data-testid="load-more-button"
          >
            {isFetchingMore ? "Loading…" : "Load more"}
          </Button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sources panel — source list with live/stale/offline status badges
// ---------------------------------------------------------------------------

const SOURCE_STATUS_VARIANT: Record<
  SourceStatus,
  "default" | "secondary" | "outline" | "destructive"
> = {
  live: "default",
  stale: "secondary",
  offline: "destructive",
  planned: "outline",
};

interface SourcesPanelProps {
  rows: ChroniclerSourceStateRow[];
  isLoading: boolean;
  nowMs: number;
}

function SourcesPanel({ rows, isLoading, nowMs }: SourcesPanelProps) {
  if (isLoading && rows.length === 0) {
    return (
      <div className="space-y-2" data-testid="source-health-loading">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="flex items-center justify-between" data-testid="loading-line">
            <Skeleton className="h-3 w-24 rounded" />
            <Skeleton className="h-5 w-14 rounded-full" />
          </div>
        ))}
      </div>
    );
  }

  const visible = rows
    .map((row) => ({ row, status: classifySourceStatus(row, nowMs) }))
    .filter((item): item is { row: ChroniclerSourceStateRow; status: SourceStatus } =>
      item.status !== null,
    );

  if (visible.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
        No sources configured.
      </p>
    );
  }

  return (
    <ul
      className="space-y-2"
      aria-label="Source health"
      data-testid="source-health-list"
    >
      {visible.map(({ row, status }) => {
        const taxonomy = LANE_TAXONOMY[row.source_name as Category];
        const label = taxonomy?.label ?? row.source_name;
        const variant = SOURCE_STATUS_VARIANT[status];

        const hasError = Boolean(row.last_error);

        return (
          <li
            key={row.source_name}
            className="flex items-start justify-between gap-2 text-sm"
            data-testid="source-health-row"
          >
            <div className="min-w-0">
              <p className="flex items-center gap-1 font-medium truncate">
                {label}
                {hasError && (
                  <AlertTriangle
                    className="inline-block h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400"
                    aria-label={`Source error: ${row.last_error}`}
                    title={row.last_error ?? undefined}
                    data-testid="source-error-icon"
                  />
                )}
              </p>
              {row.last_run_at && (
                <p className="text-xs text-muted-foreground tnum">
                  <Time value={row.last_run_at} mode="relative-compact" />
                </p>
              )}
            </div>
            <Badge
              variant={variant}
              className="shrink-0 font-mono text-xs"
              data-testid={`source-status-badge-${status}`}
            >
              {status}
            </Badge>
          </li>
        );
      })}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Main tab component
// ---------------------------------------------------------------------------

const PAGE_SIZE = 50;

export default function ButlerChroniclerTimelinesTab() {
  const tz = useTimezone();
  const today = todayInTz(tz);
  const todayStart = dayStart(today, tz);
  const todayEnd = dayEnd(today, tz);

  // KPI data (longest_gap_minutes)
  const { data: kpiData, isLoading: kpiLoading } = useChroniclesKpi({ date: today, tz });
  const kpi = kpiData?.data;

  // Today's episodes — infinite pagination
  const todayEpisodesParams = useMemo(
    () => ({
      overlaps_start: todayStart,
      overlaps_end: todayEnd,
      limit: PAGE_SIZE,
    }),
    [todayStart, todayEnd],
  );
  const {
    data: infiniteEpisodesData,
    isLoading: episodesLoading,
    isFetchingNextPage: isFetchingMore,
    hasNextPage,
    fetchNextPage,
  } = useChroniclesEpisodesInfinite(todayEpisodesParams);

  // Flatten all pages, deduplicate, sort chronologically
  const episodes = useMemo(() => {
    if (!infiniteEpisodesData) return [];
    const seen = new Set<string>();
    const flat: ChroniclerEpisode[] = [];
    for (const page of infiniteEpisodesData.pages) {
      for (const ep of page.data) {
        if (!seen.has(ep.id)) {
          seen.add(ep.id);
          flat.push(ep);
        }
      }
    }
    return flat.sort(
      (a, b) =>
        new Date(a.canonical_start_at).getTime() - new Date(b.canonical_start_at).getTime(),
    );
  }, [infiniteEpisodesData]);

  const hasMore = !!hasNextPage;
  function handleLoadMore() {
    void fetchNextPage();
  }

  // Source state
  const { data: sourceStateData, isLoading: sourceLoading } = useChroniclesSourceState();
  const sourceRows = useMemo(
    () => sourceStateData?.data ?? [],
    [sourceStateData],
  );

  // Capture a single timestamp per render so the KPI count and Sources list
  // both evaluate against the same "now". Prevents edge-case inconsistencies
  // when two separate classifySourceStatus calls straddle the 2h boundary.
  const nowMs = Date.now();

  // KPI: count of live sources (active + last_run_at within 2h)
  const sourcesLive = useMemo(
    () => sourceRows.filter((row) => classifySourceStatus(row, nowMs) === "live").length,
    // nowMs is intentionally excluded from deps — it is recaptured on every
    // render and the staleness risk (< 1 ms between useMemo evaluations) is
    // negligible. Including it would cause unnecessary recalculations.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sourceRows],
  );

  // Schedule: next day-close assembly time
  const { data: schedulesData, isLoading: schedulesLoading } = useSchedules("chronicler");
  const nextAssemblyAt = useMemo(() => {
    const schedules = schedulesData?.data ?? [];
    const dayClose = schedules.find((s) => s.name === "chronicler_day_close");
    return dayClose?.next_run_at ?? null;
  }, [schedulesData]);

  // Total episode count from backend metadata — more accurate than episodes.length
  // which only reflects the currently-loaded pages.
  const episodeCount = infiniteEpisodesData?.pages[0]?.meta.total ?? 0;

  return (
    <div
      className="grid grid-cols-1 lg:grid-cols-4 border-t border-l border-border/60"
      data-testid="chronicler-timelines-tab"
    >
      {/* Row 1: KPI strip (full width, span 4) */}
      <div data-testid="kpi-strip" className="col-span-1 lg:col-span-4">
        <KpiRow
          kpi={kpi}
          kpiLoading={kpiLoading}
          episodeCount={episodeCount}
          episodesLoading={episodesLoading}
          sourcesLive={sourcesLive}
          sourcesLoading={sourceLoading}
          nextAssemblyAt={nextAssemblyAt}
          schedulesLoading={schedulesLoading}
        />
      </div>

      {/* Row 2: Today timeline (span 3) */}
      <div data-testid="today-timeline-card" className="col-span-1 lg:col-span-3">
        <Panel title="Today timeline" span={3} scroll height="480px">
          <EpisodeSpine
            episodes={episodes}
            isLoading={episodesLoading}
            tz={tz}
            hasMore={hasMore}
            isFetchingMore={isFetchingMore}
            onLoadMore={handleLoadMore}
          />
        </Panel>
      </div>

      {/* Row 2: Sources (span 1) */}
      <div data-testid="sources-card" className="col-span-1">
        <Panel title="Sources" span={1}>
          <SourcesPanel rows={sourceRows} isLoading={sourceLoading} nowMs={nowMs} />
        </Panel>
      </div>
    </div>
  );
}
