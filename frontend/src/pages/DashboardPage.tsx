import { Link } from "react-router";

import { NotificationFeed } from "@/components/notifications/notification-feed";
import { NotificationTableSkeleton } from "@/components/skeletons";
import IssuesPanel from "@/components/issues/IssuesPanel";
import TopologyGraph from "@/components/topology/TopologyGraph";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useButlers } from "@/hooks/use-butlers";
import { useCostSummary } from "@/hooks/use-costs";
import { useIssues } from "@/hooks/use-issues";
import { useNotifications } from "@/hooks/use-notifications";
import { useSessions } from "@/hooks/use-sessions";

function StatsCard({
  title,
  value,
  description,
}: {
  title: string;
  value: string | number;
  description?: string;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-muted-foreground text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        {description && <p className="text-muted-foreground mt-1 text-xs">{description}</p>}
      </CardContent>
    </Card>
  );
}

function StatsBarSkeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <Card key={i}>
          <CardHeader className="pb-2">
            <div className="h-4 w-24 animate-pulse rounded bg-muted" />
          </CardHeader>
          <CardContent>
            <div className="h-8 w-16 animate-pulse rounded bg-muted" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

export default function DashboardPage() {
  const { data: butlersResponse, isLoading: butlersLoading } = useButlers();
  const { data: costSummaryResponse, isLoading: costSummaryLoading } = useCostSummary("today");
  const { data: sessionsTodayResponse, isLoading: sessionsTodayLoading } = useSessions({
    limit: 1,
    offset: 0,
    since: new Date(new Date().setHours(0, 0, 0, 0)).toISOString(),
  }, { refetchInterval: 60_000 });
  const { data: issuesResponse, isLoading: issuesLoading } = useIssues();
  const { data: failedResponse, isLoading: failedLoading } = useNotifications({
    status: "failed",
    limit: 5,
  });

  const butlers = butlersResponse?.data ?? [];
  const totalButlers = butlers.length;
  const healthyButlers = butlers.filter((b) => b.status === "ok").length;

  const failedNotifications = failedResponse?.data ?? [];
  const failedTotal = failedResponse?.meta.total ?? 0;
  const sessionsToday = sessionsTodayResponse?.meta.total ?? 0;
  const costToday = costSummaryResponse?.data.total_cost_usd ?? 0;
  const issues = issuesResponse?.data ?? [];

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Overview</h1>

      {/* Aggregate Stats Bar */}
      {butlersLoading ? (
        <StatsBarSkeleton />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatsCard title="Total Butlers" value={totalButlers} />
          <StatsCard
            title="Healthy"
            value={healthyButlers}
            description={
              totalButlers > 0
                ? `${Math.round((healthyButlers / totalButlers) * 100)}% online`
                : undefined
            }
          />
          <StatsCard
            title="Sessions Today"
            value={sessionsTodayLoading ? "--" : sessionsToday}
          />
          <StatsCard
            title="Est. Cost Today"
            value={costSummaryLoading ? "--" : `$${costToday.toFixed(2)}`}
          />
        </div>
      )}

      {/* Butler Topology */}
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="lg:col-span-2">
          <TopologyGraph butlers={butlers} isLoading={butlersLoading} />
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              Failed Notifications
              {!failedLoading && failedTotal > 0 && (
                <Badge variant="destructive">{failedTotal}</Badge>
              )}
            </CardTitle>
            <CardDescription>
              Recent notification delivery failures across all butlers
            </CardDescription>
            <CardAction>
              <Button variant="link" size="sm" asChild>
                <Link to="/notifications">View all notifications</Link>
              </Button>
            </CardAction>
          </CardHeader>
          <CardContent>
            {failedLoading ? (
              <NotificationTableSkeleton rows={5} />
            ) : failedNotifications.length === 0 ? (
              <div className="text-muted-foreground flex flex-col items-center justify-center py-8 text-sm">
                <p>No failed notifications. All systems healthy.</p>
              </div>
            ) : (
              <NotificationFeed notifications={failedNotifications} isLoading={false} />
            )}
          </CardContent>
        </Card>

        <IssuesPanel issues={issues} isLoading={issuesLoading} />
      </div>
    </div>
  );
}
