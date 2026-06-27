import { useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useAllPendingReviews, useMindMaps } from "@/hooks/use-education";
import type { PendingReviewNode } from "@/api/index.ts";

interface ReviewEntry extends PendingReviewNode {
  mind_map_title: string;
  mind_map_id: string;
}

function groupByTimePeriod(entries: ReviewEntry[]) {
  const now = new Date();
  const todayEnd = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59);
  const weekEnd = new Date(todayEnd.getTime() + 7 * 24 * 60 * 60 * 1000);

  const groups: { label: string; entries: ReviewEntry[]; borderClass: string }[] = [
    { label: "Overdue", entries: [], borderClass: "border-l-red-500" },
    { label: "Today", entries: [], borderClass: "border-l-amber-500" },
    { label: "This Week", entries: [], borderClass: "border-l-blue-500" },
    { label: "Later", entries: [], borderClass: "border-l-gray-300" },
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

  return groups.filter((g) => g.entries.length > 0);
}

function ReviewEntryRow({ entry }: { entry: ReviewEntry }) {
  return (
    <div className="flex items-center justify-between py-2">
      <div>
        <p className="text-sm font-medium">{entry.label}</p>
        <p className="text-xs text-muted-foreground">{entry.mind_map_title}</p>
      </div>
      <div className="flex items-center gap-2">
        <Badge variant="outline" className="text-xs">
          {Math.round(
            (entry.mastery_status === "mastered" ? 1.0 : 0.5) * 100,
          )}%
        </Badge>
        <span className="text-xs text-muted-foreground">
          {new Date(entry.next_review_at).toLocaleDateString()}
        </span>
      </div>
    </div>
  );
}

export default function ReviewTimeline() {
  const { data: mindMapsResponse } = useMindMaps({ status: "active" });
  // Stable reference for the data array so the inner useMemo doesn't refire on
  // every render (TanStack Query returns a fresh response object each render).
  const mindMaps = useMemo(() => mindMapsResponse?.data ?? [], [mindMapsResponse?.data]);
  const mapIds = useMemo(() => mindMaps.map((m) => m.id), [mindMaps]);

  // Fetch pending reviews for EVERY active mind map. useAllPendingReviews wraps
  // useQueries so the query count tracks the live map list without violating
  // React's rules of hooks — no arbitrary cap, no map silently dropped.
  const reviewResults = useAllPendingReviews(mapIds);

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

  const groups = useMemo(() => groupByTimePeriod(allEntries), [allEntries]);

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
              />
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
