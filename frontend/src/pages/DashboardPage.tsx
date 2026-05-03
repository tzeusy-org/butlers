import { Link } from "react-router";
import { Time } from "@/components/ui/time";

import { RecentMoments } from "@/components/dashboard/RecentMoments";
import { SessionStripeChart } from "@/components/dashboard/SessionStripeChart";
import { NotificationFeed } from "@/components/notifications/notification-feed";
import { NotificationTableSkeleton } from "@/components/skeletons";
import IssuesPanel from "@/components/issues/IssuesPanel";
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
import { Page } from "@/components/ui/page";
import { useApprovalMetrics } from "@/hooks/use-approvals";
import { useButlers } from "@/hooks/use-butlers";
import { useCostSummary } from "@/hooks/use-costs";
import { useIssues } from "@/hooks/use-issues";
import { useNotifications } from "@/hooks/use-notifications";
import { useSessions } from "@/hooks/use-sessions";
import { useQaSummary } from "@/hooks/use-qa";

function StatItem({ label, value }: { label: string; value: string | number }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-foreground text-sm font-medium tabular-nums">{value}</span>
      <span className="text-muted-foreground text-xs">{label}</span>
    </span>
  );
}

function StatStripSkeleton() {
  return (
    <div
      className="flex flex-wrap items-center gap-x-6 gap-y-1 border-t border-border pt-3"
      role="status"
      aria-label="Loading stats"
    >
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="h-4 w-20 animate-pulse rounded bg-muted" />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// QA widget
// ---------------------------------------------------------------------------

function QaWidget() {
  const { data: summaryResponse, isLoading, isError } = useQaSummary();
  const summary = summaryResponse?.data;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          QA Staffer
          {!isLoading && summary && summary.stats_24h.dispatched_investigations > 0 && (
            <Badge variant="secondary">{summary.stats_24h.dispatched_investigations} active</Badge>
          )}
        </CardTitle>
        <CardDescription>System-wide quality patrol status</CardDescription>
        <CardAction>
          <Button variant="link" size="sm" asChild>
            <Link to="/qa">View QA dashboard</Link>
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            <div className="h-4 w-full animate-pulse rounded bg-muted" />
            <div className="h-4 w-2/3 animate-pulse rounded bg-muted" />
          </div>
        ) : isError ? (
          <p className="text-destructive text-sm">Failed to load QA status.</p>
        ) : !summary?.last_patrol ? (
          <p className="text-muted-foreground text-sm">QA Staffer not active.</p>
        ) : (
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted-foreground">Last patrol</dt>
            <dd><Time value={summary.last_patrol.started_at} mode="absolute" /></dd>

            <dt className="text-muted-foreground">Status</dt>
            <dd>
              <span
                className={
                  summary.last_patrol.status === "clean"
                    ? "text-emerald-600 font-medium"
                    : summary.last_patrol.status === "error"
                      ? "text-destructive font-medium"
                      : "text-foreground"
                }
              >
                {summary.last_patrol.status}
              </span>
            </dd>

            <dt className="text-muted-foreground">Patrols (24h)</dt>
            <dd>{summary.stats_24h.patrols_completed}</dd>

            <dt className="text-muted-foreground">Findings (24h)</dt>
            <dd>
              {summary.stats_24h.total_findings}
              {summary.stats_24h.novel_findings > 0 && (
                <span className="text-muted-foreground ml-1 text-xs">
                  ({summary.stats_24h.novel_findings} novel)
                </span>
              )}
            </dd>
          </dl>
        )}
      </CardContent>
    </Card>
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
  const { data: approvalMetricsResponse, isLoading: approvalsLoading } = useApprovalMetrics();

  const butlers = butlersResponse?.data ?? [];
  const totalButlers = butlers.length;
  const healthyButlers = butlers.filter((b) => b.status === "ok").length;

  const failedNotifications = failedResponse?.data ?? [];
  const failedTotal = failedResponse?.meta.total ?? 0;
  const sessionsToday = sessionsTodayResponse?.meta.total ?? 0;
  const costToday = costSummaryResponse?.data.total_cost_usd ?? 0;
  const issues = issuesResponse?.data ?? [];
  const pendingApprovals = approvalMetricsResponse?.data.total_pending ?? 0;

  return (
    <Page archetype="overview" title="Overview">
      {/* Hero region: session stripe chart (primary visualization) */}
      <Card>
        <CardHeader>
          <CardTitle>Sessions</CardTitle>
          <CardDescription>Butler activity over the past 24 hours</CardDescription>
        </CardHeader>
        <CardContent>
          <SessionStripeChart butlers={butlers} />
        </CardContent>
      </Card>

      {/* Secondary region: recent moments feed */}
      <Card>
        <CardHeader>
          <CardTitle>Recent Activity</CardTitle>
          <CardDescription>Latest butler actions</CardDescription>
        </CardHeader>
        <CardContent>
          <RecentMoments limit={7} />
        </CardContent>
      </Card>

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

      {/* QA Widget */}
      <QaWidget />

      {/* Supporting stat strip -- quiet context, not hero */}
      {butlersLoading ? (
        <StatStripSkeleton />
      ) : (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 border-t border-border pt-3">
          <StatItem
            label={`of ${totalButlers} healthy`}
            value={healthyButlers}
          />
          <StatItem
            label="sessions today"
            value={sessionsTodayLoading ? "--" : sessionsToday}
          />
          <StatItem
            label="est. cost today"
            value={costSummaryLoading ? "--" : `$${costToday.toFixed(2)}`}
          />
          <StatItem
            label="pending approvals"
            value={approvalsLoading ? "--" : pendingApprovals}
          />
        </div>
      )}
    </Page>
  );
}
