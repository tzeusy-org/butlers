import { useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAllPendingReviews, useMindMaps } from "@/hooks/use-education";
import { useTimezone } from "@/components/ui/timezone-context";
import { classifyReviewBucket, type ReviewBucket } from "@/lib/review-buckets";
import type { PendingReviewNode } from "@/api/index.ts";
import type { EducationNodeSelection } from "./types";

interface ReviewEntry extends PendingReviewNode {
  mind_map_title: string;
  mind_map_id: string;
}

// Bucket order for this surface. `weekEnd` is anchored to end-of-today (the
// prior `todayEnd + 7d` semantics), and the Today/This-week boundaries now come
// from owner-tz midnight via classifyReviewBucket — see lib/review-buckets.ts.
const BUCKET_INDEX: Record<ReviewBucket, number> = {
  overdue: 0,
  today: 1,
  "this-week": 2,
  later: 3,
};

function groupByTimePeriod(entries: ReviewEntry[], now: Date, tz: string) {
  const groups: { label: string; entries: ReviewEntry[]; borderClass: string }[] = [
    { label: "Overdue", entries: [], borderClass: "border-l-red-500" },
    { label: "Today", entries: [], borderClass: "border-l-amber-500" },
    { label: "This Week", entries: [], borderClass: "border-l-blue-500" },
    { label: "Later", entries: [], borderClass: "border-l-gray-300" },
  ];

  for (const entry of entries) {
    const bucket = classifyReviewBucket(entry.next_review_at, now, tz, "end-of-today");
    groups[BUCKET_INDEX[bucket]].entries.push(entry);
  }

  return groups.filter((g) => g.entries.length > 0);
}

function ReviewEntryRow({
  entry,
  onSelectNode,
}: {
  entry: ReviewEntry;
  onSelectNode: (selection: EducationNodeSelection) => void;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      className="h-auto w-full justify-between rounded-none px-3 py-2 text-left"
      aria-label={`Open ${entry.label} in ${entry.mind_map_title}`}
      onClick={() =>
        onSelectNode({
          mindMapId: entry.mind_map_id,
          nodeId: entry.node_id,
        })
      }
    >
      <span className="flex flex-col">
        <span className="text-sm font-medium">{entry.label}</span>
        <span className="text-xs text-muted-foreground">{entry.mind_map_title}</span>
      </span>
      <span className="flex items-center gap-2">
        {entry.mastery_score != null ? (
          <Badge variant="outline" className="text-xs">
            {Math.round(entry.mastery_score * 100)}%
          </Badge>
        ) : (
          <Badge variant="outline" className="text-xs capitalize">
            {entry.mastery_status}
          </Badge>
        )}
        <span className="text-xs text-muted-foreground">
          {new Date(entry.next_review_at).toLocaleDateString()}
        </span>
      </span>
    </Button>
  );
}

interface ReviewTimelineProps {
  onSelectNode: (selection: EducationNodeSelection) => void;
}

export default function ReviewTimeline({ onSelectNode }: ReviewTimelineProps) {
  const { data: mindMapsResponse } = useMindMaps({ status: "active" });
  // Stable reference for the data array so the inner useMemo doesn't refire on
  // every render (TanStack Query returns a fresh response object each render).
  const mindMaps = useMemo(() => mindMapsResponse?.data ?? [], [mindMapsResponse?.data]);
  const mapIds = useMemo(() => mindMaps.map((m) => m.id), [mindMaps]);

  // Fetch pending reviews for EVERY active mind map. useAllPendingReviews wraps
  // useQueries so the query count tracks the live map list without violating
  // React's rules of hooks — no arbitrary cap, no map silently dropped.
  const reviewResults = useAllPendingReviews(mapIds);

  // Owner-configured timezone anchors the Today / This-week boundaries so
  // bucketing is host-timezone independent (bu-fhsph).
  const tz = useTimezone();

  const allEntries = useMemo(() => {
    const entries: ReviewEntry[] = [];
    for (let i = 0; i < mindMaps.length; i++) {
      const nodes = reviewResults[i]?.data ?? [];
      for (const node of nodes) {
        entries.push({
          ...node,
          mind_map_title: mindMaps[i].title,
          mind_map_id: mindMaps[i].id,
        });
      }
    }
    entries.sort(
      (a, b) =>
        new Date(a.next_review_at).getTime() - new Date(b.next_review_at).getTime(),
    );
    return entries;
  }, [mindMaps, reviewResults]);

  const groups = useMemo(
    () => groupByTimePeriod(allEntries, new Date(), tz),
    [allEntries, tz],
  );

  if (allEntries.length === 0) {
    return (
      <Card>
        <CardContent className="flex h-48 items-center justify-center text-muted-foreground">
          No reviews scheduled. Keep learning and reviews will appear here.
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {groups.map((group) => (
        <Card key={group.label}>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">{group.label}</CardTitle>
          </CardHeader>
          <CardContent
            className={`divide-y border-l-4 ${group.borderClass}`}
          >
            {group.entries.map((entry) => (
              <ReviewEntryRow
                key={`${entry.mind_map_id}-${entry.node_id}`}
                entry={entry}
                onSelectNode={onSelectNode}
              />
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
