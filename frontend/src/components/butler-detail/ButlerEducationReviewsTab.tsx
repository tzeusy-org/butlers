/**
 * ButlerEducationReviewsTab
 *
 * Wires spaced-repetition and mind-map endpoints to the Reviews tab on the
 * education butler detail page. Consumes existing hooks only — no new HTTP
 * routes are added.
 *
 * Three sections:
 *  1. Mastery KPI strip — total cards, mastered, overdue count.
 *  2. Review timeline — grouped by Overdue / Today / This Week / Later with
 *     color-coded left borders per spec (dashboard-education-ui).
 *  3. Frontier — next ready-to-learn nodes across active maps, top 5.
 *
 * All hooks are called once at the top level and passed down to avoid
 * duplicate hook calls and reduce rerender churn.
 */

import { useMemo } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  useMindMaps,
  useAllPendingReviews,
  useAllMasterySummaries,
  useAllFrontierNodes,
} from "@/hooks/use-education";
import type { PendingReviewNode, MindMapNode, MasterySummary } from "@/api/index.ts";

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

/** Aggregated mastery counts across all active mind maps. Only count fields are
 *  available — per-map identifiers and diagnostic scores are not preserved. */
interface AggregatedMastery {
  total_nodes: number;
  mastered_count: number;
  learning_count: number;
  reviewing_count: number;
  unseen_count: number;
}

interface AggregatedData {
  pendingEntries: ReviewEntry[];
  mastery: AggregatedMastery | null;
  frontierEntries: FrontierEntry[];
  isLoading: boolean;
}

// ---------------------------------------------------------------------------
// Top-level aggregation hook — called once in the parent
// ---------------------------------------------------------------------------

/** Aggregates all data for the Reviews tab in a single hook call per data type. */
function useReviewsTabData(): AggregatedData {
  const { data: mapsResp, isLoading: mapsLoading } = useMindMaps({ status: "active" });
  const maps = mapsResp?.data ?? [];
  const mapIds = maps.map((m) => m.id);

  // useQueries handles a dynamic number of queries without violating hook rules.
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
            }),
            {
              total_nodes: 0,
              mastered_count: 0,
              learning_count: 0,
              reviewing_count: 0,
              unseen_count: 0,
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

    return { pendingEntries, mastery, frontierEntries, isLoading };
  }, [maps, pendingResults, summaryResults, frontierResults, isLoading]);
}

// ---------------------------------------------------------------------------
// Shared primitives
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
// Section: Review Timeline (Overdue / Today / This Week / Later)
// ---------------------------------------------------------------------------

interface TimelineGroup {
  label: string;
  testId: string;
  borderClass: string;
  entries: ReviewEntry[];
}

function groupByTimePeriod(entries: ReviewEntry[], now: Date): TimelineGroup[] {
  const todayEnd = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59, 999);
  // weekEnd: strictly 7 days from now (not from end-of-today), so items are not
  // misclassified by more than a few seconds near the boundary.
  const weekEnd = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);

  const groups: TimelineGroup[] = [
    { label: "Overdue", testId: "reviews-overdue-section", borderClass: "border-l-4 border-l-red-500", entries: [] },
    { label: "Today", testId: "reviews-today-section", borderClass: "border-l-4 border-l-amber-500", entries: [] },
    { label: "This Week", testId: "reviews-this-week-section", borderClass: "border-l-4 border-l-blue-500", entries: [] },
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

function ReviewTimelineSection({
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

  if (isLoading) {
    return (
      <Card data-testid="reviews-timeline-section">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Reviews</CardTitle>
        </CardHeader>
        <CardContent>
          <LoadingLine />
        </CardContent>
      </Card>
    );
  }

  if (!hasAny) {
    return (
      <Card data-testid="reviews-timeline-section">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Reviews</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyStateLine>
            No reviews scheduled — keep learning and reviews will appear here.
          </EmptyStateLine>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-3" data-testid="reviews-timeline-section">
      {groups
        .filter((group) => group.entries.length > 0)
        .map((group) => (
          <Card key={group.label} data-testid={group.testId}>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">{group.label}</CardTitle>
            </CardHeader>
            <CardContent className={group.borderClass}>
              <ul className="divide-y" data-testid={`${group.testId}-list`}>
                {group.entries.map((entry) => (
                  <li
                    key={`${entry.mind_map_id}-${entry.node_id}`}
                    className="flex items-center justify-between py-2"
                  >
                    <div>
                      <Link
                        to="/education"
                        className="text-sm font-medium hover:underline"
                        data-testid="review-item"
                      >
                        {entry.label}
                      </Link>
                      <p className="text-xs text-muted-foreground">{entry.mind_map_title}</p>
                      <p className="text-xs text-muted-foreground" data-testid="review-item-date">
                        {new Date(entry.next_review_at).toLocaleString(undefined, {
                          month: "short",
                          day: "numeric",
                          hour: "numeric",
                          minute: "2-digit",
                        })}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <Badge variant="outline" className="text-xs font-mono">
                        {entry.mastery_status}
                      </Badge>
                      <Link
                        to="/education"
                        className="text-xs text-muted-foreground hover:underline"
                      >
                        Review →
                      </Link>
                    </div>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section: Mastery KPI strip
// ---------------------------------------------------------------------------

function MasteryKpiStrip({
  mastery,
  overdueCount,
  isLoading,
}: {
  mastery: AggregatedData["mastery"];
  overdueCount: number;
  isLoading: boolean;
}) {
  const kpis = [
    { label: "Total cards", value: isLoading ? "…" : (mastery?.total_nodes ?? "—") },
    { label: "Mastered", value: isLoading ? "…" : (mastery?.mastered_count ?? "—") },
    { label: "Overdue", value: isLoading ? "…" : overdueCount },
  ];

  return (
    <div
      className="grid grid-cols-2 gap-3 sm:grid-cols-3"
      data-testid="mastery-kpi-strip"
    >
      {kpis.map((kpi) => (
        <Card key={kpi.label}>
          <CardContent className="pt-4">
            <p className="text-xs text-muted-foreground">{kpi.label}</p>
            <p className="text-2xl font-bold font-mono" data-testid="kpi-value">
              {kpi.value}
            </p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section: Frontier
// ---------------------------------------------------------------------------

function FrontierSection({
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
          <EmptyStateLine>No frontier nodes yet — keep mastering prerequisites!</EmptyStateLine>
        ) : (
          <ul className="divide-y" data-testid="frontier-list">
            {top5.map((entry) => (
              <li
                key={`${entry.mind_map_id}-${entry.id}`}
                className="flex items-center justify-between py-2"
              >
                <div>
                  <Link
                    to="/education"
                    className="text-sm font-medium hover:underline"
                    data-testid="frontier-item"
                  >
                    {entry.label}
                  </Link>
                  <p className="text-xs text-muted-foreground">{entry.mind_map_title}</p>
                </div>
                <Badge variant="secondary" className="text-xs font-mono shrink-0">
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
// ButlerEducationReviewsTab — composed entry point
// ---------------------------------------------------------------------------

export default function ButlerEducationReviewsTab() {
  const { pendingEntries, mastery, frontierEntries, isLoading } = useReviewsTabData();

  // Capture now once — shared by overdueCount and timeline grouping to keep them consistent.
  const now = new Date();
  const overdueCount = pendingEntries.filter(
    (e) => new Date(e.next_review_at) < now,
  ).length;

  return (
    <div className="space-y-4 pt-4" data-testid="education-reviews-tab">
      <MasteryKpiStrip
        mastery={mastery}
        overdueCount={overdueCount}
        isLoading={isLoading}
      />
      <ReviewTimelineSection entries={pendingEntries} isLoading={isLoading} now={now} />
      <FrontierSection entries={frontierEntries} isLoading={isLoading} />
    </div>
  );
}
