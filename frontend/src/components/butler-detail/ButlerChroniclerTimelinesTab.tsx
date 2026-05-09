// ---------------------------------------------------------------------------
// ButlerChroniclerTimelinesTab — bu-aeg7w
//
// Timelines bespoke tab for the Chronicler butler detail page.
//
// Five sections (4-col grid):
//   1. KPI strip (full-width)          — GET /api/chronicler/kpi
//   2. Today · Episode timeline (3col) — GET /api/chronicler/episodes
//   3. Sources · today (1col)          — GET /api/chronicler/source-state
//   4. Category breakdown · 7d (2col)  — GET /api/chronicler/aggregate/by-category
//   5. Day-close prose (2col)          — GET /api/chronicler/aggregate/day-close
//
// All data comes from existing hooks. No new HTTP routes are added.
// ---------------------------------------------------------------------------

import { useMemo } from "react";

import type {
  ChroniclesKpi,
  ChroniclesLaneHours,
  ChroniclerCategoryBucket,
  ChroniclerDayCloseResponse,
  ChroniclerEpisode,
  ChroniclerSourceStateRow,
} from "@/api/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { useTimezone } from "@/components/ui/timezone-context";
import { useChroniclesKpi } from "@/hooks/use-chronicles-kpi";
import {
  useChroniclesEpisodes,
  useChroniclesByCategory,
  useChroniclesSourceState,
  useChroniclesDayClose,
} from "@/hooks/use-chronicles";
import { LANE_TAXONOMY } from "@/components/chronicles/lane-taxonomy";
import type { Category } from "@/components/chronicles/lane-taxonomy";
import { getBadgeState } from "@/components/chronicles/source-state-utils";
import {
  startOfDayInTz,
  endOfDayInTz,
  formatTimeInTz,
  formatDateTimeInTz,
  formatInTimeZone,
} from "@/components/chronicles/tz-format";

// ---------------------------------------------------------------------------
// Date helpers — today's window anchored to owner timezone
// ---------------------------------------------------------------------------

/** Returns today's YYYY-MM-DD string in the owner's timezone. */
function todayInTz(tz: string): string {
  return formatInTimeZone(new Date(), tz, "yyyy-MM-dd");
}

/** Returns the ISO start-of-day for a YYYY-MM-DD date in the owner's timezone. */
function dayStart(dateStr: string, tz: string): string {
  const [y, m, d] = dateStr.split("-").map(Number);
  return startOfDayInTz(new Date(y, m - 1, d), tz).toISOString();
}

/** Returns the ISO end-of-day (exclusive: next day start) for a YYYY-MM-DD date. */
function dayEnd(dateStr: string, tz: string): string {
  const [y, m, d] = dateStr.split("-").map(Number);
  return endOfDayInTz(new Date(y, m - 1, d), tz).toISOString();
}

/** Returns the ISO start-of-day 7 days ago in the owner's timezone. */
function sevenDaysAgoStart(todayStr: string, tz: string): string {
  const [y, m, d] = todayStr.split("-").map(Number);
  // Shift back 6 days to get a 7-day window: day-6 through today inclusive
  const sixDaysBack = new Date(y, m - 1, d - 6);
  return startOfDayInTz(sixDaysBack, tz).toISOString();
}

/** Format seconds as "Xh Ym" or "Xm". */
function fmtSeconds(s: number): string {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

/** Format minutes as "Xh Ym" or "Xm". */
function fmtMinutes(min: number): string {
  return fmtSeconds(min * 60);
}

/** Format a datetime string as HH:MM in the owner's timezone. */
function fmtTime(isoStr: string, tz: string): string {
  return formatTimeInTz(isoStr, tz);
}

// ---------------------------------------------------------------------------
// Section 1: KPI Strip
// ---------------------------------------------------------------------------

interface KpiStripProps {
  kpi: ChroniclesKpi | undefined;
  isLoading: boolean;
}

function KpiItem({
  label,
  value,
  subLabel,
}: {
  label: string;
  value: string;
  subLabel?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5" data-testid="kpi-item">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-2xl font-semibold tabular-nums" data-testid="kpi-value">
        {value}
      </span>
      {subLabel && (
        <span className="text-xs text-muted-foreground truncate">{subLabel}</span>
      )}
    </div>
  );
}

function KpiStrip({ kpi, isLoading }: KpiStripProps) {
  if (isLoading && !kpi) {
    return (
      <Card data-testid="kpi-strip">
        <CardHeader>
          <CardTitle className="text-sm font-medium">Today at a glance</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-6">
            {Array.from({ length: 5 }, (_, i) => (
              <div key={i} className="space-y-1" data-testid="loading-line">
                <Skeleton className="h-3 w-20 rounded" />
                <Skeleton className="h-7 w-12 rounded" />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  if (!kpi) {
    return (
      <Card data-testid="kpi-strip">
        <CardHeader>
          <CardTitle className="text-sm font-medium">Today at a glance</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
            No KPI data available.
          </p>
        </CardContent>
      </Card>
    );
  }

  // Top 3 lanes by hours
  const topLanes: ChroniclesLaneHours[] = kpi.hours_by_top_lanes.slice(0, 3);

  return (
    <Card data-testid="kpi-strip">
      <CardHeader>
        <CardTitle className="text-sm font-medium">Today at a glance</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-6">
          {topLanes.map((lane) => {
            const taxonomy = LANE_TAXONOMY[lane.lane as Category];
            const label = taxonomy?.label ?? lane.lane;
            return (
              <KpiItem
                key={lane.lane}
                label={label}
                value={`${lane.hours.toFixed(1)}h`}
              />
            );
          })}
          <KpiItem
            label="Sleep"
            value={fmtMinutes(kpi.sleep_minutes)}
          />
          <KpiItem
            label="Sleep streak"
            value={`${kpi.streaks.sleep}d`}
          />
          <KpiItem
            label="Longest episode"
            value={fmtMinutes(kpi.longest_episode_minutes)}
            subLabel={kpi.longest_episode_title ?? undefined}
          />
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section 2: Today's Episode Timeline
// ---------------------------------------------------------------------------

interface EpisodeSpineProps {
  episodes: ChroniclerEpisode[];
  isLoading: boolean;
  tz: string;
}

function EpisodeSpine({ episodes, isLoading, tz }: EpisodeSpineProps) {
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
    <ol
      className="relative space-y-3 border-l border-border pl-4"
      aria-label="Today's episode timeline"
      data-testid="episode-spine"
    >
      {episodes.map((ep) => {
        const taxonomy = LANE_TAXONOMY[ep.category as Category];
        const dotColour = taxonomy?.colour ?? "bg-slate-400";
        const label = taxonomy?.label ?? ep.category;
        const isSensitive = ep.canonical_privacy === "sensitive" || ep.canonical_privacy === "restricted";
        const title = isSensitive ? "···" : (ep.canonical_title ?? ep.title ?? ep.episode_type);
        const startTime = fmtTime(ep.canonical_start_at, tz);
        const endTime = ep.canonical_end_at ? fmtTime(ep.canonical_end_at, tz) : null;

        return (
          <li
            key={ep.id}
            className="relative flex items-start gap-3"
            data-testid="episode-spine-item"
          >
            {/* category dot */}
            <span
              className={`absolute -left-[1.125rem] mt-1.5 h-2.5 w-2.5 rounded-full border-2 border-background ${dotColour}`}
              aria-hidden
            />
            <div className="min-w-0">
              <p className="text-xs text-muted-foreground tabular-nums">
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
  );
}

// ---------------------------------------------------------------------------
// Section 3: Source Health Widget
// ---------------------------------------------------------------------------

interface SourceHealthWidgetProps {
  rows: ChroniclerSourceStateRow[];
  isLoading: boolean;
  tz: string;
}

function SourceHealthWidget({ rows, isLoading, tz }: SourceHealthWidgetProps) {
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

  // Filter out not_time_bearing sources (same logic as SourceStateBadgeStrip)
  const visible = rows.filter((row) => getBadgeState(row) !== null);

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
      {visible.map((row) => {
        const state = getBadgeState(row);
        const taxonomy = LANE_TAXONOMY[row.source_name as Category];
        const label = taxonomy?.label ?? row.source_name;

        let badgeVariant: "default" | "secondary" | "outline" | "destructive" = "secondary";
        if (state === "active" && !row.last_error) badgeVariant = "default";
        if (state === "inactive") badgeVariant = "destructive";
        if (state === "planned") badgeVariant = "outline";

        const lastRun = row.last_run_at
          ? formatDateTimeInTz(row.last_run_at, tz)
          : null;

        return (
          <li
            key={row.source_name}
            className="flex items-start justify-between gap-2 text-sm"
            data-testid="source-health-row"
          >
            <div className="min-w-0">
              <p className="font-medium truncate">{label}</p>
              {lastRun && (
                <p className="text-xs text-muted-foreground">{lastRun}</p>
              )}
              {row.last_error && (
                <p className="text-xs text-destructive truncate" title={row.last_error}>
                  {row.last_error}
                </p>
              )}
            </div>
            <Badge
              variant={badgeVariant}
              className="shrink-0 capitalize"
            >
              {state ?? "unknown"}
            </Badge>
          </li>
        );
      })}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Section 4: 7-day Category Breakdown
// ---------------------------------------------------------------------------

interface CategoryBreakdownProps {
  buckets: ChroniclerCategoryBucket[];
  isLoading: boolean;
}

function CategoryBreakdown({ buckets, isLoading }: CategoryBreakdownProps) {
  // Calculate total seconds for percentage bars
  const totalSeconds = useMemo(
    () => buckets.reduce((sum, b) => sum + b.total_seconds, 0),
    [buckets],
  );

  if (isLoading && buckets.length === 0) {
    return (
      <div className="space-y-3" data-testid="category-breakdown-loading">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="space-y-1" data-testid="loading-line">
            <div className="flex justify-between">
              <Skeleton className="h-3 w-20 rounded" />
              <Skeleton className="h-3 w-10 rounded" />
            </div>
            <Skeleton className="h-2 w-full rounded-full" />
          </div>
        ))}
      </div>
    );
  }

  if (buckets.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
        No activity recorded in the last 7 days.
      </p>
    );
  }

  return (
    <ul
      className="space-y-3"
      aria-label="7-day category breakdown"
      data-testid="category-breakdown-list"
    >
      {buckets.map((bucket) => {
        const taxonomy = LANE_TAXONOMY[bucket.category as Category];
        const label = taxonomy?.label ?? bucket.category;
        const hexColour = taxonomy?.hex ?? "#64748b";
        const pct = totalSeconds > 0 ? (bucket.total_seconds / totalSeconds) * 100 : 0;

        return (
          <li key={bucket.category} data-testid="category-breakdown-item">
            <div className="flex items-center justify-between text-sm mb-1">
              <span>{label}</span>
              <span className="tabular-nums text-muted-foreground">
                {fmtSeconds(bucket.total_seconds)}
              </span>
            </div>
            <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${pct.toFixed(1)}%`,
                  backgroundColor: hexColour,
                }}
                aria-label={`${label}: ${pct.toFixed(0)}%`}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Section 5: Day-Close Prose
// ---------------------------------------------------------------------------

interface DayClosePanelProps {
  dayClose: ChroniclerDayCloseResponse | undefined;
  isLoading: boolean;
  isError: boolean;
  tz: string;
}

function DayClosePanel({ dayClose, isLoading, isError, tz }: DayClosePanelProps) {
  if (isLoading && !dayClose) {
    return (
      <div className="space-y-2" data-testid="day-close-loading">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-4 w-full rounded" data-testid="loading-line" />
        ))}
      </div>
    );
  }

  if (isError || !dayClose) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
        No day-close summary available.
      </p>
    );
  }

  if (dayClose.stale) {
    return (
      <div data-testid="day-close-stale">
        <p className="text-sm text-muted-foreground italic">
          Summary is stale — data has changed since the last editorial run.
        </p>
        <p className="text-xs text-muted-foreground mt-1">
          Built: {formatDateTimeInTz(dayClose.cache_built_at, tz)}
        </p>
      </div>
    );
  }

  return (
    <div data-testid="day-close-prose">
      <p className="text-sm leading-relaxed">{dayClose.prose}</p>
      <p className="text-xs text-muted-foreground mt-2">
        Built: {formatDateTimeInTz(dayClose.cache_built_at, tz)}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main tab component
// ---------------------------------------------------------------------------

export default function ButlerChroniclerTimelinesTab() {
  const tz = useTimezone();
  const today = todayInTz(tz);
  const todayStart = dayStart(today, tz);
  const todayEnd = dayEnd(today, tz);
  const sevenDaysStart = sevenDaysAgoStart(today, tz);

  // --- Section 1: KPI strip
  const { data: kpiData, isLoading: kpiLoading } = useChroniclesKpi({ date: today, tz });
  const kpi = kpiData?.data;

  // --- Section 2: Today's episodes (sorted by start time, limit 50 per fetch)
  const todayEpisodesParams = useMemo(
    () => ({ overlaps_start: todayStart, overlaps_end: todayEnd, limit: 50 }),
    [todayStart, todayEnd],
  );
  const {
    data: episodesData,
    isLoading: episodesLoading,
  } = useChroniclesEpisodes(todayEpisodesParams);
  const episodes = useMemo(
    () =>
      [...(episodesData?.data ?? [])].sort(
        (a, b) =>
          new Date(a.canonical_start_at).getTime() -
          new Date(b.canonical_start_at).getTime(),
      ),
    [episodesData],
  );

  // --- Section 3: Source state
  const {
    data: sourceStateData,
    isLoading: sourceLoading,
  } = useChroniclesSourceState();
  const sourceRows = sourceStateData?.data ?? [];

  // --- Section 4: 7-day category breakdown (by-category only; by-day is not needed here)
  const sevenDayCategoryParams = useMemo(
    () => ({ start_at: sevenDaysStart, end_at: todayEnd }),
    [sevenDaysStart, todayEnd],
  );
  const byCategory = useChroniclesByCategory(sevenDayCategoryParams);
  const categoryBuckets = byCategory.data?.data.buckets ?? [];

  // --- Section 5: Day-close prose (today's window)
  const dayCloseParams = useMemo(
    () => ({ window_start: todayStart, window_end: todayEnd }),
    [todayStart, todayEnd],
  );
  const {
    data: dayClose,
    isLoading: dayCloseLoading,
    isError: dayCloseError,
  } = useChroniclesDayClose(dayCloseParams);

  return (
    <div className="space-y-6" data-testid="chronicler-timelines-tab">
      {/* Section 1: KPI strip — full width */}
      <KpiStrip kpi={kpi} isLoading={kpiLoading} />

      {/* Sections 2–3: Today timeline (3col) + Sources (1col) */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <Card className="lg:col-span-3" data-testid="today-timeline-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Today · Timeline</CardTitle>
          </CardHeader>
          <CardContent className="max-h-[480px] overflow-y-auto">
            <EpisodeSpine
              episodes={episodes}
              isLoading={episodesLoading}
              tz={tz}
            />
          </CardContent>
        </Card>

        <Card className="lg:col-span-1" data-testid="sources-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Sources · today</CardTitle>
          </CardHeader>
          <CardContent>
            <SourceHealthWidget
              rows={sourceRows}
              isLoading={sourceLoading}
              tz={tz}
            />
          </CardContent>
        </Card>
      </div>

      {/* Sections 4–5: Category breakdown (2col) + Day-close prose (2col) */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card data-testid="category-breakdown-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Category breakdown · 7d</CardTitle>
          </CardHeader>
          <CardContent>
            <CategoryBreakdown
              buckets={categoryBuckets}
              isLoading={byCategory.isLoading}
            />
          </CardContent>
        </Card>

        <Card data-testid="day-close-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Day-close · summary</CardTitle>
          </CardHeader>
          <CardContent>
            <DayClosePanel
              dayClose={dayClose}
              isLoading={dayCloseLoading}
              isError={dayCloseError}
              tz={tz}
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
