/**
 * ButlerEducationReviewsTab
 *
 * Reviews bespoke tab for the education butler detail page.
 *
 * Layout (4-col panel grid, 3 rows):
 *  Row 1: 4 KPI cells — total cards, mastered count, overdue count, avg mastery score
 *  Row 2: mind maps progress (span 2) + pending reviews timeline (span 2, scrollable)
 *  Row 3: frontier nodes (span 2) + retention 7d trend chart (span 2)
 *
 * Tab label: "Reviews" (manifesto rule — NOT "Decks")
 *
 * New hooks:
 *  useMindMapAnalyticsTrend(mindMapId, days) — wraps GET /analytics/trend?days=7
 *
 * bead: bu-iuol4.26
 */

import { useMemo } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Section, SectionContent, SectionHeader, SectionTitle } from "@/components/ui/Section";
import { Time } from "@/components/ui/time";
import { useTimezone } from "@/components/ui/timezone-context";
import { classifyReviewBucket, type ReviewBucket } from "@/lib/review-buckets";
import {
  useMindMaps,
  useAllPendingReviews,
  useAllMasterySummaries,
  useAllFrontierNodes,
  useMindMapAnalyticsTrend,
} from "@/hooks/use-education";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { toneClass } from "@/components/butler-detail/atoms-utils";
import { chartColor } from "@/lib/chart-colors";
import { useTickingNow } from "@/hooks/use-ticking-now";
import { DAY_MS, buildTimeSeries } from "@/lib/time-series";
import { TimeSeriesChart, TooltipDate } from "@/components/ui/TimeSeriesChart";
import type { PendingReviewNode, MindMapNode, MasterySummary, AnalyticsTrendEntry } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ReviewEntry extends PendingReviewNode {
  mind_map_id: string;
  mind_map_title: string;
}

interface FrontierEntry extends MindMapNode {
  mind_map_id: string;
  mind_map_title: string;
}

/** Aggregated mastery counts across all active mind maps. */
interface AggregatedMastery {
  total_nodes: number;
  mastered_count: number;
  learning_count: number;
  reviewing_count: number;
  unseen_count: number;
  avg_mastery_score: number;
}

interface AggregatedData {
  pendingEntries: ReviewEntry[];
  mastery: AggregatedMastery | null;
  frontierEntries: FrontierEntry[];
  mindMaps: Array<{ id: string; title: string; status: string }>;
  perMapMastery: Array<MasterySummary | null>;
  isLoading: boolean;
  failedSources: Array<{ title: string; source: string }>;
  mapsDegraded: boolean;
  pendingDegraded: boolean;
  frontierDegraded: boolean;
}

// ---------------------------------------------------------------------------
// Top-level aggregation hook
// ---------------------------------------------------------------------------

/** Aggregates all data for the Reviews tab in a single hook call per data type. */
function useReviewsTabData(): AggregatedData {
  const { data: mapsResp, isLoading: mapsLoading, isError: mapsError } = useMindMaps({ status: "active" });
  // Stable reference: memoize the data array so inner useMemo deps don't fire on
  // every render when data hasn't changed (TanStack Query returns new object
  // references on each render even when data is the same).
  const maps = useMemo(() => mapsResp?.data ?? [], [mapsResp?.data]);
  const mapIds = maps.map((m) => m.id);

  const pendingResults = useAllPendingReviews(mapIds);
  const summaryResults = useAllMasterySummaries(mapIds);
  const frontierResults = useAllFrontierNodes(mapIds);

  const isLoading =
    mapsLoading ||
    pendingResults.some((r) => r.isLoading) ||
    summaryResults.some((r) => r.isLoading) ||
    frontierResults.some((r) => r.isLoading);

  return useMemo(() => {
    const pendingEntries: ReviewEntry[] = [];
    for (let i = 0; i < maps.length; i++) {
      const nodes = pendingResults[i]?.data ?? [];
      for (const node of nodes) {
        pendingEntries.push({
          ...node,
          mind_map_id: maps[i].id,
          mind_map_title: maps[i].title,
        });
      }
    }
    pendingEntries.sort(
      (a, b) =>
        new Date(a.next_review_at).getTime() - new Date(b.next_review_at).getTime(),
    );

    const summaries = summaryResults
      .map((r) => r.data)
      .filter((s): s is MasterySummary => s != null);

    const mastery =
      summaries.length === 0
        ? null
        : summaries.reduce(
            (acc, s) => ({
              total_nodes: acc.total_nodes + s.total_nodes,
              mastered_count: acc.mastered_count + s.mastered_count,
              learning_count: acc.learning_count + s.learning_count,
              reviewing_count: acc.reviewing_count + s.reviewing_count,
              unseen_count: acc.unseen_count + s.unseen_count,
              // Weighted average for avg_mastery_score; fall back to simple average
              // across maps since per-map total_nodes is available.
              avg_mastery_score:
                acc.total_nodes + s.total_nodes > 0
                  ? (acc.avg_mastery_score * acc.total_nodes + s.avg_mastery_score * s.total_nodes) /
                    (acc.total_nodes + s.total_nodes)
                  : 0,
            }),
            {
              total_nodes: 0,
              mastered_count: 0,
              learning_count: 0,
              reviewing_count: 0,
              unseen_count: 0,
              avg_mastery_score: 0,
            },
          );

    const frontierEntries: FrontierEntry[] = [];
    for (let i = 0; i < maps.length; i++) {
      const nodes = frontierResults[i]?.data ?? [];
      for (const node of nodes) {
        frontierEntries.push({
          ...node,
          mind_map_id: maps[i].id,
          mind_map_title: maps[i].title,
        });
      }
    }
    frontierEntries.sort((a, b) => a.mastery_score - b.mastery_score);

    const perMapMastery: Array<MasterySummary | null> = summaryResults.map(
      (r) => r.data ?? null,
    );

    const mapsUnavailable = mapsError || (!mapsLoading && mapsResp == null);
    const failedSources = mapsUnavailable ? [{ title: "Curriculum list", source: "curriculum list" }] : [];
    for (const [results, source] of [[pendingResults, "review schedule"], [summaryResults, "mastery"], [frontierResults, "frontier"]] as const) {
      results.forEach((result, index) => {
        if (result.isError || (!result.isLoading && result.data == null)) {
          failedSources.push({ title: maps[index].title, source });
        }
      });
    }
    return {
      failedSources,
      mapsDegraded: mapsUnavailable,
      pendingDegraded: mapsUnavailable || pendingResults.length !== maps.length || pendingResults.some((result) => result.isError || result.data == null),
      frontierDegraded: mapsUnavailable || frontierResults.length !== maps.length || frontierResults.some((result) => result.isError || result.data == null),
      pendingEntries,
      mastery,
      frontierEntries,
      mindMaps: maps.map((m) => ({ id: m.id, title: m.title, status: m.status })),
      perMapMastery,
      isLoading,
    };
  }, [maps, pendingResults, summaryResults, frontierResults, isLoading, mapsError, mapsLoading, mapsResp]);
}

// ---------------------------------------------------------------------------
// Shared UI primitives
// ---------------------------------------------------------------------------

/** Empty-state text: serif italic per Dispatch typography guidelines. */
function EmptyStateLine({ children }: { children: ReactNode }) {
  return (
    <p
      className="text-sm text-muted-foreground italic font-[family-name:var(--font-serif)]"
      data-testid="empty-state-line"
    >
      {children}
    </p>
  );
}

/** Non-spinner loading placeholder. */
function LoadingLine() {
  return (
    <p className="text-sm text-muted-foreground" data-testid="loading-line">
      Loading…
    </p>
  );
}

// ---------------------------------------------------------------------------
// Row 1: KPI quartet
// ---------------------------------------------------------------------------

interface KpiItem {
  label: string;
  value: string | number;
  tone?: "normal" | "amber" | "red";
}

function KpiQuartet({
  mastery,
  overdueCount,
  isLoading,
}: {
  mastery: AggregatedData["mastery"];
  overdueCount: number;
  isLoading: boolean;
}) {
  const kpis: KpiItem[] = [
    {
      label: "Total cards",
      value: isLoading ? "…" : (mastery?.total_nodes ?? "—"),
    },
    {
      label: "Mastered",
      value: isLoading ? "…" : (mastery?.mastered_count ?? "—"),
      tone: "normal",
    },
    {
      label: "Overdue",
      value: isLoading ? "…" : overdueCount,
      tone: overdueCount > 0 ? "red" : "normal",
    },
    {
      label: "Avg mastery",
      value: isLoading
        ? "…"
        : mastery != null
          ? `${Math.round(mastery.avg_mastery_score * 100)}%`
          : "—",
      tone: "normal",
    },
  ];

  return (
    <div
      className="grid grid-cols-2 gap-3 sm:grid-cols-4"
      data-testid="mastery-kpi-strip"
    >
      {kpis.map((kpi) => (
        <Section key={kpi.label}>
          <SectionContent className="pt-4">
            <p className="text-xs text-muted-foreground">{kpi.label}</p>
            <p
              className={`text-2xl font-bold tnum font-mono ${
                kpi.tone === "red"
                  ? toneClass("red")
                  : kpi.tone === "amber"
                    ? toneClass("amber")
                    : ""
              }`}
              data-testid="kpi-value"
            >
              {kpi.value}
            </p>
          </SectionContent>
        </Section>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row 2a: Mind maps progress panel
// ---------------------------------------------------------------------------

interface MindMapProgressItem {
  id: string;
  title: string;
  mastery: MasterySummary | null;
}

function MindMapsProgressPanel({
  items,
  isLoading,
  degraded,
}: {
  items: MindMapProgressItem[];
  isLoading: boolean;
  degraded: boolean;
}) {
  return (
    <Section data-testid="mind-maps-progress-panel">
      <SectionHeader className="pb-2">
        <SectionTitle>Mind maps</SectionTitle>
      </SectionHeader>
      <SectionContent>
        {degraded && <SourceDegradedNote label="Curriculum list" />}
        {isLoading ? (
          <LoadingLine />
        ) : items.length === 0 ? (
          !degraded && <EmptyStateLine>No active mind maps (start learning to see progress here).</EmptyStateLine>
        ) : (
          <ul className="divide-y" data-testid="mind-maps-list">
            {items.map((item) => {
              const mastered = item.mastery?.mastered_count ?? 0;
              const total = item.mastery?.total_nodes ?? 0;
              const pct = total > 0 ? Math.round((mastered / total) * 100) : 0;

              return (
                <li
                  key={item.id}
                  className="flex items-center justify-between py-2 gap-3"
                  data-testid="mind-map-progress-row"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium truncate">{item.title}</p>
                    {item.mastery != null ? (
                      <div className="mt-1">
                        <div
                          className="h-1.5 w-full rounded-full bg-muted overflow-hidden"
                          aria-label={`${pct}% mastered`}
                        >
                          <div
                            className="h-full bg-primary rounded-full transition-all"
                            style={{ width: `${pct}%` }}
                            data-testid="mastery-progress-bar"
                          />
                        </div>
                      </div>
                    ) : null}
                  </div>
                  <div className="shrink-0 text-right">
                    {item.mastery != null ? (
                      <>
                        <span
                          className="text-sm font-mono tnum font-medium"
                          data-testid="mastery-pct"
                        >
                          {pct}%
                        </span>
                        <p className="text-xs text-muted-foreground tnum">
                          {mastered}/{total}
                        </p>
                      </>
                    ) : (
                      <span className="text-sm text-muted-foreground">—</span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </SectionContent>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Row 2b: Pending reviews timeline (scrollable)
// ---------------------------------------------------------------------------

interface TimelineGroup {
  label: string;
  testId: string;
  borderClass: string;
  entries: ReviewEntry[];
}

// Bucket order for this surface. `weekEnd` keeps its "strictly 7 days from now"
// semantics via the `"now"` week anchor; the Today/This-week boundaries now come
// from owner-tz midnight via classifyReviewBucket — see lib/review-buckets.ts.
const BUCKET_INDEX: Record<ReviewBucket, number> = {
  overdue: 0,
  today: 1,
  "this-week": 2,
  later: 3,
};

function groupByTimePeriod(entries: ReviewEntry[], now: Date, tz: string): TimelineGroup[] {
  const groups: TimelineGroup[] = [
    { label: "Overdue", testId: "reviews-overdue-section", borderClass: "border-l-4 border-l-red-500", entries: [] },
    { label: "Today", testId: "reviews-today-section", borderClass: "border-l-4 border-l-amber-500", entries: [] },
    { label: "This week", testId: "reviews-this-week-section", borderClass: "border-l-4 border-l-blue-500", entries: [] },
    { label: "Later", testId: "reviews-later-section", borderClass: "border-l-4 border-l-gray-300", entries: [] },
  ];

  for (const entry of entries) {
    const bucket = classifyReviewBucket(entry.next_review_at, now, tz, "now");
    groups[BUCKET_INDEX[bucket]].entries.push(entry);
  }

  return groups;
}

function ReviewTimelinePanel({
  entries,
  isLoading,
  degraded,
  now,
}: {
  entries: ReviewEntry[];
  isLoading: boolean;
  degraded: boolean;
  now: Date;
}) {
  // Owner-configured timezone anchors the Today / This-week boundaries so
  // bucketing is host-timezone independent (bu-fhsph).
  const tz = useTimezone();
  const groups = groupByTimePeriod(entries, now, tz);
  const hasAny = groups.some((g) => g.entries.length > 0);

  return (
    <Section data-testid="reviews-timeline-section">
      <SectionHeader className="pb-2">
        <SectionTitle>Pending reviews</SectionTitle>
      </SectionHeader>
      <SectionContent className="max-h-72 overflow-y-auto">
        {isLoading ? (
          <LoadingLine />
        ) : !hasAny && degraded ? (
          <p className="text-sm text-muted-foreground">Review schedule is incomplete.</p>
        ) : !hasAny ? (
          <EmptyStateLine>
            No reviews scheduled: keep learning and reviews will appear here.
          </EmptyStateLine>
        ) : (
          <div className="space-y-3">
            {groups
              .filter((group) => group.entries.length > 0)
              .map((group) => (
                <div key={group.label} data-testid={group.testId}>
                  <p className="text-xs font-medium text-muted-foreground mb-1">{group.label}</p>
                  <ul
                    className={`divide-y rounded-sm ${group.borderClass}`}
                    data-testid={`${group.testId}-list`}
                  >
                    {group.entries.map((entry) => (
                      <li
                        key={`${entry.mind_map_id}-${entry.node_id}`}
                        className="flex items-center justify-between py-2 pl-2"
                      >
                        <div className="min-w-0">
                          <Link
                            to="/education"
                            className="text-sm font-medium hover:underline truncate block"
                            data-testid="review-item"
                          >
                            {entry.label}
                          </Link>
                          <p className="text-xs text-muted-foreground">{entry.mind_map_title}</p>
                          <p className="text-xs text-muted-foreground" data-testid="review-item-date">
                            <Time value={entry.next_review_at} mode="relative-compact" />
                          </p>
                        </div>
                        <div className="flex items-center gap-2 shrink-0 ml-2">
                          <Badge variant="outline" className="text-xs tnum">
                            {entry.mastery_status}
                          </Badge>
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
          </div>
        )}
      </SectionContent>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Row 3a: Frontier nodes panel
// ---------------------------------------------------------------------------

function FrontierPanel({
  entries,
  isLoading,
  degraded,
}: {
  entries: FrontierEntry[];
  isLoading: boolean;
  degraded: boolean;
}) {
  const top5 = entries.slice(0, 5);

  return (
    <Section data-testid="reviews-frontier-section">
      <SectionHeader className="pb-2">
        <SectionTitle>Ready to learn</SectionTitle>
      </SectionHeader>
      <SectionContent>
        {isLoading ? (
          <LoadingLine />
        ) : top5.length === 0 && degraded ? (
          <p className="text-sm text-muted-foreground">Frontier is incomplete.</p>
        ) : top5.length === 0 ? (
          <EmptyStateLine>No frontier nodes yet. Keep mastering prerequisites!</EmptyStateLine>
        ) : (
          <ul className="divide-y" data-testid="frontier-list">
            {top5.map((entry) => (
              <li
                key={`${entry.mind_map_id}-${entry.id}`}
                className="flex items-center justify-between py-2"
              >
                <div className="min-w-0">
                  <Link
                    to="/education"
                    className="text-sm font-medium hover:underline truncate block"
                    data-testid="frontier-item"
                  >
                    {entry.label}
                  </Link>
                  <p className="text-xs text-muted-foreground">{entry.mind_map_title}</p>
                </div>
                <Badge variant="secondary" className="text-xs tnum shrink-0 ml-2">
                  {Math.round(entry.mastery_score * 100)}%
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </SectionContent>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Row 3b: Retention 7d trend chart (recharts LineChart sparkline)
// ---------------------------------------------------------------------------

/**
 * Custom tooltip styled with design tokens (popover/border).
 */
function RetentionTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ value: number; payload: { x: number; value: number } }>;
}) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div
      className="rounded border border-border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-sm"
      data-testid="retention-tooltip"
    >
      <p className="text-muted-foreground">
        <TooltipDate x={point.x} />
      </p>
      <p className="font-mono tnum font-medium">{point.value}%</p>
    </div>
  );
}

/** Shape of one chart data point. */
interface RetentionPoint {
  date: string;  // ISO date string
  value: number; // mastery_pct * 100 (y axis)
}

const RETENTION_WINDOW_DAYS = 7;

/**
 * Extract retention from an AnalyticsTrendEntry using the canonical key `mastery_pct`.
 *
 * The backend (roster/education/tools/analytics.py) has always emitted `mastery_pct`
 * exclusively; fallback aliases (mastered_pct, mastery_percent) were never emitted and
 * have been removed to surface schema drift immediately (empty state) rather than
 * silently accepting alternate keys.
 *
 * Returns null when `mastery_pct` is absent or not a number, which causes the entry to
 * be excluded from chartData (fail-fast over silent fallback).
 */
function extractMasteryPct(entry: AnalyticsTrendEntry): number | null {
  const v = entry.metrics["mastery_pct"];
  if (typeof v !== "number") {
    return null;
  }
  return Math.max(0, Math.min(100, Math.round(v * (v <= 1 ? 100 : 1))));
}

function RetentionTrendPanel({
  mindMapId, title, mapsDegraded,
}: {
  mindMapId: string | null;
  title: string;
  mapsDegraded: boolean;
}) {
  const { data, isLoading, isError } = useMindMapAnalyticsTrend(mindMapId, 7);

  const chartData = useMemo((): RetentionPoint[] => {
    if (!data?.trend) return [];
    return data.trend.flatMap((entry) => {
      const pct = extractMasteryPct(entry);
      if (pct === null) return [];
      return [{ date: entry.snapshot_date.slice(0, 10), value: pct }];
    });
  }, [data]);
  const windowEnd = useTickingNow(60_000);
  const series = useMemo(() => {
    return buildTimeSeries(
      chartData.map((p) => ({ at: p.date, values: { value: p.value } })),
      ["value"],
      {
        windowStart: windowEnd - RETENTION_WINDOW_DAYS * DAY_MS,
        windowEnd,
        maxGapMs: 3 * DAY_MS,
      },
    );
  }, [chartData, windowEnd]);

  return (
    <Section data-testid="retention-trend-panel">
      <SectionHeader className="pb-2">
        <SectionTitle>Retention · 7d</SectionTitle>
      </SectionHeader>
      <SectionContent>
        {mapsDegraded && <SourceDegradedNote label="Curriculum list" />}
        {isLoading ? (
          <LoadingLine />
        ) : isError ? (
          <SourceDegradedNote label={title} detail="retention trend could not be reached" />
        ) : !mindMapId ? (
          !mapsDegraded && <EmptyStateLine>Select a mind map to see retention trend.</EmptyStateLine>
        ) : chartData.length === 0 ? (
          <EmptyStateLine>No retention data in this window.</EmptyStateLine>
        ) : (
          <div data-testid="retention-chart">
            <div className="flex items-baseline gap-1 mb-2">
              <span
                className="text-2xl font-bold font-mono tnum"
                data-testid="retention-latest-value"
              >
                {chartData[chartData.length - 1]?.value ?? "—"}%
              </span>
              <span className="text-xs text-muted-foreground">mastery</span>
            </div>
            <TimeSeriesChart
              testId="retention-sparkline"
              series={series}
              lines={[{ key: "value", name: "Mastery", stroke: chartColor() }]}
              height={80}
              compact
              yDomain={[0, 100]}
              tooltip={<RetentionTooltip />}
            />
            <p className="sr-only">{`Retention trend · ${series.observationCount} snapshots over ${RETENTION_WINDOW_DAYS} days`}</p>
          </div>
        )}
      </SectionContent>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// ButlerEducationReviewsTab — composed entry point
// ---------------------------------------------------------------------------

export default function ButlerEducationReviewsTab() {
  const { pendingEntries, mastery, frontierEntries, mindMaps, perMapMastery, isLoading, failedSources, mapsDegraded, pendingDegraded, frontierDegraded } = useReviewsTabData();

  // Capture now once — shared by overdueCount and timeline grouping to keep them consistent.
  const now = new Date();
  const overdueCount = pendingEntries.filter(
    (e) => new Date(e.next_review_at) < now,
  ).length;

  // Use the first active mind map as the anchor for the 7d trend panel.
  // If no maps are present the panel renders an empty state.
  const primaryMapId = mindMaps.length > 0 ? mindMaps[0].id : null;

  // Build mind-maps progress items — per-map mastery is already threaded through
  // the aggregate hook so we don't need to call hooks a second time.
  const mindMapProgressItems: MindMapProgressItem[] = mindMaps.map((m, i) => ({
    id: m.id,
    title: m.title,
    mastery: perMapMastery[i] ?? null,
  }));

  return (
    <div className="space-y-4 pt-4" data-testid="education-reviews-tab">
      {/* Row 1: KPI quartet */}
      {failedSources.map((source, index) => <SourceDegradedNote key={`${source.source}-${index}`} label={source.title} detail={`${source.source} could not be reached`} />)}
      {failedSources.length === 0 && <KpiQuartet mastery={mastery} overdueCount={overdueCount} isLoading={isLoading} />}

      {/* Row 2: Mind maps progress + pending reviews timeline */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        <div className="lg:col-span-2">
          <MindMapsProgressPanel items={mindMapProgressItems} isLoading={isLoading} degraded={mapsDegraded} />
        </div>
        <div className="lg:col-span-2">
          <ReviewTimelinePanel entries={pendingEntries} isLoading={isLoading} degraded={pendingDegraded} now={now} />
        </div>
      </div>

      {/* Row 3: Frontier nodes + 7d retention trend */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        <div className="lg:col-span-2">
          <FrontierPanel entries={frontierEntries} isLoading={isLoading} degraded={frontierDegraded} />
        </div>
        <div className="lg:col-span-2">
          <RetentionTrendPanel mindMapId={primaryMapId} title={mindMaps[0]?.title ?? "Curriculum"} mapsDegraded={mapsDegraded} />
        </div>
      </div>
    </div>
  );
}
