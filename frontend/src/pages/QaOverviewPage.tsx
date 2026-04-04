import { useState } from "react";
import { Link } from "react-router";

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
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useDismissQaIssue, useQaKnownIssues, useQaPatrols, useQaSummary, useUndismissQaIssue } from "@/hooks/use-qa";
import type { QaKnownIssue, QaPatrolSummary } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTs(iso: string | null | undefined): string {
  if (!iso) return "--";
  return new Date(iso).toLocaleString();
}

function formatDuration(start: string, end: string | null | undefined): string {
  if (!end) return "running...";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 0) return "--";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "--";
  const diff = Date.now() - new Date(iso).getTime();
  const minutes = Math.round(diff / 60_000);
  if (minutes < 2) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

// ---------------------------------------------------------------------------
// Severity badge
// ---------------------------------------------------------------------------

function SeverityBadge({ severity }: { severity: number }) {
  const labels: Record<number, string> = { 0: "critical", 1: "high", 2: "medium", 3: "low", 4: "info" };
  const label = labels[severity] ?? String(severity);
  const classNames: Record<number, string> = {
    0: "bg-red-600 text-white hover:bg-red-600/90",
    1: "bg-orange-500 text-white hover:bg-orange-500/90",
    2: "bg-yellow-500 text-white hover:bg-yellow-500/90",
    3: "bg-slate-400 text-white hover:bg-slate-400/90",
    4: "bg-sky-400 text-white hover:bg-sky-400/90",
  };
  return <Badge className={classNames[severity] ?? ""}>{label}</Badge>;
}

// ---------------------------------------------------------------------------
// Source type badge
// ---------------------------------------------------------------------------

function SourceTypeBadge({ sourceType }: { sourceType: string }) {
  const labels: Record<string, string> = {
    log_scanner: "log",
    session_records: "session",
    butler_reports: "butler",
  };
  return (
    <Badge variant="secondary" className="font-mono text-xs">
      {labels[sourceType] ?? sourceType}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Patrol status badge
// ---------------------------------------------------------------------------

function PatrolStatusBadge({ status }: { status: string }) {
  if (status === "clean") {
    return <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">clean</Badge>;
  }
  if (status === "findings_dispatched") {
    return <Badge className="bg-blue-600 text-white hover:bg-blue-600/90">dispatched</Badge>;
  }
  if (status === "running") {
    return (
      <Badge variant="outline" className="border-amber-500 text-amber-600">
        running
      </Badge>
    );
  }
  if (status === "error") {
    return <Badge variant="destructive">error</Badge>;
  }
  if (status === "skipped_overlap") {
    return <Badge variant="secondary">skipped</Badge>;
  }
  return <Badge variant="outline">{status}</Badge>;
}

// ---------------------------------------------------------------------------
// Stats card
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Recent patrols table
// ---------------------------------------------------------------------------

function RecentPatrolsTable({ patrols }: { patrols: QaPatrolSummary[] }) {
  if (patrols.length === 0) {
    return (
      <div className="text-muted-foreground py-8 text-center text-sm">
        No patrol cycles recorded yet.
      </div>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Started</TableHead>
          <TableHead>Duration</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Sources</TableHead>
          <TableHead className="text-right">Findings</TableHead>
          <TableHead className="text-right">Novel</TableHead>
          <TableHead className="text-right">Dispatched</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {patrols.map((p) => (
          <TableRow key={p.id}>
            <TableCell>
              <Link
                to={`/qa/patrols/${p.id}`}
                className="text-primary font-mono text-xs underline-offset-4 hover:underline"
              >
                {formatTs(p.started_at)}
              </Link>
            </TableCell>
            <TableCell className="text-muted-foreground text-xs">
              {formatDuration(p.started_at, p.completed_at)}
            </TableCell>
            <TableCell>
              <PatrolStatusBadge status={p.status} />
            </TableCell>
            <TableCell>
              <div className="flex flex-wrap gap-1">
                {p.sources_polled.map((s) => (
                  <SourceTypeBadge key={s} sourceType={s} />
                ))}
              </div>
            </TableCell>
            <TableCell className="text-right">{p.findings_count}</TableCell>
            <TableCell className="text-right">{p.novel_count}</TableCell>
            <TableCell className="text-right">{p.dispatched_count}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// ---------------------------------------------------------------------------
// Known issues panel
// ---------------------------------------------------------------------------

function KnownIssuesPanel() {
  const [showDismissed, setShowDismissed] = useState(false);
  const { data: response, isLoading, isError } = useQaKnownIssues({
    dismissed: showDismissed ? undefined : false,
    limit: 50,
  });
  const dismissMutation = useDismissQaIssue();
  const undismissMutation = useUndismissQaIssue();
  const [dismissingFp, setDismissingFp] = useState<string | null>(null);
  const issues = response?.data ?? [];

  if (isLoading) {
    return (
      <div className="space-y-2">
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <div className="text-destructive py-8 text-center text-sm">
        Failed to load known issues.
      </div>
    );
  }

  function handleDismiss(issue: QaKnownIssue) {
    setDismissingFp(issue.fingerprint);
    dismissMutation.mutate(
      { fingerprint: issue.fingerprint, body: { dismissed_by: "dashboard_user" } },
      { onSettled: () => setDismissingFp(null) },
    );
  }

  function handleUndismiss(issue: QaKnownIssue) {
    setDismissingFp(issue.fingerprint);
    undismissMutation.mutate(issue.fingerprint, { onSettled: () => setDismissingFp(null) });
  }

  const isDismissed = (issue: QaKnownIssue): boolean =>
    issue.dismissal !== null &&
    new Date(issue.dismissal.dismissed_until) > new Date();

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setShowDismissed((v) => !v)}
        >
          {showDismissed ? "Hide dismissed" : "Show dismissed"}
        </Button>
      </div>

      {issues.length === 0 && (
        <div className="text-muted-foreground py-8 text-center text-sm">
          {showDismissed ? "No known issues." : "No active issues. System is clean."}
        </div>
      )}

      {issues.map((issue) => {
        const dismissed = isDismissed(issue);
        return (
          <div
            key={issue.fingerprint}
            className={`rounded-md border p-3 text-sm ${dismissed ? "opacity-50" : ""}`}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <SeverityBadge severity={issue.severity} />
                  <SourceTypeBadge sourceType={issue.source_type} />
                  <Badge variant="outline" className="font-mono text-xs">
                    {issue.source_butler}
                  </Badge>
                  <code className="text-muted-foreground text-xs">
                    {issue.fingerprint.slice(0, 12)}
                  </code>
                </div>
                <p className="text-muted-foreground truncate text-xs">{issue.exception_type}</p>
                <p className="truncate text-xs">{issue.event_summary}</p>
                <div className="text-muted-foreground text-xs">
                  {issue.occurrence_count} occurrence{issue.occurrence_count !== 1 ? "s" : ""} across{" "}
                  {issue.patrol_count} patrol{issue.patrol_count !== 1 ? "s" : ""} · last seen{" "}
                  {formatRelative(issue.last_seen)}
                </div>
                {issue.healing_attempt_id && (
                  <div className="text-xs">
                    <Link
                      to={`/qa/investigations/${issue.healing_attempt_id}`}
                      className="text-primary underline-offset-4 hover:underline"
                    >
                      View investigation
                    </Link>
                  </div>
                )}
              </div>
              <div className="flex shrink-0 gap-1">
                {dismissed ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={dismissingFp === issue.fingerprint}
                    onClick={() => handleUndismiss(issue)}
                  >
                    Restore
                  </Button>
                ) : (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={dismissingFp === issue.fingerprint}
                    onClick={() => handleDismiss(issue)}
                  >
                    Dismiss
                  </Button>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// QaOverviewPage
// ---------------------------------------------------------------------------

const PAGE_SIZE = 20;

export default function QaOverviewPage() {
  const [page, setPage] = useState(0);
  const { data: summaryResponse, isLoading: summaryLoading, isError: summaryError } = useQaSummary();
  const { data: patrolsResponse, isLoading: patrolsLoading, isError: patrolsError } = useQaPatrols({
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  });

  const summary = summaryResponse?.data;
  const patrols = patrolsResponse?.data ?? [];
  const patrolsMeta = patrolsResponse?.meta;
  const totalPatrols = patrolsMeta?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalPatrols / PAGE_SIZE));

  return (
    <div className="space-y-6">
      {/* Page heading */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">QA Staffer</h1>
        <p className="text-muted-foreground mt-1">
          System-wide quality patrol, investigation pipeline, and known issue tracking.
        </p>
      </div>

      {/* Summary stats */}
      {summaryLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <Card key={i}>
              <CardHeader className="pb-2">
                <Skeleton className="h-4 w-24" />
              </CardHeader>
              <CardContent>
                <Skeleton className="h-8 w-16" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : summaryError ? (
        <Card>
          <CardContent className="py-8 text-center">
            <p className="text-destructive text-sm">Failed to load QA summary.</p>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* Last patrol banner */}
          {summary?.last_patrol && (
            <Card>
              <CardContent className="pt-4">
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <div className="space-y-1">
                    <p className="text-muted-foreground text-sm font-medium">Last patrol</p>
                    <div className="flex items-center gap-2">
                      <PatrolStatusBadge status={summary.last_patrol.status} />
                      <span className="text-sm">
                        {formatTs(summary.last_patrol.started_at)}
                      </span>
                      <span className="text-muted-foreground text-xs">
                        ({formatRelative(summary.last_patrol.started_at)})
                      </span>
                    </div>
                    {summary.last_patrol.sources_polled.length > 0 && (
                      <div className="flex items-center gap-1">
                        <span className="text-muted-foreground text-xs">Sources:</span>
                        {summary.last_patrol.sources_polled.map((s) => (
                          <SourceTypeBadge key={s} sourceType={s} />
                        ))}
                      </div>
                    )}
                  </div>
                  <Button variant="outline" size="sm" asChild>
                    <Link to={`/qa/patrols/${summary.last_patrol.id}`}>View patrol</Link>
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}

          {!summary?.last_patrol && !summaryLoading && (
            <Card>
              <CardContent className="py-8 text-center">
                <p className="text-muted-foreground text-sm">
                  No patrol cycles recorded. The QA staffer may not be running yet.
                </p>
              </CardContent>
            </Card>
          )}

          {/* 24h stats */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatsCard
              title="Patrols (24h)"
              value={summary?.stats_24h.patrols_completed ?? 0}
            />
            <StatsCard
              title="Findings (24h)"
              value={summary?.stats_24h.total_findings ?? 0}
              description={`${summary?.stats_24h.novel_findings ?? 0} novel`}
            />
            <StatsCard
              title="Dispatched (24h)"
              value={summary?.stats_24h.dispatched_investigations ?? 0}
            />
            <StatsCard
              title="All-time patrols"
              value={summary?.stats_all_time.total_patrols ?? 0}
              description={`${summary?.stats_all_time.dispatched_investigations ?? 0} investigations`}
            />
          </div>
        </>
      )}

      {/* Known issues panel */}
      <Card>
        <CardHeader>
          <CardTitle>Known Issues</CardTitle>
          <CardDescription>
            Active issues tracked across patrol cycles. Dismiss to suppress future investigations.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <KnownIssuesPanel />
        </CardContent>
      </Card>

      {/* Recent patrols table */}
      <Card>
        <CardHeader>
          <CardTitle>Recent Patrols</CardTitle>
          <CardDescription>Patrol cycle history with findings and dispatch counts.</CardDescription>
          <CardAction>
            {summary?.active_sources && summary.active_sources.length > 0 && (
              <div className="flex items-center gap-1 text-xs text-muted-foreground">
                Active sources:
                {summary.active_sources.map((s) => (
                  <SourceTypeBadge key={s} sourceType={s} />
                ))}
              </div>
            )}
          </CardAction>
        </CardHeader>
        <CardContent>
          {patrolsLoading ? (
            <div className="space-y-2">
              {[1, 2, 3, 4, 5].map((i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : patrolsError ? (
            <p className="text-destructive py-4 text-center text-sm">Failed to load patrols.</p>
          ) : (
            <RecentPatrolsTable patrols={patrols} />
          )}
        </CardContent>
      </Card>

      {/* Pagination */}
      {totalPatrols > PAGE_SIZE && (
        <div className="flex items-center justify-between">
          <p className="text-muted-foreground text-sm">
            Page {page + 1} of {totalPages}
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!patrolsMeta?.has_more}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
