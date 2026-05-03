import { Link, useParams } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import { Time } from "@/components/ui/time";
import {
  Card,
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
import { useQaPatrol } from "@/hooks/use-qa";
import type { QaFindingRecord } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDuration(start: string, end: string | null | undefined): string {
  if (!end) return "running...";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 0) return "--";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
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
// Dedup reason badge
// ---------------------------------------------------------------------------

function DedupBadge({ reason }: { reason: string | null }) {
  if (!reason) {
    return <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">novel</Badge>;
  }
  const labels: Record<string, string> = {
    active_attempt: "active attempt",
    open_pr: "open PR",
    dismissed: "dismissed",
    cooldown: "cooldown",
  };
  return <Badge variant="secondary">{labels[reason] ?? reason}</Badge>;
}

// ---------------------------------------------------------------------------
// Findings table
// ---------------------------------------------------------------------------

function FindingsTable({ findings }: { findings: QaFindingRecord[] }) {
  if (findings.length === 0) {
    return (
      <div className="text-muted-foreground py-8 text-center text-sm">
        No findings in this patrol.
      </div>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Severity</TableHead>
          <TableHead>Source</TableHead>
          <TableHead>Butler</TableHead>
          <TableHead>Exception</TableHead>
          <TableHead className="max-w-xs">Summary</TableHead>
          <TableHead className="text-right">Count</TableHead>
          <TableHead>Dedup</TableHead>
          <TableHead>Investigation</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {findings.map((f) => (
          <TableRow key={f.id}>
            <TableCell>
              <SeverityBadge severity={f.severity} />
            </TableCell>
            <TableCell>
              <SourceTypeBadge sourceType={f.source_type} />
            </TableCell>
            <TableCell>
              <Badge variant="outline" className="font-mono text-xs">
                {f.source_butler}
              </Badge>
            </TableCell>
            <TableCell>
              <code className="text-xs">{f.exception_type}</code>
            </TableCell>
            <TableCell className="max-w-xs">
              <p className="truncate text-xs" title={f.event_summary}>
                {f.event_summary}
              </p>
            </TableCell>
            <TableCell className="text-right">{f.occurrence_count}</TableCell>
            <TableCell>
              <DedupBadge reason={f.dedup_reason} />
            </TableCell>
            <TableCell>
              {f.healing_attempt_id ? (
                <Link
                  to={`/qa/investigations/${f.healing_attempt_id}`}
                  className="text-primary text-xs underline-offset-4 hover:underline"
                >
                  {f.healing_attempt_id.slice(0, 8)}
                </Link>
              ) : (
                <span className="text-muted-foreground text-xs">—</span>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function PageSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-8 w-64" />
      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-32" />
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4" />
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// QaPatrolDetailPage
// ---------------------------------------------------------------------------

export default function QaPatrolDetailPage() {
  const { patrolId = "" } = useParams<{ patrolId: string }>();
  const { data: response, isLoading, isError } = useQaPatrol(patrolId || undefined);
  const patrol = response?.data;

  if (!patrolId) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold tracking-tight">Patrol Detail</h1>
        <Card>
          <CardContent>
            <p className="text-muted-foreground py-12 text-center text-sm">
              No patrol ID provided.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (isLoading) return <PageSkeleton />;

  if (isError || !patrol) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Button variant="outline" size="sm" asChild>
            <Link to="/qa">Back to QA</Link>
          </Button>
          <h1 className="text-2xl font-bold tracking-tight">Patrol Detail</h1>
        </div>
        <Card>
          <CardContent>
            <p className="text-destructive py-12 text-center text-sm">
              Patrol not found or failed to load.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const novelFindings = patrol.findings.filter((f) => !f.dedup_reason);
  const dispatched = patrol.findings.filter((f) => f.healing_attempt_id);

  return (
    <div className="space-y-6">
      {/* Breadcrumbs + header */}
      <Breadcrumbs
        items={[
          { label: "QA", href: "/qa" },
          { label: `Patrol ${patrol.id.slice(0, 8)}` },
        ]}
      />
      <div className="flex items-center gap-4">
        <h1 className="text-2xl font-bold tracking-tight">Patrol Detail</h1>
        <PatrolStatusBadge status={patrol.status} />
      </div>

      {/* Metadata card */}
      <Card>
        <CardHeader>
          <CardTitle>Metadata</CardTitle>
          <CardDescription>
            <code className="text-xs">{patrol.id}</code>
          </CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-3 text-sm">
            <dt className="text-muted-foreground font-medium">Started</dt>
            <dd><Time value={patrol.started_at} mode="absolute" /></dd>

            <dt className="text-muted-foreground font-medium">Completed</dt>
            <dd>{patrol.completed_at ? <Time value={patrol.completed_at} mode="absolute" /> : "--"}</dd>

            <dt className="text-muted-foreground font-medium">Duration</dt>
            <dd>{formatDuration(patrol.started_at, patrol.completed_at)}</dd>

            <dt className="text-muted-foreground font-medium">Lookback</dt>
            <dd>{patrol.log_lookback_minutes} minutes</dd>

            <dt className="text-muted-foreground font-medium">Sources polled</dt>
            <dd className="flex flex-wrap gap-1">
              {patrol.sources_polled.map((s) => (
                <SourceTypeBadge key={s} sourceType={s} />
              ))}
            </dd>

            <dt className="text-muted-foreground font-medium">Total findings</dt>
            <dd>{patrol.findings_count}</dd>

            <dt className="text-muted-foreground font-medium">Novel findings</dt>
            <dd>{patrol.novel_count}</dd>

            <dt className="text-muted-foreground font-medium">Dispatched</dt>
            <dd>{patrol.dispatched_count}</dd>

            {patrol.error_detail && (
              <>
                <dt className="text-muted-foreground font-medium">Error</dt>
                <dd className="text-destructive text-xs">{patrol.error_detail}</dd>
              </>
            )}
          </dl>
        </CardContent>
      </Card>

      {/* Dispatch summary */}
      {dispatched.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Dispatched Investigations</CardTitle>
            <CardDescription>
              {dispatched.length} finding{dispatched.length !== 1 ? "s" : ""} triggered an
              investigation in this patrol.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {dispatched.map((f) => (
                <div key={f.id} className="flex items-center justify-between rounded-md border p-2 text-sm">
                  <div className="flex items-center gap-2">
                    <SeverityBadge severity={f.severity} />
                    <code className="text-xs">{f.exception_type}</code>
                    <span className="text-muted-foreground text-xs truncate max-w-64">
                      {f.event_summary}
                    </span>
                  </div>
                  {f.healing_attempt_id && (
                    <Link
                      to={`/qa/investigations/${f.healing_attempt_id}`}
                      className="text-primary text-xs underline-offset-4 hover:underline"
                    >
                      View investigation
                    </Link>
                  )}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* All findings table */}
      <Card>
        <CardHeader>
          <CardTitle>
            All Findings ({patrol.findings_count})
          </CardTitle>
          <CardDescription>
            {novelFindings.length} novel · {patrol.findings_count - novelFindings.length} deduplicated
          </CardDescription>
        </CardHeader>
        <CardContent>
          <FindingsTable findings={patrol.findings} />
        </CardContent>
      </Card>
    </div>
  );
}
