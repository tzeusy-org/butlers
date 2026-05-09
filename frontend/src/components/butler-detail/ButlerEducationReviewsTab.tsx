/**
 * ButlerEducationReviewsTab
 *
 * Wires spaced-repetition and mind-map endpoints to the Reviews tab on the
 * education butler detail page. Consumes existing hooks only — no new HTTP
 * routes are added.
 *
 * Three sections:
 *  1. Due now — today's pending reviews, top 5. Click → /education.
 *  2. Mastery KPI strip — total cards, mastered, due today, due this week.
 *  3. Frontier — next ready-to-learn nodes across active maps, top 5.
 */

import { useMemo } from "react";
import { Link } from "react-router";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  useMindMaps,
  usePendingReviews,
  useMasterySummary,
  useFrontierNodes,
} from "@/hooks/use-education";
import type { PendingReviewNode, MindMapNode, MasterySummary } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Due-now helpers
// ---------------------------------------------------------------------------

interface ReviewEntry extends PendingReviewNode {
  mind_map_id: string;
  mind_map_title: string;
}

/** Hook that returns pending reviews for a single map (null-safe wrapper). */
function useMapPendingReviews(mindMapId: string | null) {
  return usePendingReviews(mindMapId);
}

/** Hook that aggregates pending reviews across the first 5 active mind maps. */
function useAggregatedPendingReviews() {
  const { data: mapsResp } = useMindMaps({ status: "active" });
  const maps = mapsResp?.data ?? [];

  // Fixed-count hooks required by React hook rules (no conditional hooks in loops).
  const r0 = useMapPendingReviews(maps[0]?.id ?? null);
  const r1 = useMapPendingReviews(maps[1]?.id ?? null);
  const r2 = useMapPendingReviews(maps[2]?.id ?? null);
  const r3 = useMapPendingReviews(maps[3]?.id ?? null);
  const r4 = useMapPendingReviews(maps[4]?.id ?? null);

  return useMemo(() => {
    const entries: ReviewEntry[] = [];
    const results = [r0, r1, r2, r3, r4];
    for (let i = 0; i < Math.min(maps.length, 5); i++) {
      const nodes = results[i]?.data ?? [];
      for (const node of nodes) {
        entries.push({
          ...node,
          mind_map_id: maps[i].id,
          mind_map_title: maps[i].title,
        });
      }
    }
    // Sort by next_review_at ascending (most overdue first).
    entries.sort(
      (a, b) =>
        new Date(a.next_review_at).getTime() - new Date(b.next_review_at).getTime(),
    );
    return { entries, maps };
  }, [maps, r0, r1, r2, r3, r4]);
}

// ---------------------------------------------------------------------------
// Mastery aggregation helpers
// ---------------------------------------------------------------------------

function useMapMasterySummary(mindMapId: string | null) {
  return useMasterySummary(mindMapId);
}

function useAggregatedMastery() {
  const { data: mapsResp } = useMindMaps({ status: "active" });
  const maps = mapsResp?.data ?? [];

  const s0 = useMapMasterySummary(maps[0]?.id ?? null);
  const s1 = useMapMasterySummary(maps[1]?.id ?? null);
  const s2 = useMapMasterySummary(maps[2]?.id ?? null);
  const s3 = useMapMasterySummary(maps[3]?.id ?? null);
  const s4 = useMapMasterySummary(maps[4]?.id ?? null);

  return useMemo(() => {
    const summaries = [s0, s1, s2, s3, s4]
      .slice(0, maps.length)
      .map((r) => r.data)
      .filter((s): s is MasterySummary => s != null);

    if (summaries.length === 0) return null;

    return summaries.reduce(
      (acc, s) => ({
        total_nodes: acc.total_nodes + s.total_nodes,
        mastered_count: acc.mastered_count + s.mastered_count,
        learning_count: acc.learning_count + s.learning_count,
        reviewing_count: acc.reviewing_count + s.reviewing_count,
        unseen_count: acc.unseen_count + s.unseen_count,
      }),
      { total_nodes: 0, mastered_count: 0, learning_count: 0, reviewing_count: 0, unseen_count: 0 },
    );
  }, [maps.length, s0, s1, s2, s3, s4]);
}

// ---------------------------------------------------------------------------
// Frontier aggregation helpers
// ---------------------------------------------------------------------------

interface FrontierEntry extends MindMapNode {
  mind_map_id: string;
  mind_map_title: string;
}

function useMapFrontier(mindMapId: string | null) {
  return useFrontierNodes(mindMapId);
}

function useAggregatedFrontier() {
  const { data: mapsResp } = useMindMaps({ status: "active" });
  const maps = mapsResp?.data ?? [];

  const f0 = useMapFrontier(maps[0]?.id ?? null);
  const f1 = useMapFrontier(maps[1]?.id ?? null);
  const f2 = useMapFrontier(maps[2]?.id ?? null);
  const f3 = useMapFrontier(maps[3]?.id ?? null);
  const f4 = useMapFrontier(maps[4]?.id ?? null);

  return useMemo(() => {
    const entries: FrontierEntry[] = [];
    const results = [f0, f1, f2, f3, f4];
    for (let i = 0; i < Math.min(maps.length, 5); i++) {
      const nodes = results[i]?.data ?? [];
      for (const node of nodes) {
        entries.push({
          ...node,
          mind_map_id: maps[i].id,
          mind_map_title: maps[i].title,
        });
      }
    }
    // Sort by mastery_score ascending (least mastered first).
    entries.sort((a, b) => a.mastery_score - b.mastery_score);
    return entries;
  }, [maps, f0, f1, f2, f3, f4]);
}

// ---------------------------------------------------------------------------
// Section: Due Now
// ---------------------------------------------------------------------------

/** Empty-state text: serif italic per Dispatch typography guidelines. */
function EmptyStateLine({ children }: { children: React.ReactNode }) {
  return (
    <p
      className="text-sm text-muted-foreground italic font-[family-name:var(--font-serif,serif)]"
      data-testid="empty-state-line"
    >
      {children}
    </p>
  );
}

function DueNowSection() {
  const { entries } = useAggregatedPendingReviews();
  const top5 = entries.slice(0, 5);

  return (
    <Card data-testid="reviews-due-now-section">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Due now</CardTitle>
      </CardHeader>
      <CardContent>
        {top5.length === 0 ? (
          <EmptyStateLine>No reviews due — keep learning!</EmptyStateLine>
        ) : (
          <ul className="divide-y" data-testid="due-now-list">
            {top5.map((entry) => (
              <li
                key={`${entry.mind_map_id}-${entry.node_id}`}
                className="flex items-center justify-between py-2"
              >
                <div>
                  <Link
                    to="/education"
                    className="text-sm font-medium hover:underline"
                    data-testid="due-now-item"
                  >
                    {entry.label}
                  </Link>
                  <p className="text-xs text-muted-foreground">{entry.mind_map_title}</p>
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
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section: Mastery KPI strip
// ---------------------------------------------------------------------------

function MasteryKpiStrip() {
  const aggregated = useAggregatedMastery();
  const { entries: pendingEntries } = useAggregatedPendingReviews();

  const now = new Date();
  const weekEnd = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);

  const dueToday = pendingEntries.filter(
    (e) => new Date(e.next_review_at) <= now,
  ).length;

  const dueThisWeek = pendingEntries.filter(
    (e) => new Date(e.next_review_at) <= weekEnd,
  ).length;

  const kpis = [
    { label: "Total cards", value: aggregated?.total_nodes ?? "—" },
    { label: "Mastered", value: aggregated?.mastered_count ?? "—" },
    { label: "Due today", value: dueToday },
    { label: "Due this week", value: dueThisWeek },
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

function FrontierSection() {
  const frontier = useAggregatedFrontier();
  const top5 = frontier.slice(0, 5);

  return (
    <Card data-testid="reviews-frontier-section">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Ready to learn</CardTitle>
      </CardHeader>
      <CardContent>
        {top5.length === 0 ? (
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
  return (
    <div className="space-y-4 pt-4" data-testid="education-reviews-tab">
      <MasteryKpiStrip />
      <DueNowSection />
      <FrontierSection />
    </div>
  );
}
