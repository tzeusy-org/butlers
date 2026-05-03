import { Badge } from "@/components/ui/badge";
import { Time } from "@/components/ui/time";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useMemoryActivity } from "@/hooks/use-memory";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function typeBadge(type: string) {
  switch (type) {
    case "episode":
      return <Badge variant="secondary">Episode</Badge>;
    case "fact":
      return (
        <Badge className="bg-sky-600 text-white hover:bg-sky-600/90">
          Fact
        </Badge>
      );
    case "rule":
      return (
        <Badge className="bg-violet-600 text-white hover:bg-violet-600/90">
          Rule
        </Badge>
      );
    default:
      return <Badge variant="secondary">{type}</Badge>;
  }
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function TimelineSkeleton() {
  return (
    <div className="space-y-4">
      {Array.from({ length: 5 }, (_, i) => (
        <div key={i} className="flex gap-3">
          <Skeleton className="size-6 shrink-0 rounded-full" />
          <div className="flex-1 space-y-1">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-3 w-1/3" />
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// MemoryActivityTimeline
// ---------------------------------------------------------------------------

interface MemoryActivityTimelineProps {
  limit?: number;
}

export default function MemoryActivityTimeline({
  limit = 30,
}: MemoryActivityTimelineProps) {
  const { data: response, isLoading } = useMemoryActivity(limit);
  const items = response?.data ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent Activity</CardTitle>
        <CardDescription>Latest memory events across all tiers</CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <TimelineSkeleton />
        ) : items.length === 0 ? (
          <p className="text-muted-foreground py-6 text-center text-sm">
            No recent activity.
          </p>
        ) : (
          <div className="relative space-y-0">
            {/* Vertical line */}
            <div className="bg-border absolute left-3 top-0 bottom-0 w-px" />

            {items.map((item) => (
              <div
                key={`${item.type}-${item.id}`}
                className="relative flex gap-4 py-3 pl-8"
              >
                {/* Dot on the timeline */}
                <div className="bg-background absolute left-1.5 top-4 size-3 rounded-full border-2 border-current text-muted-foreground" />

                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    {typeBadge(item.type)}
                    {item.butler && (
                      <Badge variant="outline" className="text-xs">
                        {item.butler}
                      </Badge>
                    )}
                  </div>
                  <p className="mt-1 truncate text-sm">{item.summary}</p>
                  <p className="text-muted-foreground mt-0.5 text-xs">
                    <Time value={item.created_at} mode="absolute" />
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
