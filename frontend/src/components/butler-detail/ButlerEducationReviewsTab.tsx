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
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
} from "recharts";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Time } from "@/components/ui/time";
import {
  useMindMaps,
  useAllPendingReviews,
  useAllMasterySummaries,
  useAllFrontierNodes,
  useMindMapAnalyticsTrend,
} from "@/hooks/use-education";
import { ErrorLine } from "@/components/butler-detail/atoms";
import { toneClass } from "@/components/butler-detail/atoms-utils";
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
}

// ---------------------------------------------------------------------------
// Top-level aggregation hook
// ---------------------------------------------------------------------------

/** Aggregates all data for the Reviews tab in a single hook call per data type. */
function useReviewsTabData(): AggregatedData {
  const { data: mapsResp, isLoading: mapsLoading } = useMindMaps({ status: "active" });
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

    return {
      pendingEntries,
      mastery,
      frontierEntries,
      mindMaps: maps.map((m) => ({ id: m.id, title: m.title, status: m.status })),
      perMapMastery,
      isLoading,
    };
  }, [maps, pendingResults, summaryResults, frontierResults, isLoading]);
}

// ---------------------------------------------------------------------------
// Shared UI primitives
// ---------------------------------------------------------------------------

/** Empty-state text: serif italic per Dispatch typography guidelines. */
function EmptyStateLine({ children }: { children: ReactNode }) {
  return (
    <p
      className="text-sm text-muted-foreground italic font-[family-name:var(--font-serif,serif)]"
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
        <Card key={kpi.label}>
          <CardContent className="pt-4">
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
          </CardContent>
        </Card>
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
}: {
  items: MindMapProgressItem[];
  isLoading: boolean;
}) {
  return (
    <Card data-testid="mind-maps-progress-panel">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Mind maps</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingLine />
        ) : items.length === 0 ? (
          <EmptyStateLine>No active mind maps (start learning to see progress here).</EmptyStateLine>
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
      </CardContent>
    </Card>
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

function groupByTimePeriod(entries: ReviewEntry[], now: Date): TimelineGroup[] {
  const todayEnd = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59, 999);
  // weekEnd: strictly 7 days from now.
  const weekEnd = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);

  const groups: TimelineGroup[] = [
    { label: "Overdue", testId: "reviews-overdue-section", borderClass: "border-l-4 border-l-red-500", entries: [] },
    { label: "Today", testId: "reviews-today-section", borderClass: "border-l-4 border-l-amber-500", entries: [] },
    { label: "This week", testId: "reviews-this-week-section", borderClass: "border-l-4 border-l-blue-500", entries: [] },
    { label: "Later", testId: "reviews-later-section", borderClass: "border-l-4 border-l-gray-300", entries: [] },
  ];

  for (const entry of entries) {
    const reviewDate = new Date(entry.next_review_at);
    if (reviewDate < now) {
      groups[0].entries.push(entry);
    } else if (reviewDate <= todayEnd) {
      groups[1].entries.push(entry);
    } else if (reviewDate <= weekEnd) {
      groups[2].entries.push(entry);
    } else {
      groups[3].entries.push(entry);
    }
  }

  return groups;
}

function ReviewTimelinePanel({
  entries,
  isLoading,
  now,
}: {
  entries: ReviewEntry[];
  isLoading: boolean;
  now: Date;
}) {
  const groups = groupByTimePeriod(entries, now);
  const hasAny = groups.some((g) => g.entries.length > 0);

  return (
    <Card data-testid="reviews-timeline-section">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Pending reviews</CardTitle>
      </CardHeader>
      <CardContent className="max-h-72 overflow-y-auto">
        {isLoading ? (
          <LoadingLine />
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
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Row 3a: Frontier nodes panel
// ---------------------------------------------------------------------------

function FrontierPanel({
  entries,
  isLoading,
}: {
  entries: FrontierEntry[];
  isLoading: boolean;
}) {
  const top5 = entries.slice(0, 5);

  return (
    <Card data-testid="reviews-frontier-section">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Ready to learn</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingLine />
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
      </CardContent>
    </Card>
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
  payload?: Array<{ value: number; payload: { date: string; value: number } }>;
}) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div
      className="rounded border border-border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-sm"
      data-testid="retention-tooltip"
    >
      <p className="text-muted-foreground">{point.date}</p>
      <p className="font-mono tnum font-medium">{point.value}%</p>
    </div>
  );
}

/** Shape of one chart data point. */
interface RetentionPoint {
  date: string;  // ISO date string (x axis)
  value: number; // mastery_pct * 100 (y axis)
}

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
  mindMapId,
}: {
  mindMapId: string | null;
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

  return (
    <Card data-testid="retention-trend-panel">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Retention · 7d</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingLine />
        ) : isError ? (
          <ErrorLine>Could not load retention trend.</ErrorLine>
        ) : !mindMapId ? (
          <EmptyStateLine>Select a mind map to see retention trend.</EmptyStateLine>
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
            <div data-testid="retention-sparkline">
              <ResponsiveContainer width="100%" height={80}>
                <LineChart data={chartData} margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
                  <XAxis dataKey="date" hide />
                  <YAxis hide domain={[0, 100]} />
                  <Tooltip
                    content={<RetentionTooltip />}
                    isAnimationActive={false}
                  />
                  <Line
                    dataKey="value"
                    type="monotone"
                    stroke="hsl(var(--primary))"
                    dot={false}
                    strokeWidth={1.5}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <p className="sr-only">{`Retention trend · ${chartData.length} snapshots over 7 days`}</p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// ButlerEducationReviewsTab — composed entry point
// ---------------------------------------------------------------------------

export default function ButlerEducationReviewsTab() {
  const { pendingEntries, mastery, frontierEntries, mindMaps, perMapMastery, isLoading } = useReviewsTabData();

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
      <KpiQuartet mastery={mastery} overdueCount={overdueCount} isLoading={isLoading} />

      {/* Row 2: Mind maps progress + pending reviews timeline */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        <div className="lg:col-span-2">
          <MindMapsProgressPanel items={mindMapProgressItems} isLoading={isLoading} />
        </div>
        <div className="lg:col-span-2">
          <ReviewTimelinePanel entries={pendingEntries} isLoading={isLoading} now={now} />
        </div>
      </div>

      {/* Row 3: Frontier nodes + 7d retention trend */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        <div className="lg:col-span-2">
          <FrontierPanel entries={frontierEntries} isLoading={isLoading} />
        </div>
        <div className="lg:col-span-2">
          <RetentionTrendPanel mindMapId={primaryMapId} />
        </div>
      </div>
    </div>
  );
}
